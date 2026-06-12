"""Provider-portable model adapter.

Three backends:
  bedrock   — default; Claude on Amazon Bedrock via the Anthropic SDK's
              Bedrock client (AWS credential chain, anthropic.-prefixed
              model IDs). See ADR 0003.
  anthropic — direct Anthropic API, available behind FPA_PROVIDER=anthropic.
  mock      — deterministic offline backend for tests and plumbing runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class Model(Protocol):
    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        ...


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

    Same Messages API shape as the direct API; model IDs carry the
    `anthropic.` provider prefix (config handles that). Credentials resolve
    through the standard AWS chain (env vars, profile, instance role); region
    comes from AWS_REGION.
    """

    def __init__(self, model: str):
        import os

        import anthropic

        self.model = model
        # Default region matches the CI configuration; AWS_REGION overrides.
        self._client = anthropic.AnthropicBedrockMantle(
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
    if provider == "mock":
        return MockModel(model)
    raise ValueError(f"unknown provider: {provider}")
