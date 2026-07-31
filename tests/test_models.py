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
    GEN_AI_RESPONSE_MODEL,
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
    def __init__(self, blocks, usage, *, model=None):
        self.content = blocks
        self.usage = usage
        if model is not None:
            self.model = model


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
    http_clients: list[httpx.Client] = []
    resp = _Resp(
        [
            _Block("text", "Senior fare is $1.00 "),
            _Block("thinking", "ignore me"),
            _Block("text", "[doc:mst-fares]."),
        ],
        _Usage(42, 13),
    )
    fake = types.ModuleType("anthropic")

    def anthropic_client(*args, **kwargs):
        recorder["anthropic_client"] = {"args": args, "kwargs": kwargs}
        http_clients.append(kwargs["http_client"])
        return _FakeClient(resp, recorder)

    def bedrock_client(*args, **kwargs):
        recorder["bedrock_client"] = {"args": args, "kwargs": kwargs}
        http_clients.append(kwargs["http_client"])
        return _FakeClient(resp, recorder)

    fake.Anthropic = anthropic_client
    fake.AnthropicBedrock = bedrock_client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    yield recorder
    for client in http_clients:
        client.close()


def _assert_isolated_http_client(value: object) -> None:
    assert isinstance(value, httpx.Client)
    assert value._trust_env is False  # noqa: SLF001 - security invariant under test
    assert value.follow_redirects is True


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


def test_anthropic_joins_text_blocks_and_reads_usage(fake_anthropic, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    model = models.AnthropicModel("claude-haiku-4-5")
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)
    # Only text blocks are joined; the "thinking" block is dropped.
    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.model == "claude-haiku-4-5"
    assert out.input_tokens == 42 and out.output_tokens == 13
    assert fake_anthropic["model"] == "claude-haiku-4-5"
    client_kwargs = fake_anthropic["anthropic_client"]["kwargs"]
    assert client_kwargs["base_url"] == "https://api.anthropic.com"
    _assert_isolated_http_client(client_kwargs["http_client"])


def test_anthropic_emits_canonical_pii_free_telemetry(fake_anthropic, caplog):
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        models.AnthropicModel("claude-haiku-4-5").complete(
            system="sensitive system", user="sensitive rider question", max_tokens=64, temperature=0
        )
    event = vars(caplog.records[-1])
    assert event[GEN_AI_SYSTEM] == "anthropic"
    assert event[GEN_AI_REQUEST_MODEL] == "claude-haiku-4-5"
    assert event[GEN_AI_USAGE_INPUT_TOKENS] == 42
    assert event[METRIC_OPERATION_DURATION] >= 0
    assert event["input_tokens"] == 42
    assert event["output_tokens"] == 13
    assert event["completion_recorded"] is True
    assert "sensitive" not in repr(event)


