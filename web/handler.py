"""AWS Lambda handler: the static page plus the demo's single API route.

    GET  /          → web/index.html
    POST /api/ask   → {"question": "..."} → answer JSON with citations

Privacy: rider questions are answered and discarded. Nothing a rider types is
logged or stored; request logs carry only the response kind, language, and
timing so abuse stays visible without keeping content (see ADR 0004).

Cost guards, in order: Lambda reserved concurrency (set by infra/deploy.sh),
a per-container request budget here, a 500-character question cap, and the
pinned 1024-token answer ceiling in config.
"""

from __future__ import annotations

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
from assistant.models import get_model  # noqa: E402
from assistant.retrieve import default_retriever  # noqa: E402

MAX_QUESTION_CHARS = 500
# Reject oversized request bodies before json.loads parses them. A question (500
# chars) plus three truncated history turns is a few KB; 16 KB is comfortable
# headroom and well under the API Gateway 10 MB ceiling.
MAX_BODY_BYTES = 16 * 1024
REQUESTS_PER_MINUTE = 8  # per container; reserved concurrency bounds containers
ANSWER_CACHE_SIZE = 256  # per container; answers are deterministic (temperature 0)
MAX_HISTORY_TURNS = 3  # prior turns the client may send for a follow-up
MAX_HISTORY_ANSWER_CHARS = 1200  # truncate prior answers kept as context

_INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
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

_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'"
    ),
}


def _embed_response(body: str) -> dict:
    """The embed widget is the one route allowed to be framed. It drops the
    x-frame-options DENY of every other response and instead names allowed
    ancestors in CSP. The allowlist is read at call time from
    FPA_EMBED_ANCESTORS (space-separated origins) and defaults to 'self', so out
    of the box the widget is frameable only from this origin. A deployment that
    wants agencies to embed it sets FPA_EMBED_ANCESTORS to their origins. Nothing
    else in the security posture changes: no store, nosniff, no referrer, and the
    same default-src 'none' base.
    """
    ancestors = os.environ.get("FPA_EMBED_ANCESTORS", "'self'")
    headers = {k: v for k, v in _SECURITY_HEADERS.items() if k != "x-frame-options"}
    headers["content-security-policy"] = (
        _SECURITY_HEADERS["content-security-policy"] + f"; frame-ancestors {ancestors}"
    )
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/html; charset=utf-8", **headers},
        "body": body,
    }


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
    """
    if not isinstance(raw, list):
        return []
    turns: list[tuple[str, str]] = []
    for item in raw[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        q, a = item.get("q"), item.get("a")
        if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip():
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
    payload = {
        "answer": result.answer,
        "kind": result.kind,
        "language": guards.detect_language(result.answer),
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
    }
    _cache_put(key, payload)
    # Operational log only: no question text, no answer text (ADR 0004).
    print(
        json.dumps(
            {
                "kind": result.kind,
                "language": payload["language"],
                "question_chars": len(question),
                "turns": len(history),
                "duration_ms": round(1000 * (time.monotonic() - started)),
                "cache": "miss",
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
        return _response(200, _INDEX_HTML, "text/html; charset=utf-8")
    if path == "/offline" and method == "GET":
        return _response(200, _offline_html(), "text/html; charset=utf-8")
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
