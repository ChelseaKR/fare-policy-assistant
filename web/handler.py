"""AWS Lambda handler: the static page plus the demo's single API route.

    GET  /          → web/index.html
    POST /api/ask   → {"question": "..."} → answer JSON with citations

Privacy: plaintext rider questions are not logged or retained in the server
cache. Successful requests are processed in memory and sent to the configured
model; the bounded answer cache uses a process-local keyed digest rather than
plaintext question/history. Request logs carry only response kind, language,
length, timing, and operational flags (see ADR 0004 and docs/dpia.md).

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

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from collections import OrderedDict, deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # bundle root mirrors the repo root
sys.path.insert(0, str(_ROOT / "src"))

from assistant import config, guards, release_identity, telemetry  # noqa: E402
from assistant.answer import AnswerResult, answer_question  # noqa: E402
from assistant.contract import build_structured_answer  # noqa: E402
from assistant.models import get_model  # noqa: E402
from assistant.retrieve import default_retriever  # noqa: E402
from web.csp import html_csp  # noqa: E402

MAX_QUESTION_CHARS = config.MAX_QUESTION_CHARS
# Reject oversized request bodies before json.loads parses them. A question (500
# chars) plus three truncated history turns is a few KB; 16 KB is comfortable
# headroom and well under the API Gateway 10 MB ceiling.
MAX_BODY_BYTES = config.MAX_BODY_BYTES
REQUESTS_PER_MINUTE = config.REQUESTS_PER_MINUTE  # per container, in-process backstop; the gateway
# throttle (infra/deploy.sh) is the cross-container ceiling -- see module
# docstring and ADR 0004 amendment "a true cross-container rate limit".
ANSWER_CACHE_SIZE = config.ANSWER_CACHE_SIZE  # per container; temperature 0
MAX_HISTORY_TURNS = config.MAX_HISTORY_TURNS
MAX_HISTORY_ANSWER_CHARS = config.MAX_HISTORY_ANSWER_CHARS

# Optional forged-history hardening. The client holds the conversation and sends
# prior turns back with a follow-up; by default any well-formed turn is accepted
# as context (see SECURITY.md — this is not a trust boundary, the output guard
# still polices every answer). A deployment that wants history restricted to
# turns this server actually issued sets FPA_HISTORY_HMAC_KEY: the /api/ask
# response then carries an HMAC over (question, answer), and _parse_history drops
# any turn whose signature does not verify. Read at call time so tests (and a
# key rotation) take effect without a container restart. Default "" = off.
_HISTORY_HMAC_KEY = os.environ.get("FPA_HISTORY_HMAC_KEY", "")

# Deployment health checks use the ordinary paid /api/ask path, but must not
# accidentally reuse or populate a warm answer cache. Lambda direct invocation
# can add this top-level marker to the API Gateway-shaped event. A rider can put
# the same text in a request body or header, but neither location is consulted.
_DIRECT_HEALTH_FIELD = "fare_assistant_health"
_DIRECT_HEALTH_VALUE = "release-v1"


def _history_hmac_key() -> str:
    """The signing key, re-read from the environment on every call so tests can
    monkeypatch it and an operator can rotate it without redeploying."""
    return os.environ.get("FPA_HISTORY_HMAC_KEY", _HISTORY_HMAC_KEY)


def _sign_turn(q: str, a: str) -> str:
    """HMAC-SHA256 over a server-issued turn and its evidence-policy state.

    Binding the complete release means a turn signed before any source,
    configuration, prompt, containment, or evidence change cannot be replayed
    as current context afterward. JSON encoding keeps field boundaries
    unambiguous.
    """
    key = _history_hmac_key()
    material = json.dumps(
        [_behavior_version(), q, a],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key.encode(), material, hashlib.sha256).hexdigest()


_INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
# The index page's CSP, computed once from its own markup: the inline <style> and
# <script> blocks are allowed by sha256 hash, not 'unsafe-inline' (see web/csp.py).
_INDEX_CSP = html_csp(_INDEX_HTML)
# Rendered once per container from the committed corpus; it changes only when the
# corpus does, which means a new deploy.
_OFFLINE_HTML: tuple[tuple[str, ...], str] | None = None
_GUIDE_HTML: tuple[tuple[str, ...], str] | None = None


def _disabled_document_ids() -> tuple[str, ...]:
    """Normalized operator-disabled source IDs, stable for cache keys."""
    return tuple(
        sorted(
            {
                doc_id.strip()
                for doc_id in os.environ.get("FPA_DISABLED_DOC_IDS", "").split(",")
                if doc_id.strip()
            }
        )
    )


def _offline_html() -> str:
    global _OFFLINE_HTML
    disabled = _disabled_document_ids()
    if _OFFLINE_HTML is None or _OFFLINE_HTML[0] != disabled:
        from assistant.ingest import load_chunks
        from web.offline import render_offline_reference

        chunks = [chunk for chunk in load_chunks() if chunk.doc_id not in disabled]
        _OFFLINE_HTML = (
            disabled,
            render_offline_reference(
                chunks,
                full_corpus_version=_corpus_summary()["corpus_version"],
            ),
        )
    return _OFFLINE_HTML[1]


def _guide_html() -> str:
    global _GUIDE_HTML
    disabled = _disabled_document_ids()
    if _GUIDE_HTML is None or _GUIDE_HTML[0] != disabled:
        from assistant.ingest import load_chunks
        from web.guide import render_guide

        chunks = [chunk for chunk in load_chunks() if chunk.doc_id not in disabled]
        _GUIDE_HTML = (
            disabled,
            render_guide(
                chunks,
                full_corpus_version=_corpus_summary()["corpus_version"],
            ),
        )
    return _GUIDE_HTML[1]


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

        budget = int(
            os.environ.get(
                "FPA_STALENESS_BUDGET_DAYS",
                str(config.DEFAULT_STALENESS_BUDGET_DAYS),
            )
        )
        age = (datetime.now(UTC).date() - date.fromisoformat(as_of)).days
        summary["staleness_days"] = age
        summary["staleness_budget_days"] = budget
        summary["stale"] = age > budget

    summary["known_versions"] = _known_versions()
    summary["disabled_documents"] = list(_disabled_document_ids())
    pinned = os.environ.get("FPA_PINNED_CORPUS_VERSION")
    if pinned:
        summary["pinned"] = pinned
        summary["matches_pin"] = pinned == summary["corpus_version"]
        if not summary["matches_pin"]:
            telemetry.log_corpus_version_mismatch(
                serving=summary["corpus_version"],
                pinned=pinned,
            )
    try:
        summary.update(_release_status())
    except release_identity.ReleaseIdentityError:
        # The candidate gate needs a safe diagnostic, never the validation
        # exception or any environment material.
        summary.update(
            {
                "identity_status": "invalid",
                "source_revision": None,
                "config_version": None,
                "snapshot_version": None,
                "release_version": None,
                "artifact_code_sha256": None,
                "function_version": (
                    "local" if os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", "") == "" else None
                ),
            }
        )
    return summary


_RECENT: deque[float] = deque()
# Per-container answer cache: identical questions return the recorded payload
# without a model call, since the corpus is fixed and the model runs at
# temperature 0. Cache keys are process-local keyed HMAC digests, never plaintext
# questions or history. The random key is deliberately not configurable or
# persisted: a warm container can recognize its own repeated requests, while a
# cache snapshot or diagnostic cannot be used to guess a rider's question with
# an offline dictionary. The bounded LRU and its key both die with the container.
_ANSWER_CACHE: OrderedDict[str, dict] = OrderedDict()
_ANSWER_CACHE_HMAC_KEY = os.urandom(32)
_LOCAL_SNAPSHOT_VERSION: str | None = None

_RELEASE_ENV_KEYS = (
    "FPA_SOURCE_REVISION",
    "FPA_CONFIG_VERSION",
    "FPA_PINNED_CONTENT_VERSION",
    "FPA_PINNED_SNAPSHOT_VERSION",
    "FPA_RELEASE_VERSION",
    "FPA_ARTIFACT_CODE_SHA256",
)


def _function_version() -> str:
    value = os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", "")
    if value == "":
        return "local"
    if re.fullmatch(r"[1-9][0-9]*", value):
        return value
    raise release_identity.ReleaseIdentityError(
        "AWS_LAMBDA_FUNCTION_VERSION must be an immutable numeric release"
    )


def _artifact_code_sha256(*, required: bool) -> str | None:
    """Validate the AWS base64 SHA-256 without exposing any environment values."""
    value = os.environ.get("FPA_ARTIFACT_CODE_SHA256", "")
    if not value:
        if required:
            raise release_identity.ReleaseIdentityError(
                "artifact code identity is required for a numeric Lambda release"
            )
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise release_identity.ReleaseIdentityError(
            "artifact code identity is not valid base64"
        ) from exc
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        raise release_identity.ReleaseIdentityError(
            "artifact code identity is not an AWS-style SHA-256"
        )
    return value


def _local_snapshot_version() -> str:
    global _LOCAL_SNAPSHOT_VERSION
    if _LOCAL_SNAPSHOT_VERSION is None:
        _LOCAL_SNAPSHOT_VERSION = release_identity.resolve_current_snapshot().snapshot_version
    return _LOCAL_SNAPSHOT_VERSION


def _release_status() -> dict[str, object]:
    """Return a verified production tuple or an explicit local-development state."""
    numeric_lambda = _function_version() != "local"
    descriptor_present = config.RELEASE_DESCRIPTOR_PATH.is_file()
    identity_environment_present = any(os.environ.get(key) for key in _RELEASE_ENV_KEYS)

    if descriptor_present:
        descriptor = release_identity.load_release_descriptor()
        descriptor = release_identity.verify_release_descriptor(
            descriptor,
            require_environment=numeric_lambda or identity_environment_present,
        )
        artifact = _artifact_code_sha256(required=numeric_lambda)
        return {
            "identity_status": "verified",
            "source_revision": descriptor.source_revision,
            "config_version": descriptor.config_version,
            "content_version": descriptor.content_version,
            "snapshot_version": descriptor.snapshot_version,
            "release_version": descriptor.release_version,
            "artifact_code_sha256": artifact,
            "function_version": _function_version(),
        }

    if numeric_lambda or identity_environment_present:
        raise release_identity.ReleaseIdentityError(
            "an identity-bearing runtime is missing its bundled release descriptor"
        )

    local_config = release_identity.build_config_identity()
    return {
        "identity_status": "development",
        "source_revision": None,
        "config_version": local_config.config_version,
        "content_version": _corpus_summary()["content_version"],
        "snapshot_version": _local_snapshot_version(),
        "release_version": None,
        "artifact_code_sha256": None,
        "function_version": "local",
    }


def _behavior_version() -> str:
    """Identity boundary for cache entries and signed client-held history."""
    status = _release_status()
    release_version = status.get("release_version")
    if isinstance(release_version, str):
        return release_version
    return (
        "development:"
        f"{status['config_version']}:{status['content_version']}:{status['snapshot_version']}"
    )


def _cache_key(question: str, history: list[tuple[str, str]]) -> str:
    """Return an opaque digest bound to one complete application release."""
    material = json.dumps(
        [
            config.ANSWER_CACHE_KEY_SCHEMA,
            _behavior_version(),
            question.casefold(),
            history,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_ANSWER_CACHE_HMAC_KEY, material, hashlib.sha256).hexdigest()


def _cache_get(key: str) -> dict | None:
    payload = _ANSWER_CACHE.get(key)
    if payload is not None:
        _ANSWER_CACHE.move_to_end(key)
    return payload


def _cache_put(key: str, payload: dict) -> None:
    # A response signature belongs to the current history-signing key, which
    # operators may rotate without replacing a warm container. Cache only the
    # stable answer payload and sign each response at delivery time.
    cached = dict(payload)
    cached.pop("sig", None)
    _ANSWER_CACHE[key] = cached
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
    ancestors = os.environ.get("FPA_EMBED_ANCESTORS", config.DEFAULT_EMBED_ANCESTORS)
    return _html_response(body, html_csp(body, frame_ancestors=ancestors), frameable=True)


def _make_cfg() -> config.Config:
    """Read the provider at call time so tests can run the handler offline."""
    return config.Config.from_environment()


def _response(status: int, body: str, content_type: str = "application/json") -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": content_type, **_SECURITY_HEADERS},
        "body": body,
    }


def _json(status: int, payload: dict) -> dict:
    return _response(status, json.dumps(payload, ensure_ascii=False))


_IDENTITY_UNAVAILABLE = "This release could not verify its runtime identity. Please try later."


def _policy_identity_error() -> dict | None:
    """Fail closed before serving rider-facing policy or interactive surfaces."""
    try:
        _release_status()
    except release_identity.ReleaseIdentityError:
        return _json(503, {"error": _IDENTITY_UNAVAILABLE})
    return None


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


def _direct_health_bypass(event: dict) -> bool:
    """Recognize only the deployer's top-level direct-invocation marker."""
    return event.get(_DIRECT_HEALTH_FIELD) == _DIRECT_HEALTH_VALUE


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


