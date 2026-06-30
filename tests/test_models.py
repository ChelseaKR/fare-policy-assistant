"""Model-adapter tests.

The mock backend and dispatch need no network. The Anthropic and Bedrock
backends are covered by injecting a fake SDK client (monkeypatching the
`anthropic` constructors), so the adapter's response handling — joining text
blocks and reading token usage — is verified without a paid call.
"""

from __future__ import annotations

import sys
import types

import pytest

from assistant import models


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Usage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


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
        [_Block("text", "Senior fare is $1.00 "), _Block("thinking", "ignore me"),
         _Block("text", "[doc:mst-fares].")],
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
    assert isinstance(models.get_model("bedrock", "us.anthropic.claude-haiku-4-5"),
                      models.BedrockModel)


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


def test_bedrock_uses_region_and_reads_usage(fake_anthropic, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    model = models.BedrockModel("us.anthropic.claude-haiku-4-5")
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)
    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.input_tokens == 42 and out.output_tokens == 13
