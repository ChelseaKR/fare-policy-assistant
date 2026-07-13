"""Model-adapter tests.

The mock backend and dispatch need no network. The Anthropic and Bedrock
backends are covered by injecting a fake SDK client (monkeypatching the
`anthropic` constructors), so the adapter's response handling — joining text
blocks and reading token usage — is verified without a paid call. The local
(Ollama) backend is covered the same way, via an `httpx.MockTransport` in
place of a real HTTP call.
"""

from __future__ import annotations

import json
import logging
import sys
import types

import httpx
import pytest

from assistant import models
from assistant._vendor.genai_telemetry.attributes import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    METRIC_OPERATION_DURATION,
    PORTFOLIO_COST_USD,
)


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Usage:
    def __init__(self, i, o, *, cache_creation=0, cache_read=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_creation_input_tokens = cache_creation
        self.cache_read_input_tokens = cache_read


class _Resp:
    def __init__(self, blocks, usage):
        self.content = blocks
        self.usage = usage


class _FakeMessages:
    def __init__(self, resp, recorder):
        self._resp = resp
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        return self._resp


class _FakeClient:
    def __init__(self, resp, recorder):
        self.messages = _FakeMessages(resp, recorder)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Install a fake `anthropic` module whose clients return a canned response.

    Returns the recorder dict so tests can assert what was sent to the SDK.
    """
    recorder: dict = {}
    resp = _Resp(
        [
            _Block("text", "Senior fare is $1.00 "),
            _Block("thinking", "ignore me"),
            _Block("text", "[doc:mst-fares]."),
        ],
        _Usage(42, 13),
    )
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda *a, **k: _FakeClient(resp, recorder)
    fake.AnthropicBedrock = lambda *a, **k: _FakeClient(resp, recorder)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return recorder


# ── mock backend & dispatch ──────────────────────────────────────────────────


def test_mock_cites_first_passage_doc_id():
    out = models.MockModel().complete(
        system="s", user="[doc:mst-fares] passage text", max_tokens=10, temperature=0.0
    )
    assert "[doc:mst-fares]" in out.text


def test_mock_declines_without_passages():
    out = models.MockModel().complete(
        system="s", user="no docs here", max_tokens=10, temperature=0.0
    )
    assert "don't have a published policy" in out.text


def test_get_model_dispatches_each_provider(fake_anthropic):
    assert isinstance(models.get_model("mock", "mock"), models.MockModel)
    assert isinstance(models.get_model("anthropic", "claude-haiku-4-5"), models.AnthropicModel)
    assert isinstance(
        models.get_model("bedrock", "us.anthropic.claude-haiku-4-5"), models.BedrockModel
    )
    assert isinstance(models.get_model("local", "llama3.2:3b"), models.LocalModel)


def test_get_model_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        models.get_model("openai", "gpt")


# ── live backends, faked client ──────────────────────────────────────────────


def test_anthropic_joins_text_blocks_and_reads_usage(fake_anthropic):
    model = models.AnthropicModel("claude-haiku-4-5")
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)
    # Only text blocks are joined; the "thinking" block is dropped.
    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.model == "claude-haiku-4-5"
    assert out.input_tokens == 42 and out.output_tokens == 13
    assert fake_anthropic["model"] == "claude-haiku-4-5"


def test_anthropic_emits_canonical_pii_free_telemetry(fake_anthropic, caplog):
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        models.AnthropicModel("claude-haiku-4-5").complete(
            system="sensitive system", user="sensitive rider question", max_tokens=64, temperature=0
        )
    event = json.loads(caplog.records[-1].message)
    assert event[GEN_AI_SYSTEM] == "anthropic"
    assert event[GEN_AI_REQUEST_MODEL] == "claude-haiku-4-5"
    assert event[GEN_AI_USAGE_INPUT_TOKENS] == 42
    assert event[METRIC_OPERATION_DURATION] >= 0
    assert "sensitive" not in caplog.text


def test_bedrock_uses_region_and_reads_usage(fake_anthropic, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    model = models.BedrockModel("us.anthropic.claude-haiku-4-5")
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)
    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.input_tokens == 42 and out.output_tokens == 13


@pytest.mark.parametrize(
    ("model_class", "model_id", "expected_cost"),
    [
        (models.AnthropicModel, "claude-haiku-4-5", 0.78),
        (
            models.BedrockModel,
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            0.858,
        ),
    ],
)
def test_hosted_cache_usage_is_normalized_and_priced_once(
    fake_anthropic, caplog, model_class, model_id, expected_cost
):
    response = _Resp(
        [_Block("text", "Senior fare is $1.00 [doc:mst-fares].")],
        _Usage(500_000, 0, cache_creation=200_000, cache_read=300_000),
    )
    model = model_class(model_id)
    model._client = _FakeClient(response, {})
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        completion = model.complete("system", "question", 64, 0.0)
    event = json.loads(caplog.records[-1].message)
    assert completion.input_tokens == 1_000_000
    assert completion.cache_creation_input_tokens == 200_000
    assert completion.cache_read_input_tokens == 300_000
    assert event[GEN_AI_USAGE_INPUT_TOKENS] == 1_000_000
    assert event[GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] == 200_000
    assert event[GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] == 300_000
    assert event[PORTFOLIO_COST_USD] == pytest.approx(expected_cost)


@pytest.mark.parametrize(
    "usage",
    [
        _Usage(True, 1),
        _Usage(-1, 1),
        _Usage(1, "1"),
        _Usage(1, 1, cache_creation=-1),
        _Usage(1, 1, cache_read="1"),
    ],
)
def test_hosted_usage_rejects_malformed_counts(fake_anthropic, usage):
    model = models.AnthropicModel("claude-haiku-4-5")
    model._client = _FakeClient(_Resp([_Block("text", "answer")], usage), {})
    with pytest.raises(ValueError, match="provider usage"):
        model.complete("system", "question", 64, 0.0)


# ── local (Ollama) backend, faked transport ──────────────────────────────────


def test_local_posts_chat_and_reads_ollama_usage():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "Senior fare is $1.00 [doc:mst-fares].",
                },
                "prompt_eval_count": 40,
                "eval_count": 11,
            },
        )

    model = models.LocalModel("llama3.2:3b")
    # Swap in a client wired to a fake transport instead of a real socket —
    # same base_url, so the /api/chat path assembly is exercised for real.
    model._client = httpx.Client(
        base_url=model._client.base_url, transport=httpx.MockTransport(handler)
    )
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)

    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.model == "llama3.2:3b"
    assert out.input_tokens == 40 and out.output_tokens == 11
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "llama3.2:3b"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert captured["body"]["options"] == {"temperature": 0.0, "num_predict": 64}


def test_local_uses_fpa_ollama_host(monkeypatch):
    monkeypatch.setenv("FPA_OLLAMA_HOST", "http://kiosk-box:11434")
    model = models.LocalModel("llama3.2:3b")
    assert str(model._client.base_url) == "http://kiosk-box:11434"