def test_bedrock_uses_region_and_reads_usage(fake_anthropic, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("ANTHROPIC_BEDROCK_BASE_URL", raising=False)
    model = models.BedrockModel("us.anthropic.claude-haiku-4-5")
    out = model.complete(system="s", user="u", max_tokens=64, temperature=0.0)
    assert out.text == "Senior fare is $1.00 [doc:mst-fares]."
    assert out.input_tokens == 42 and out.output_tokens == 13
    client_kwargs = fake_anthropic["bedrock_client"]["kwargs"]
    assert client_kwargs["aws_region"] == "us-east-1"
    assert client_kwargs["base_url"] == "https://bedrock-runtime.us-east-1.amazonaws.com"
    _assert_isolated_http_client(client_kwargs["http_client"])


def test_hosted_clients_receive_the_exact_centralized_endpoint(fake_anthropic, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic-gateway.example/v1/")
    models.AnthropicModel("claude-haiku-4-5")
    monkeypatch.setenv("AWS_REGION", "us-gov-west-1")
    monkeypatch.setenv(
        "ANTHROPIC_BEDROCK_BASE_URL",
        "https://bedrock-gateway.example/runtime/",
    )
    models.BedrockModel("us.anthropic.claude-haiku-4-5")

    anthropic_kwargs = fake_anthropic["anthropic_client"]["kwargs"]
    assert anthropic_kwargs["base_url"] == "https://anthropic-gateway.example/v1"
    _assert_isolated_http_client(anthropic_kwargs["http_client"])
    bedrock_kwargs = fake_anthropic["bedrock_client"]["kwargs"]
    assert bedrock_kwargs["aws_region"] == "us-gov-west-1"
    assert bedrock_kwargs["base_url"] == "https://bedrock-gateway.example/runtime"
    _assert_isolated_http_client(bedrock_kwargs["http_client"])


@pytest.mark.parametrize(
    ("provider", "environment", "message"),
    [
        ("anthropic", {"ANTHROPIC_BASE_URL": "file:///tmp/provider"}, "ANTHROPIC_BASE_URL"),
        (
            "anthropic",
            {"ANTHROPIC_BASE_URL": "http://api.anthropic.test"},
            "ANTHROPIC_BASE_URL",
        ),
        (
            "anthropic",
            {"ANTHROPIC_CUSTOM_HEADERS": "X-Secret: never-release-identify-this"},
            "ANTHROPIC_CUSTOM_HEADERS",
        ),
        (
            "bedrock",
            {"ANTHROPIC_BEDROCK_BASE_URL": "https://user:secret@example.test"},
            "ANTHROPIC_BEDROCK_BASE_URL",
        ),
        (
            "bedrock",
            {"ANTHROPIC_BEDROCK_BASE_URL": "http://bedrock.example.test"},
            "ANTHROPIC_BEDROCK_BASE_URL",
        ),
        ("bedrock", {"AWS_REGION": "US West 2"}, "AWS_REGION"),
        ("local", {"FPA_OLLAMA_HOST": "https://kiosk.example/api"}, "FPA_OLLAMA_HOST"),
    ],
)
def test_model_construction_rejects_invalid_transport_environment(
    fake_anthropic,
    monkeypatch,
    provider,
    environment,
    message,
):
    for name in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "AWS_REGION",
        "FPA_OLLAMA_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        models.get_model(provider, "model-v1")


def test_custom_header_rejection_does_not_echo_the_secret(fake_anthropic, monkeypatch):
    secret = "never-echo-this-header-secret"
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", f"X-Secret: {secret}")

    with pytest.raises(ValueError) as caught:
        models.AnthropicModel("claude-haiku-4-5")

    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    ("model_class", "request_model"),
    [
        (models.AnthropicModel, "claude-haiku-4-5"),
        (
            models.BedrockModel,
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        ),
    ],
)
def test_hosted_completion_emits_actual_response_model_but_prices_request_model(
    fake_anthropic,
    caplog,
    monkeypatch,
    model_class,
    request_model,
):
    response = _Resp(
        [_Block("text", "answer")],
        _Usage(4, 2),
        model="provider-resolved-model",
    )
    priced_models: list[str] = []

    def estimate(model, *args, **kwargs):
        priced_models.append(model)
        return 0.0

    monkeypatch.setattr(models.config, "estimate_cost_usd", estimate)
    model = model_class(request_model)
    model._client = _FakeClient(response, {})
    with caplog.at_level(logging.INFO, logger="fare_assistant"):
        completion = model.complete("system", "question", 64, 0.0)
    event = vars(caplog.records[-1])
    assert completion.model == "provider-resolved-model"
    assert event[GEN_AI_REQUEST_MODEL] == request_model
    assert event[GEN_AI_RESPONSE_MODEL] == "provider-resolved-model"
    assert priced_models == [request_model]


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
    event = vars(caplog.records[-1])
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
    assert model._client._trust_env is False  # noqa: SLF001
    # Swap in a client wired to a fake transport instead of a real socket —
    # same base_url, so the /api/chat path assembly is exercised for real.
    base_url = model._client.base_url
    model._client.close()
    model._client = httpx.Client(
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        trust_env=False,
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
    model._client.close()


def test_local_uses_fpa_ollama_host(monkeypatch):
    monkeypatch.setenv("FPA_OLLAMA_HOST", "http://kiosk-box:11434")
    model = models.LocalModel("llama3.2:3b")
    assert str(model._client.base_url) == "http://kiosk-box:11434"
    assert model._client._trust_env is False  # noqa: SLF001
    model._client.close()


def test_local_canonicalizes_one_trailing_endpoint_slash(monkeypatch):
    monkeypatch.setenv("FPA_OLLAMA_HOST", "https://selected-kiosk.example:11434/")
    model = models.LocalModel("llama3.2:3b")

    assert str(model._client.base_url) == "https://selected-kiosk.example:11434"
    assert model._client._trust_env is False  # noqa: SLF001
    model._client.close()
