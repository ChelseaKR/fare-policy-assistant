from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from assistant import telemetry
from assistant._vendor.genai_telemetry.attributes import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
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
