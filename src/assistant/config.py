"""Central configuration: paths, model choices, thresholds.

Everything that affects answer or eval behavior is pinned here or in a
versioned file under prompts/ so that eval runs are reproducible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from assistant import domain

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.yaml"
RAW_DIR = CORPUS_DIR / "raw"
PROCESSED_DIR = CORPUS_DIR / "processed"
INDEX_DIR = CORPUS_DIR / "index"
CHUNKS_PATH = PROCESSED_DIR / "chunks.jsonl"
# Retained corpus history (EXP-05): one subdirectory per distinct corpus_version,
# written by assistant.corpus.archive_version and never overwritten in place.
VERSIONS_DIR = CORPUS_DIR / "versions"
FACTS_PATH = PROCESSED_DIR / "facts.jsonl"
PROMPTS_DIR = REPO_ROOT / "prompts"
EVAL_SUITES_DIR = REPO_ROOT / "evals" / "suites"
EVAL_RUNS_DIR = REPO_ROOT / "evals" / "runs"
# Content-keyed answer/judge cache (evals/cache.py, FIX-12). Gitignored, like
# evals/runs/ — it is a local speed/cost optimization, not an artifact.
EVAL_CACHE_DIR = REPO_ROOT / "evals" / "cache"


# Sourced from the active domain profile (src/assistant/domain.py) so the
# transit-specific knobs live in one place. These are call-time accessors, not
# import-time constants: the active profile is chosen by FPA_DOMAIN, which may
# be set any time before a request is handled, so binding the value at import
# would pin it to whatever profile was active when this module first loaded.
def known_agencies() -> tuple[str, ...]:
    """The scopes (agencies) of the active domain profile, read at call time."""
    return domain.get_profile().scopes


# Riders asking about agencies we do not cover get pointed here.
def statewide_transit_info() -> str:
    """The active profile's fallback contact, read at call time."""
    return domain.get_profile().fallback_contact


# Backward-compat: the old module constants KNOWN_AGENCIES / STATEWIDE_TRANSIT_INFO
# now resolve to the live profile values on each attribute access, so external
# users and tests that read them keep working while the value tracks FPA_DOMAIN.
def __getattr__(name: str):
    if name == "KNOWN_AGENCIES":
        return known_agencies()
    if name == "STATEWIDE_TRANSIT_INFO":
        return statewide_transit_info()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Bedrock serves these models through cross-region inference profiles
# (us.-prefixed IDs); direct anthropic.-prefixed IDs reject invocation.
_DEFAULT_MODELS = {
    "bedrock": (
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-sonnet-4-6",
    ),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-4-6"),
    # Two distinct small models so the judge-must-differ-from-answer rule
    # holds standalone (`FPA_PROVIDER=local`), same as the hosted backends.
    # Both are small enough to be kiosk-appropriate; pull with
    # `ollama pull llama3.2:3b && ollama pull qwen2.5:3b`. See ADR 0010.
    "local": ("llama3.2:3b", "qwen2.5:3b"),
    "mock": ("mock", "mock"),
}

_provider = os.environ.get("FPA_PROVIDER", "bedrock")


@dataclass(frozen=True)
class ModelConfig:
    """Pinned model versions. The judge model must differ from the answer model."""

    provider: str = _provider
    answer_model: str = os.environ.get(
        "FPA_ANSWER_MODEL", _DEFAULT_MODELS.get(_provider, _DEFAULT_MODELS["bedrock"])[0]
    )
    judge_model: str = os.environ.get(
        "FPA_JUDGE_MODEL", _DEFAULT_MODELS.get(_provider, _DEFAULT_MODELS["bedrock"])[1]
    )
    max_tokens: int = 1024
    temperature: float = 0.0


@dataclass(frozen=True)
class RetrievalConfig:
    # 8 rather than 6: fare-table chunks are number-heavy and rank low on
    # BM25 even when they hold the answer (eval cases ground-001, ground-014).
    top_k: int = 8
    # Mild preference for chunks in the question's language.
    language_boost: float = 1.2
    # FIX-07 / ADR 0013: the decline rule reads normalized, corpus-size-
    # independent signals (assistant.retrieve.ConfidenceSignals) instead of
    # an absolute BM25 score. An absolute score drifts every time the corpus
    # grows (every new agency changes IDF for every existing chunk), so the
    # old `min_confidence = 4.0` was an untracked moving floor. Below this
    # z-score (top result vs the full-corpus score distribution for the same
    # query) *or* below this fraction of query terms actually present in the
    # top chunk, the assistant declines rather than guessing. Calibrated by
    # evals/decline_calibration.py against a labeled should-answer/
    # should-decline question set — see the ablation table in ADR 0013.
    # Re-run the calibration after every corpus change.
    #
    # 2026-07-11 re-calibration (ADR 0013 amendment): the corpus grew since the
    # original ADR (SacRT, HTA), moving the recommended tightest 100%-answer-
    # coverage z from 1.75 to 1.50. At the stale 1.75 the harness reported only
    # 98.2% should-answer coverage — it wrongly declined on-topic natural-
    # language process questions (eval edge-046 z=1.72, sens-003a z=1.67,
    # conv-forged-004 z=1.53) for zero gain in should-decline recall (0.0% at
    # every 100%-coverage row: the z-gate is not what separates out-of-corpus
    # questions). 1.50 is the harness's own recommendation, not a hand-pick.
    decline_z_threshold: float = 1.50
    decline_coverage_floor: float = 0.10
    # Operational confidence band for the answered path (not a tuned eval
    # parameter, and not itself calibrated by the ablation): a top z-score at
    # or above this reads as "high", between decline_z_threshold and this as
    # "medium". Surfaced to integrators and staff who want a graded signal,
    # never used to gate or alter an answer.
    confidence_high_z: float = 3.5
    use_dense: bool = os.environ.get("FPA_DENSE", "") == "1"
    dense_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Hybrid mixing weight when dense retrieval is enabled.
    dense_weight: float = 0.5


@dataclass(frozen=True)
class Config:
    models: ModelConfig = field(default_factory=ModelConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)


# Estimated list prices, USD per 1M tokens (input, output), for the models the
# eval harness calls. Token counts in the report are exact (from the API); the
# dollar figure is an estimate at these rates — update from the provider's
# pricing page when it moves. Unknown models fall back to (0, 0) and contribute
# nothing rather than a wrong number.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "us.anthropic.claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost for a token count at the list rates above (0 if unknown)."""
    rate_in, rate_out = MODEL_PRICES.get(model, (0.0, 0.0))
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def load_prompt(name: str) -> str:
    """Read a versioned prompt file from prompts/ (e.g. 'system')."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """First line of a prompt file is its version header, e.g. '# v1 2026-06-11'."""
    first = load_prompt(name).splitlines()[0]
    return first.lstrip("# ").strip()
