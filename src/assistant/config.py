"""Central configuration: paths, model choices, thresholds.

Everything that affects answer or eval behavior is pinned here or in a
versioned file under prompts/ so that eval runs are reproducible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.yaml"
RAW_DIR = CORPUS_DIR / "raw"
PROCESSED_DIR = CORPUS_DIR / "processed"
INDEX_DIR = CORPUS_DIR / "index"
CHUNKS_PATH = PROCESSED_DIR / "chunks.jsonl"
PROMPTS_DIR = REPO_ROOT / "prompts"
EVAL_SUITES_DIR = REPO_ROOT / "evals" / "suites"
EVAL_RUNS_DIR = REPO_ROOT / "evals" / "runs"

KNOWN_AGENCIES = ("MST", "SBMTD", "Yolobus", "SacRT")

# Riders asking about agencies we do not cover get pointed here.
STATEWIDE_TRANSIT_INFO = "https://511.org (Bay Area) or the agency's own website"


# Bedrock serves the same models under anthropic.-prefixed IDs.
_DEFAULT_MODELS = {
    "bedrock": ("anthropic.claude-haiku-4-5", "anthropic.claude-sonnet-4-6"),
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-4-6"),
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
    top_k: int = 6
    # Below this top BM25 score the assistant declines rather than guessing.
    min_confidence: float = 4.0
    use_dense: bool = os.environ.get("FPA_DENSE", "") == "1"
    dense_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Hybrid mixing weight when dense retrieval is enabled.
    dense_weight: float = 0.5


@dataclass(frozen=True)
class Config:
    models: ModelConfig = field(default_factory=ModelConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)


def load_prompt(name: str) -> str:
    """Read a versioned prompt file from prompts/ (e.g. 'system')."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """First line of a prompt file is its version header, e.g. '# v1 2026-06-11'."""
    first = load_prompt(name).splitlines()[0]
    return first.lstrip("# ").strip()
