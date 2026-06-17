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
REQUESTS_PER_MINUTE = 8  # per container; reserved concurrency bounds containers
ANSWER_CACHE_SIZE = 256  # per container; answers are deterministic (temperature 0)

_INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
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


def _ask(event: dict) -> dict:
    try:
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode("utf-8")
        question = json.loads(body).get("question")
    except (ValueError, AttributeError):
        question = None
    if not isinstance(question, str) or not question.strip():
        return _json(400, {"error": "Send JSON like {\"question\": \"...\"}."})
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        return _json(
            400, {"error": f"Please keep questions under {MAX_QUESTION_CHARS} characters."}
        )

    # Cache hits cost no model call, so they bypass the per-minute budget (which
    # exists to bound Bedrock spend) but are still logged.
    key = question.casefold()
    cached = _cache_get(key)
    if cached is not None:
        print(json.dumps({"kind": cached["kind"], "language": cached["language"],
                          "question_chars": len(question), "cache": "hit"}))
        return _json(200, cached)

    if _over_budget(time.monotonic()):
        return _json(429, {"error": "Too many requests right now. Please try again in a minute."})

    cfg = _make_cfg()
    started = time.monotonic()
    result = answer_question(
        question,
        model=get_model(cfg.models.provider, cfg.models.answer_model),
        cfg=cfg,
    )
    payload = {
        "answer": result.answer,
        "kind": result.kind,
        "language": guards.detect_language(result.answer),
        "as_of_date": result.as_of_date,
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
                "duration_ms": round(1000 * (time.monotonic() - started)),
                "cache": "miss",
            }
        )
    )
    return _json(200, payload)


def handler(event: dict, context: object = None) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath", "/")

    if path == "/" and method == "GET":
        return _response(200, _INDEX_HTML, "text/html; charset=utf-8")
    if path == "/api/ask":
        if method != "POST":
            return _json(405, {"error": "Use POST."})
        try:
            return _ask(event)
        except Exception as exc:  # never leak internals; never log content
            print(json.dumps({"error": type(exc).__name__}))
            return _json(500, {"error": "Something went wrong on our side. Please try again."})
    return _json(404, {"error": "Not found."})
