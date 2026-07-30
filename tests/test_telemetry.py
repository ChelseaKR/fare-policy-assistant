from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from assistant import telemetry
from assistant._vendor.genai_telemetry.attributes import (
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


@pytest.fixture(autouse=True)
def reset_span_factory() -> Iterator[None]:
    yield
    telemetry.set_span_factory(None)


def test_optional_span_receives_canonical_completion_attributes() -> None:
    requests: list[dict[str, object]] = []
    completed: dict[str, object] = {}

    class Span:
        def set_attribute(self, name: str, value: object) -> None:
            completed[name] = value

    @contextmanager
    def factory(name: str, attributes: dict[str, object]) -> Iterator[Span]:
        assert name == "chat"
        requests.append(attributes)
        yield Span()

    telemetry.set_span_factory(factory)
    with telemetry.genai_call("anthropic", "claude-haiku-4-5") as call:
        call.record_completion(
            model="claude-haiku-4-5",
            input_tokens=10,
            output_tokens=2,
            cost_usd=0.00002,
            cache_creation_input_tokens=3,
            cache_read_input_tokens=2,
        )

    assert requests[0][GEN_AI_SYSTEM] == "anthropic"
    assert requests[0][GEN_AI_REQUEST_MODEL] == "claude-haiku-4-5"
    assert completed[GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert completed[GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] == 3
    assert completed[GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 2
    assert completed[PORTFOLIO_COST_USD] == 0.00002


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 1, "output_tokens": "1"},
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 2,
        },
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("inf")},
    ],
)
def test_completion_telemetry_rejects_invalid_measurements(kwargs) -> None:
    values = {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0, **kwargs}
    with telemetry.genai_call("anthropic", "claude-haiku-4-5") as call:
        with pytest.raises(ValueError):
            call.record_completion(model="claude-haiku-4-5", **values)


def _genai_record(caplog) -> logging.LogRecord:
    return next(record for record in reversed(caplog.records) if record.event == "genai_call")


def test_completion_is_structured_with_canonical_and_filter_safe_fields(
    caplog, monkeypatch
) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "17")
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        with telemetry.request_correlation("lambda-request-123"):
            with telemetry.genai_call("anthropic", "requested-model") as call:
                call.record_completion(
                    model="served-model",
                    input_tokens=10,
                    output_tokens=2,
                    cost_usd=0.00002,
                )

    record = _genai_record(caplog)
    fields = vars(record)
    assert record.message == "genai_call"
    assert fields["event"] == "genai_call"
    assert fields["aws_request_id"] == "lambda-request-123"
    assert fields["function_version"] == "17"
    assert fields[GEN_AI_REQUEST_MODEL] == "requested-model"
    assert fields[GEN_AI_RESPONSE_MODEL] == "served-model"
    assert fields[GEN_AI_USAGE_INPUT_TOKENS] == fields["input_tokens"] == 10
    assert fields[GEN_AI_USAGE_OUTPUT_TOKENS] == fields["output_tokens"] == 2
    assert fields[METRIC_OPERATION_DURATION] >= 0
    assert fields["model_duration_ms"] >= 0
    assert fields[PORTFOLIO_COST_USD] == fields["estimated_cost_usd"] == 0.00002
    assert fields["cost_estimate_available"] is True
    assert fields["completion_recorded"] is True


def test_unknown_price_is_explicitly_unavailable_not_zero(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        with telemetry.genai_call("new-provider", "unpriced-model") as call:
            call.record_completion(
                model="unpriced-model",
                input_tokens=3,
                output_tokens=1,
                cost_usd=None,
            )

    fields = vars(_genai_record(caplog))
    assert PORTFOLIO_COST_USD not in fields
    assert fields["estimated_cost_usd"] is None
    assert fields["cost_estimate_available"] is False
    assert fields["completion_recorded"] is True


def test_model_failure_is_error_without_message_or_stack(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        with pytest.raises(RuntimeError, match="private-provider-detail"):
            with telemetry.genai_call("anthropic", "requested-model"):
                raise RuntimeError("private-provider-detail")

    record = _genai_record(caplog)
    fields = vars(record)
    assert record.levelno == logging.ERROR
    assert fields["error_type"] == "RuntimeError"
    assert fields["completion_recorded"] is False
    assert fields["input_tokens"] is None
    assert fields["output_tokens"] is None
    assert fields["estimated_cost_usd"] is None
    assert fields["cost_estimate_available"] is False
    assert record.exc_info is None
    assert "private-provider-detail" not in repr(fields)