def _request_input_check(question: str, raw_history: object) -> guards.InputCheck:
    """Guard current and prior rider questions before parsing history or cache.

    History is client-held and therefore untrusted even when turn signing is
    enabled only optionally. Checking each raw ``q`` first prevents PII,
    injection text, or another refused rider input from reaching retrieval, a
    model, or an answer-cache key. The ``a`` field is not input-guarded here:
    server answers can legitimately contain public agency phone numbers that
    resemble personal contact data. Output guards still police every new answer,
    and optional history signing authenticates prior answers. Only the last
    turns the request could actually use are inspected.
    """
    current = guards.check_input(question)
    if not current.ok or not isinstance(raw_history, list):
        return current

    for item in raw_history[-MAX_HISTORY_TURNS:]:
        if not isinstance(item, dict):
            continue
        value = item.get("q")
        if not isinstance(value, str):
            continue
        checked = guards.check_input(value)
        if not checked.ok:
            return checked
    return current


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
    try:
        release_status = _release_status()
    except release_identity.ReleaseIdentityError:
        return _json(503, {"error": _IDENTITY_UNAVAILABLE})
    started = time.monotonic()
    direct_health = _direct_health_bypass(event)
    raw_history = data.get("history")
    pre = _request_input_check(question, raw_history)
    history: list[tuple[str, str]] = []
    key: str | None = None

    if not pre.ok:
        # Build the same public result contract as answer_question's input-guard
        # path, but do so before history parsing, cache access, budget accounting,
        # retrieval, or model construction.
        result = AnswerResult(
            question=question,
            answer=pre.message or "",
            kind="refused_input",
            guard_flags=pre.flags,
        )
    else:
        history = _parse_history(raw_history)

        # Cache hits cost no model call, so they bypass the per-minute budget
        # (which exists to bound Bedrock spend) but are still logged. The HMAC
        # covers an unambiguous JSON encoding of both question and history.
        if not direct_health:
            key = _cache_key(question, history)
            cached = _cache_get(key)
            if cached is not None:
                # Return a copy so signing-key rotation cannot mutate the stable
                # cached payload or send a signature made with the previous key.
                cached = dict(cached)
                if _history_hmac_key():
                    cached["sig"] = _sign_turn(question, cached["answer"])
                else:
                    cached.pop("sig", None)
                telemetry.log_answer_request(
                    kind=cached["kind"],
                    language=cached["language"],
                    question_chars=len(question),
                    turns=len(history),
                    request_duration_ms=round(1000 * (time.monotonic() - started)),
                    cache="hit",
                    model_called=False,
                    structured_ok=cached["structured"] is not None,
                )
                return _json(200, cached)

        if not direct_health and _over_budget(time.monotonic()):
            telemetry.log_answer_request(
                kind="rate_limited",
                language=None,
                question_chars=len(question),
                turns=len(history),
                request_duration_ms=round(1000 * (time.monotonic() - started)),
                cache="miss",
                model_called=False,
                structured_ok=None,
                status_code=429,
            )
            return _json(
                429, {"error": "Too many requests right now. Please try again in a minute."}
            )

        cfg = _make_cfg()
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
        "content_version": release_status["content_version"],
        "snapshot_version": release_status["snapshot_version"],
        "config_version": release_status["config_version"],
        "release_version": release_status["release_version"],
        "source_revision": release_status["source_revision"],
        "identity_status": release_status["identity_status"],
        "citations": [
            {"agency": c.agency, "title": c.title, "url": c.url, "fetch_date": c.fetch_date}
            for c in result.citations
        ],
        "structured": structured.to_json_dict() if structured.structured_ok else None,
    }
    # Forged-history hardening: when a key is set, sign the turn so the client can
    # echo the signature back with its next follow-up and _parse_history can
    # confirm this turn was server-issued. Off by default (empty key → no field).
    if _history_hmac_key() and result.kind == "answered":
        payload["sig"] = _sign_turn(question, result.answer)
    # Never retain any guarded or refused response. Input-guard refusals bypass
    # cache access entirely above; this narrower allowlist also prevents output
    # guard and no-support results from entering the cache.
    if key is not None and result.kind == "answered":
        _cache_put(key, payload)
    # Operational record only: no question, answer, history, citation, or
    # schema error text. A guarded response still consumed a model call even
    # though it is intentionally not cacheable.
    telemetry.log_answer_request(
        kind=result.kind,
        language=payload["language"],
        question_chars=len(question),
        turns=len(history),
        request_duration_ms=round(1000 * (time.monotonic() - started)),
        cache="miss" if key is not None and result.kind == "answered" else "bypass",
        model_called=bool(result.model),
        structured_ok=structured.structured_ok,
        direct_health=direct_health,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
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
    telemetry.log_feedback(
        verdict=verdict,
        kind=kind
        if isinstance(kind, str)
        and kind in {"answered", "answered_guarded", "refused_input", "refused_no_support"}
        else None,
        language=language if isinstance(language, str) and language in {"en", "es", "tl"} else None,
    )
    return _json(200, {"ok": True})


def _handle_event(event: dict) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath", "/")

    if path == "/" and method == "GET":
        return _html_response(_INDEX_HTML, _INDEX_CSP)
    if path == "/offline" and method == "GET":
        if identity_error := _policy_identity_error():
            return identity_error
        body = _offline_html()
        return _html_response(body, html_csp(body))
    if path == "/guide" and method == "GET":
        if identity_error := _policy_identity_error():
            return identity_error
        body = _guide_html()
        return _html_response(body, html_csp(body))
    if path == "/embed" and method == "GET":
        if identity_error := _policy_identity_error():
            return identity_error
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
            telemetry.log_handler_error(
                route="api_ask" if path == "/api/ask" else "api_feedback",
                error_type=type(exc).__name__,
            )
            return _json(500, {"error": "Something went wrong on our side. Please try again."})
    return _json(404, {"error": "Not found."})


def handler(event: dict, context: object = None) -> dict:
    """Handle one Lambda invocation with runtime-owned anonymous correlation."""
    request_id = getattr(context, "aws_request_id", None)
    if not isinstance(request_id, str):
        request_id = None
    with telemetry.request_correlation(request_id):
        return _handle_event(event)
