"""Provider-portable model adapter.

Four backends:
  bedrock   — default; Claude on Amazon Bedrock via the Anthropic SDK's
              Bedrock client (AWS credential chain, anthropic.-prefixed
              model IDs). See ADR 0003.
  anthropic — direct Anthropic API, available behind FPA_PROVIDER=anthropic.
  local     — offline, no-network backend for kiosk deployment: a small model
              served by Ollama on localhost. FPA_PROVIDER=local. See ADR 0010
              and evals/backend_comparison.py for the measured delta against
              Bedrock before shipping this on a kiosk.
  mock      — deterministic offline backend for tests and plumbing runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Module-level (unlike the lazy `import anthropic` in the hosted backends
# below): httpx is already an unconditional dependency of this project
# (transport for the Anthropic SDK), so importing it eagerly here costs
# nothing and lets tests monkeypatch `models.httpx.Client`.
import httpx

from assistant import config
from assistant.telemetry import genai_call


@dataclass
class Completion:
    text: str
    model: str
    # Canonical input total: fresh + cache creation + cache read.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def _usage_count(usage: object, name: str, *, default: int | None = None) -> int:
    """Read one SDK usage field without coercing malformed provider data."""
    try:
        value = getattr(usage, name)
    except AttributeError:
        if default is not None:
            return default
        raise ValueError(f"provider usage missing {name}") from None
    if type(value) is not int or value < 0:
        raise ValueError(f"provider usage {name} must be a non-negative integer")
    return value


def _completion_usage(usage: object) -> tuple[int, int, int, int]:
    """Normalize Anthropic's disjoint fresh/cache buckets to OTel totals."""
    fresh = _usage_count(usage, "input_tokens")
    output = _usage_count(usage, "output_tokens")
    cache_creation = _usage_count(usage, "cache_creation_input_tokens", default=0)
    cache_read = _usage_count(usage, "cache_read_input_tokens", default=0)
    return fresh + cache_creation + cache_read, output, cache_creation, cache_read


class Model(Protocol):
    def complete(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> Completion: ...


class AnthropicModel:
    def __init__(self, model: str):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        with genai_call("anthropic", self.model) as call:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            input_tokens, output_tokens, cache_creation, cache_read = _completion_usage(resp.usage)
            completion = Completion(
                text="".join(block.text for block in resp.content if block.type == "text"),
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
            )
            call.record_completion(
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=config.estimate_cost_usd(
                    completion.model,
                    completion.input_tokens,
                    completion.output_tokens,
                    provider="anthropic",
                    cache_creation_input_tokens=completion.cache_creation_input_tokens,
                    cache_read_input_tokens=completion.cache_read_input_tokens,
                ),
                cache_creation_input_tokens=completion.cache_creation_input_tokens,
                cache_read_input_tokens=completion.cache_read_input_tokens,
            )
            return completion


class BedrockModel:
    """Claude on Amazon Bedrock, via the Anthropic SDK's Bedrock client.

    Same Messages API shape as the direct API. Model IDs are cross-region
    inference profiles (`us.anthropic.…`, pinned in config) — Bedrock serves
    these models through inference profiles, not direct model IDs.
    Credentials resolve through the standard AWS chain (SSO profile, web
    identity, env vars, instance role); region comes from AWS_REGION.
    """

    def __init__(self, model: str):
        import os

        import anthropic

        self.model = model
        # Default region matches the CI configuration; AWS_REGION overrides.
        self._client = anthropic.AnthropicBedrock(
            aws_region=os.environ.get("AWS_REGION", "us-west-2")
        )

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        with genai_call("aws.bedrock", self.model) as call:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            input_tokens, output_tokens, cache_creation, cache_read = _completion_usage(resp.usage)
            completion = Completion(
                text="".join(block.text for block in resp.content if block.type == "text"),
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
            )
            call.record_completion(
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=config.estimate_cost_usd(
                    completion.model,
                    completion.input_tokens,
                    completion.output_tokens,
                    provider="bedrock",
                    cache_creation_input_tokens=completion.cache_creation_input_tokens,
                    cache_read_input_tokens=completion.cache_read_input_tokens,
                ),
                cache_creation_input_tokens=completion.cache_creation_input_tokens,
                cache_read_input_tokens=completion.cache_read_input_tokens,
            )
            return completion


class LocalModel:
    """A small model served locally by Ollama — no network call, no per-query cost.

    This is the offline kiosk backend from EXP-13 in `docs/ideation/03-expansions.md`:
    the identical guarded pipeline (retrieval, prompt assembly, citation
    extraction, guards) but with generation happening on-device against a
    model already pulled onto the kiosk hardware. Talks to the Ollama HTTP
    API (`ollama serve`, default `http://localhost:11434`) rather than the
    `ollama` Python package, so this backend adds no new dependency beyond
    `httpx`, already required for the Anthropic SDK's transport.

    `FPA_OLLAMA_HOST` overrides the host. A connection error (Ollama not
    running, wrong host) raises `httpx.ConnectError` — the caller sees the
    same "backend unreachable" failure shape as a Bedrock/Anthropic auth or
    network error, rather than a silent fallback to a different backend.
    """

    def __init__(self, model: str):
        import os

        self.model = model
        host = os.environ.get("FPA_OLLAMA_HOST", "http://localhost:11434")
        # Local generation on modest kiosk hardware can be slow; a long
        # timeout avoids flagging a slow-but-working answer as a backend
        # failure. No retries — a kiosk should fail fast and fall back to
        # EXP-07's no-model guide rather than hang.
        self._client = httpx.Client(base_url=host, timeout=120.0)

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        with genai_call("ollama", self.model) as call:
            resp = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            completion = Completion(
                text=data["message"]["content"],
                model=self.model,
                # Ollama's own token counts for the served request, not a
                # cross-provider-comparable tokenizer — good enough for the
                # usage bookkeeping the eval harness does per backend.
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
            )
            call.record_completion(
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=0.0,
            )
            return completion


class MockModel:
    """Echoes a grounded-looking answer built only from the passages it was given.

    Exists so tests and `--offline` eval runs exercise the full pipeline
    (prompt assembly, citation extraction, guards) without network or cost.
    """

    def __init__(self, model: str = "mock"):
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        # The answer prompt includes passages in [doc:<id>] blocks; cite the first.
        import re

        with genai_call("mock", self.model) as call:
            doc_ids = re.findall(r"\[doc:([a-z0-9-]+)\]", user)
            if doc_ids:
                text = (
                    "Based on the published policy, see the cited document for the "
                    f"specific criteria. [doc:{doc_ids[0]}]"
                )
            else:
                text = (
                    "I don't have a published policy document that answers this. "
                    "Please contact the transit agency directly."
                )
            completion = Completion(text=text, model=self.model)
            call.record_completion(
                model=completion.model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
            )
            return completion


def get_model(provider: str, model: str) -> Model:
    if provider == "anthropic":
        return AnthropicModel(model)
    if provider == "bedrock":
        return BedrockModel(model)
    if provider == "local":
        return LocalModel(model)
    if provider == "mock":
        return MockModel(model)
    raise ValueError(f"unknown provider: {provider}")
