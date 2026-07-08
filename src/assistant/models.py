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


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


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
        resp = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return Completion(
            text=text,
            model=self.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


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
        resp = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return Completion(
            text=text,
            model=self.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


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
        return Completion(
            text=data["message"]["content"],
            model=self.model,
            # Ollama's own token counts for the served request, not a
            # cross-provider-comparable tokenizer — good enough for the
            # cost/usage bookkeeping the eval harness does per backend.
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )


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
        return Completion(text=text, model=self.model)


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
