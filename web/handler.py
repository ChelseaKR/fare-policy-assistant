"""AWS Lambda handler: the static page plus the demo's single API route.

    GET  /          → web/index.html
    POST /api/ask   → {"question": "..."} → answer JSON with citations

Privacy: rider questions are answered and discarded. Nothing a rider types is
logged or stored; request logs carry only the response kind, language, and
timing so abuse stays visible without keeping content (see ADR 0004).

Cost guards, in order: the API Gateway stage throttle (set by
infra/deploy.sh, derived from its reserved-concurrency value) is the true
cross-container rate limit -- it is enforced before any container runs, so it
holds identically across cold starts and concurrent containers. Lambda
reserved concurrency is the hard ceiling on parallelism. The per-container
request budget in this module (`_over_budget`) is a fast, in-process backstop
on top of those two: cheap defense in depth within one warm container, not
itself a cross-container guarantee -- it resets on cold start and is
invisible to sibling containers (see ADR 0004 amendment, "a true
cross-container rate limit"). Then a 500-character question cap, and the
pinned 1024-token answer ceiling in config.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from collections import OrderedDict, deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # bundle root mirrors the repo root
sys.path.insert(0, str(_ROOT / "src"))

from assistant import config, guards  # noqa: E402
from assistant.answer import answer_question  # noqa: E402
from assistant.contract import build_structured_answer  # noqa: E402
from assistant.models import get_model  # noqa: E402
from assistant.retrieve import default_retriever  # noqa: E402
from web.csp import html_csp  # noqa: E402

MAX_QUESTION_CHARS = 500
# Reject oversized request bodies before json.loads parses them. A question (500
# chars) plus three truncated history turns is a few KB; 16 KB is comfortable
# headroom and well under the API Gateway 10 MB ceiling.
MAX_BODY_BYTES = 16 * 1024
REQUESTS_PER_MINUTE = 8  # per container, in-process backstop; the gateway
# throttle (infra/deploy.sh) is the cross-container ceiling -- see module
# docstring and ADR 0004 amendment "a true cross-container rate limit".
ANSWER_CACHE_SIZE = 256  # per container; answers are deterministic (temperature 0)
MAX_HISTORY_TURNS = 3  # prior turns the client may send for a follow-up
MAX_HISTORY_ANSWER_CHARS = 1200  # truncate prior answers kept as context

# Optional forged-history hardening. The client holds the conversation and sends
# prior turns back with a follow-up; by default any well-formed turn is accepted
# as context (see SECURITY.md — this is not a trust boundary, the output guard
# still polices every answer). A deployment that wants history restricted to
# turns this server actually issued sets FPA_HISTORY_HMAC_KEY: the /api/ask
# response then carries an HMAC over (question, answer), and _parse_history drops
# any turn whose signature does not verify. Read at call time so tests (and a
# key rotation) take effect without a container restart. Default "" = off.
_HISTORY_HMAC_KEY = os.environ.get("FPA_HISTORY_HMAC_KEY", "")


def _history_hmac_key() -> str:
    """The signing key, re-read from the environment on every call so tests can
    monkeypatch it and an operator can rotate it without redeploying."""
    return os.environ.get("FPA_HISTORY_HMAC_KEY", _HISTORY_HMAC_KEY)


def _sign_turn(q: str, a: str) -> str:
    """HMAC-SHA256 over a server-issued turn. The question is length-prefixed so
    the `|` delimiter cannot be shifted between the two fields (a plain `q|a`
    join would collide for `("x|y", "z")` and `("x", "y|z")`)."""
    key = _history_hmac_key()
    return hmac.new(key.encode(), f"{len(q)}:{q}|{a}".encode(), hashlib.sha256).hexdigest()


_INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
# The index page's CSP, computed once from its own markup: the inline <style> and
# <script> blocks are allowed by sha256 hash, not 'unsafe-inline' (see web/csp.py).
_INDEX_CSP = html_csp(_INDEX_HTML)
# Rendered once per container from the committed corpus; it changes only when the
# corpus does, which means a new deploy.
_OFFLINE_HTML: str | None = None


def _offline_html() -> str:
    global _OFFLINE_HTML
    if _OFFLINE_HTML is None:
        from assistant.ingest import load_chunks
        from web.offline import render_offline_reference

        _OFFLINE_HTML = render_offline_reference(load_chunks())
    return _OFFLINE_HTML


# Corpus identity, computed once per container.
_CORPUS_SUMMARY: dict | None = None


def _corpus_summary() -> dict:
    global _CORPUS_SUMMARY
    if _CORPUS_SUMMARY is None:
        from assistant.corpus import corpus_summary

        _CORPUS_SUMMARY = corpus_summary()
    return _CORPUS_SUMMARY


def _known_versions() -> list[str]:
    """Retained corpus versions (EXP-05), most recent first, capped so the
    payload stays small. `corpus/versions/` is a dev/provenance artifact, not
    part of the deploy bundle (infra/deploy.sh ships only the pinned
    `corpus/processed/chunks.jsonl`), so this is normally empty in a real
    deployment and populated only when the handler runs against a full
    checkout (e.g. local dev, `make offline`-style tooling)."""
    from assistant.corpus import list_versions

    return list(reversed(list_versions()))[:10]


def _version_payload() -> dict:
    """The corpus a deployment is actually serving, plus whether it matches the
    version an operator approved (FPA_PINNED_CORPUS_VERSION). The mismatch is a
    signal, not an error: the corpus is whatever was deployed, and this surfaces
    when that differs from what was approved. `known_versions` is provenance
    only — the serving path always stays pinned to the one corpus above; there
    is no time-travel answering for riders."""
    summary = dict(_corpus_summary())
    # Staleness budget: how old the freshest cited snapshot is, against a
    # configurable budget (FPA_STALENESS_BUDGET_DAYS, default 90). The UI already
    # shows the "as of" age; this surfaces the same signal as a machine-readable
    # over-budget flag for operators and the freshness automation.
    as_of = summary.get("as_of")
    if as_of:
        from datetime import UTC, date, datetime

        budget = int(os.environ.get("FPA_STALENESS_BUDGET_DAYS", "90"))
        age = (datetime.now(UTC).date() - date.fromisoformat(as_of)).days
        summary["staleness_days"] = age
        summary["staleness_budget_days"] = budget
        summary["stale"] = age > budget

    summary["known_versions"] = _known_versions()
    pinned = os.environ.get("FPA_PINNED_CORPUS_VERSION")
    if pinned:
        summary["pinned"] = pinned
        summary["matches_pin"] = pinned == summary["corpus_version"]
        if not summary["matches_pin"]:
            print(
                json.dumps(
                    {
                        "warning": "corpus_version_mismatch",
                        "serving": summary["corpus_version"],
                        "pinned": pinned,
                    }
                )
            )
    return summary


_RECENT: deque[float] = deque()
# Per-container answer cache: identical questions return the recorded payload
# without a model call, since the corpus is fixed and the model runs at
# temperature 0. Stores only the response payload (no rider content beyond the
# question key, which lives in memory and dies with the container). Bounded LRU.
_ANSWER_CACHE: OrderedDict[str, dict] = OrderedDict()


def _cache_get(key: str) -> dict | None:
    payload = _ANSWER_CACHE.get(key)
    if payload is not None:
        _ANSWER_CACHE.move_to_end(key)
    return payload


def _cache_put(key: str, payload: dict) -> None:
    _ANSWER_CACHE[key] = payload
    _ANSWER_CACHE.move_to_end(key)
    while len(_ANSWER_CACHE) > ANSWER_CACHE_SIZE:
        _ANSWER_CACHE.popitem(last=False)


# Build the BM25 index once per container, not per request.
default_retriever()

# Baseline for JSON/API responses: nothing loads, nothing frames. HTML routes
# override content-security-policy with a per-page policy that hashes their inline
# blocks (see _html_response); JSON responses carry no scripts or styles, so the
# blanket default-src 'none' is all they need. No 'unsafe-inline' anywhere.
_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'none'; connect-src 'self'; form-action 'self'; base-uri 'none'"
    ),
}


def _html_response(body: str, csp: str, *, frameable: bool = False) -> dict:
    """An HTML response carrying the baseline security headers with a per-page
    CSP. The CSP hashes the page's own inline <style>/<script> blocks, so it can
    never drift from the served markup. Framed routes (the embed) drop the
    x-frame-options DENY and instead scope framing via frame-ancestors in the CSP.
    """
    headers = dict(_SECURITY_HEADERS)
    if frameable:
        headers.pop("x-frame-options", None)
    headers["content-security-policy"] = csp
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/html; charset=utf-8", **headers},
        "body": body,
    }


def _embed_response(body: str) -> dict:
    """The embed widget is the one route allowed to be framed. It drops the
    x-frame-options DENY of every other response and instead names allowed
    ancestors in CSP. The allowlist is read at call time from
    FPA_EMBED_ANCESTORS (space-separated origins) and defaults to 'self', so out
    of the box the widget is frameable only from this origin. A deployment that
    wants agencies to embed it sets FPA_EMBED_ANCESTORS to their origins. Nothing
    else in the security posture changes: no store, nosniff, no referrer, and the
    same default-src 'none' base with the widget's inline blocks hashed in.
    """
    ancestors = os.environ.get("FPA_EMBED_ANCESTORS", "'self'")
    return _html_response(body, html_csp(body, frame_ancestors=ancestors), frameable=True)


def _make_cfg() -> config.Config:
    """Read the provider at call time so tests can run the handler offline."""
    provider = os.environ.get("FPA_PROVIDER", "bedrock")
    if provider == "mock":
        return config.Config(
            models=config.ModelConfig(provider="mock", answer_model="mock", judge_model="mock")
        )
    return config.Config()


def _response(status: int, body: str, content_type: str = "application/json") -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": content_type, **_SECURITY_HEADERS},
        "body": body,
    }


def _json(status: int, payload: dict) -> dict:
    return _response(status, json.dumps(payload, ensure_ascii=False))


def _over_budget(now: float) -> bool:
    """Per-container sliding-window backstop (defense in depth only).

    This is intentionally *not* the cross-container rate limit: it lives in
    this container's memory, so it resets on cold start and a burst spread
    across several warm containers is invisible to it. The real, cross-
    container ceiling is the API Gateway stage throttle configured in
    infra/deploy.sh, which is enforced before a request ever reaches a
    container. This function exists only to stop one warm container from
    running away with Bedrock spend between gateway-throttle windows.
    """
    while _RECENT and now - _RECENT[0] > 60.0:
        _RECENT.popleft()
    if len(_RECENT) >= REQUESTS_PER_MINUTE:
        return True
    _RECENT.append(now)
    return False


def _parse_history(raw: object) -> list[tuple[str, str]]:
    """Validate and bound client-supplied prior turns.

    The client holds the conversation; the server keeps nothing. Each turn is a
    {"q", "a"} pair of strings. We keep only the last MAX_HISTORY_TURNS, truncate
    each field, and drop anything malformed — context, not a trust boundary (the
    output guard still polices every new answer regardless of history).

    When FPA_HISTORY_HMAC_KEY is set, history is additionally restricted to turns
    this server issued: each turn must carry a `"sig"` string that verifies (in
    constant time) against the raw q/a as sent. Verification happens before the
    strip/truncate below, since the signature covers the exact strings the /ask
    response returned.
    """
    if not isinstance(raw, list):
        return []
    key = _history_hmac_key()
    turns: list[tuple[str, str]] = []
    for item in raw[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        q, a = item.get("q"), item.get("a")
        if not (isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip()):
            continue
        if key:
            sig = item.get("sig")
            if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign_turn(q, a)):
                continue  # unsigned or tampered — not a turn this server issued
        turns.append((q.strip()[:MAX_QUESTION_CHARS], a.strip()[:MAX_HISTORY_ANSWER_CHARS]))
    return turns


def _ask(event: dict) -> dict:
    try:
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            return _json(413, {"error": "Request too large."})
        data = json.loads(body)
        question = data.get("question")
    except (ValueError, AttributeError):
        question, data = None, {}
    if not isinstance(question, str) or not question.strip():
        return _json(400, {"error": 'Send JSON like {"question": "..."}.'})
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        return _json(
            400, {"error": f"Please keep questions under {MAX_QUESTION_CHARS} characters."}
        )
    history = _parse_history(data.get("history"))

    # Cache hits cost no model call, so they bypass the per-minute budget (which
    # exists to bound Bedrock spend) but are still logged. The key includes the
    # history, since a follow-up's answer depends on the turns before it.
    key = question.casefold() + "".join(f"|{q}>{a}" for q, a in history)
    cached = _cache_get(key)
    if cached is not None:
        print(
            json.dumps(
                {
                    "kind": cached["kind"],
                    "language": cached["language"],
                    "question_chars": len(question),
                    "turns": len(history),
                    "cache": "hit",
                }
            )
        )
        return _json(200, cached)

    if _over_budget(time.monotonic()):
        return _json(429, {"error": "Too many requests right now. Please try again in a minute."})

    cfg = _make_cfg()
    started = time.monotonic()
    result = answer_question(
        question,
        history=history or None,
        model=get_model(cfg.models.provider, cfg.models.answer_model),
        cfg=cfg,
    )
    # EXP-04 (docs/ideation/03-expansions.md): the typed contract alongside
    # the existing prose `answer`. `structured` is additive — every prior
    # field stays, so existing clients are unaffected — and is null when the
    # deterministic parse fails schema validation; the UI falls back to
    # rendering `answer` as prose in that case (never hidden, always logged
    # below as structured_ok).
    structured = build_structured_answer(result)
    response_language, language_confidence, language_uncertain = guards.detect_language_confident(
        result.answer
    )
    payload = {
        "answer": result.answer,
        "kind": result.kind,
        # Report the classifier's actual top language. Input safety still uses
        # the conservative English fallback when detection is uncertain; output
        # metadata exposes that uncertainty rather than silently relabeling a
        # Taglish answer as English.
        "language": response_language,
        "language_confidence": round(language_confidence, 3),
        "language_uncertain": language_uncertain,
        "as_of_date": result.as_of_date,
        # Operational confidence band for integrators and staff; never alters
        # the answer or the guards (persona research F-16).
        "confidence": result.confidence,
        # The corpus snapshot this answer came from, so a client can tie an
        # answer to an approved corpus version (persona research R2-6).
        "corpus_version": _corpus_summary()["corpus_version"],
        "citations": [
            {"agency": c.agency, "title": c.title, "url": c.url, "fetch_date": c.fetch_date}
            for c in result.citations
        ],
        "structured": structured.to_json_dict() if structured.structured_ok else None,
    }
    # Forged-history hardening: when a key is set, sign the turn so the client can
    # echo the signature back with its next follow-up and _parse_history can
    # confirm this turn was server-issued. Off by default (empty key → no field).
    if _history_hmac_key():
        payload["sig"] = _sign_turn(question, result.answer)
    _cache_put(key, payload)
    # Operational log only: no question text, no answer text (ADR 0004).
    # structured_ok/reason reference schema field paths, not rider content.
    print(
        json.dumps(
            {
                "kind": result.kind,
                "language": payload["language"],
                "question_chars": len(question),
                "turns": len(history),
                "duration_ms": round(1000 * (time.monotonic() - started)),
                "cache": "miss",
                "structured_ok": structured.structured_ok,
                "structured_fallback_reason": structured.fallback_reason or None,
            }
        )
    )
    return _json(200, payload)


def _feedback(event: dict) -> dict:
    """Record a thumbs up/down. Logs only the verdict, the response kind, and the
    language — never the question or answer. Nothing identifies the rider; the
    aggregate is queryable in CloudWatch without storing any content."""
    try:
        data = json.loads(event.get("body") or "")
        verdict = data.get("verdict")
    except (ValueError, AttributeError):
        verdict = None
    if verdict not in ("up", "down"):
        return _json(400, {"error": 'Send {"verdict": "up" | "down"}.'})
    kind = data.get("kind")
    language = data.get("language")
    print(
        json.dumps(
            {
                "feedback": verdict,
                "kind": kind if isinstance(kind, str) else None,
                "language": language if isinstance(language, str) else None,
            }
        )
    )
    return _json(200, {"ok": True})


def handler(event: dict, context: object = None) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath", "/")

    if path == "/" and method == "GET":
        return _html_response(_INDEX_HTML, _INDEX_CSP)
    if path == "/offline" and method == "GET":
        body = _offline_html()
        return _html_response(body, html_csp(body))
    if path == "/embed" and method == "GET":
        from web.embed import EMBED_HTML

        return _embed_response(EMBED_HTML)
    if path == "/version" and method == "GET":
        return _json(200, _version_payload())
    if path in ("/api/ask", "/api/feedback"):
        if method != "POST":
            return _json(405, {"error": "Use POST."})
        try:
            return _ask(event) if path == "/api/ask" else _feedback(event)
        except Exception as exc:  # never leak internals; never log content
            print(json.dumps({"error": type(exc).__name__}))
            return _json(500, {"error": "Something went wrong on our side. Please try again."})
    return _json(404, {"error": "Not found."})
