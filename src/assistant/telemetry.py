"""Privacy-safe structured runtime and GenAI telemetry.

All queryable fields are attached to :class:`logging.LogRecord` as ``extra``
attributes.  Messages are fixed event names, never JSON strings, and no helper
in this module accepts prompt, question, response, history, citation, request
header, or exception-message content.
"""

from __future__ import annotations

import contextvars
import logging
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from assistant._vendor.genai_telemetry.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    METRIC_OPERATION_DURATION,
    PORTFOLIO_COST_USD,
)

_LOG = logging.getLogger("fare_assistant")
SpanFactory = Callable[[str, dict[str, object]], AbstractContextManager[Any]]
_span_factory: SpanFactory | None = None
_aws_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fare_assistant_aws_request_id",
    default=None,
)


def function_version() -> str:
    """Return the immutable Lambda version, or a stable local-runtime label."""
    return os.environ.get("AWS_LAMBDA_FUNCTION_VERSION") or "local"


@contextmanager
def request_correlation(aws_request_id: str | None) -> Iterator[None]:
    """Correlate one invocation without accepting a client-supplied identifier."""
    value = aws_request_id if isinstance(aws_request_id, str) and aws_request_id else None
    token = _aws_request_id.set(value)
    try:
        yield
    finally:
        _aws_request_id.reset(token)


def _common_fields(event: str) -> dict[str, object]:
    return {
        "event": event,
        # Lambda's Python JSON formatter reserves/omits an ``aws_request_id``
        # extra key. Use a distinct name, then require it to equal Lambda's
        # built-in ``requestId`` during candidate verification.
        "runtime_request_id": _aws_request_id.get(),
        "function_version": function_version(),
    }


def _emit(level: int, event: str, fields: dict[str, object]) -> None:
    """Emit a fixed-message structured record without exception information."""
    _LOG.log(level, event, extra={**_common_fields(event), **fields})


def log_answer_request(
    *,
    kind: str,
    language: str | None,
    question_chars: int,
    turns: int,
    request_duration_ms: int,
    cache: str,
    model_called: bool,
    structured_ok: bool | None,
    status_code: int = 200,
    direct_health: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Record one terminal answer outcome using bounded, non-content fields."""
    _emit(
        logging.INFO,
        "answer_request",
        {
            "kind": kind,
            "language": language,
            "question_chars": question_chars,
            "turns": turns,
            "duration_ms": request_duration_ms,
            "request_duration_ms": request_duration_ms,
            "cache": cache,
            "model_called": model_called,
            "structured_ok": structured_ok,
            "status_code": status_code,
            "direct_health": direct_health,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "completion_recorded": model_called,
        },
    )


def log_feedback(*, verdict: str, kind: str | None, language: str | None) -> None:
    """Record a bounded feedback classification, never free-form client fields."""
    _emit(
        logging.INFO,
        "feedback",
        {
            "verdict": verdict,
            "kind": kind,
            "language": language,
        },
    )


def log_feedback_rate_limited() -> None:
    """Record that a feedback submission was refused by the per-container budget.

    Deliberately carries no fields beyond the common envelope: the request was
    rejected before its body was read, so there is no verdict, kind, or language
    to report, and there is nothing about the rider to report either. Operators
    get a countable signal; the record cannot describe who was refused.
    """
    _emit(logging.INFO, "feedback_rate_limited", {})


def log_handler_error(*, route: str, error_type: str) -> None:
    """Record only an exception class for an API handler failure."""
    _emit(
        logging.ERROR,
        "handler_error",
        {
            "route": route,
            "error_type": error_type,
        },
    )


def log_corpus_version_mismatch(*, serving: str, pinned: str) -> None:
    """Surface a non-sensitive deployment-integrity warning as structured data."""
    _emit(
        logging.WARNING,
        "corpus_version_mismatch",
        {
            "serving_corpus_version": serving,
            "pinned_corpus_version": pinned,
        },
    )


def set_span_factory(factory: SpanFactory | None) -> None:
    """Install an optional tracer adapter; ``None`` restores the no-op default."""
    global _span_factory
    _span_factory = factory


@dataclass
class GenAICall:
    attributes: dict[str, object] = field(default_factory=dict)
    error_type: str | None = None
    completion_recorded: bool = False

    def record_completion(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        counts = (
            input_tokens,
            output_tokens,
            cache_creation_input_tokens,
            cache_read_input_tokens,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("usage counts must be non-negative integers")
        if cache_creation_input_tokens + cache_read_input_tokens > input_tokens:
            raise ValueError("cache token buckets cannot exceed canonical input total")
        if cost_usd is not None and (
            isinstance(cost_usd, bool)
            or not isinstance(cost_usd, (int, float))
            or not math.isfinite(cost_usd)
            or cost_usd < 0
        ):
            raise ValueError("cost_usd must be finite and non-negative")
        self.attributes.update(
            {
                GEN_AI_RESPONSE_MODEL: model,
                GEN_AI_USAGE_INPUT_TOKENS: input_tokens,
                GEN_AI_USAGE_OUTPUT_TOKENS: output_tokens,
                GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: cache_creation_input_tokens,
                GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: cache_read_input_tokens,
            }
        )
        if cost_usd is not None:
            self.attributes[PORTFOLIO_COST_USD] = round(cost_usd, 6)
        self.completion_recorded = True


@contextmanager
def genai_call(system: str, model: str) -> Iterator[GenAICall]:
    """Measure one non-streaming chat call without capturing prompt/response content."""
    request_attributes: dict[str, object] = {
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_SYSTEM: system,
        GEN_AI_REQUEST_MODEL: model,
    }
    started = time.perf_counter()
    manager = _span_factory("chat", request_attributes) if _span_factory else nullcontext(None)
    with manager as span:
        call = GenAICall()
        try:
            yield call
        except Exception as exc:
            call.error_type = type(exc).__name__
            raise
        finally:
            duration = max(0.0, time.perf_counter() - started)
            setter = getattr(span, "set_attribute", None)
            if callable(setter):
                for name, value in call.attributes.items():
                    setter(name, value)
                if call.error_type is not None:
                    setter("error.type", call.error_type)
            payload: dict[str, object] = {
                **request_attributes,
                **call.attributes,
                METRIC_OPERATION_DURATION: duration,
                "input_tokens": call.attributes.get(GEN_AI_USAGE_INPUT_TOKENS),
                "output_tokens": call.attributes.get(GEN_AI_USAGE_OUTPUT_TOKENS),
                "model_duration_ms": round(duration * 1000),
                "estimated_cost_usd": call.attributes.get(PORTFOLIO_COST_USD),
                "cost_estimate_available": PORTFOLIO_COST_USD in call.attributes,
                "completion_recorded": call.completion_recorded,
                "error_type": call.error_type,
            }
            level = logging.ERROR if call.error_type is not None else logging.INFO
            _emit(level, "genai_call", payload)
