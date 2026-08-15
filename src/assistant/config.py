"""Central configuration: paths, model choices, thresholds.

Everything that affects answer or eval behavior is pinned here or in a
versioned file under prompts/ so that eval runs are reproducible.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from assistant import domain
from assistant._vendor.genai_telemetry import Usage, cost_usd

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
# Schema-v2, source-complete snapshots keyed by their full snapshot identity.
# Unlike the legacy processed-only archives above, each snapshot carries the
# exact raw bytes and fetch receipt needed to re-verify its provenance.
SNAPSHOTS_DIR = CORPUS_DIR / "snapshots"
FACTS_PATH = PROCESSED_DIR / "facts.jsonl"
PROMPTS_DIR = REPO_ROOT / "prompts"
ANSWER_SCHEMA_PATH = REPO_ROOT / "docs" / "answer-contract.schema.json"
RELEASE_DESCRIPTOR_PATH = REPO_ROOT / "release" / "release.json"
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

DEFAULT_PROVIDER = "bedrock"
DEFAULT_AWS_REGION = "us-west-2"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_STALENESS_BUDGET_DAYS = 90
DEFAULT_EMBED_ANCESTORS = "'self'"

_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-[1-9][0-9]*$")

# Rider-runtime limits currently enforced by ``web.handler``. They live here as
# named release inputs so the handler and the release descriptor can share one
# reviewed value instead of maintaining parallel, unversioned constants.
MAX_QUESTION_CHARS = 500
MAX_BODY_BYTES = 16 * 1024
REQUESTS_PER_MINUTE = 8
ANSWER_CACHE_SIZE = 256
MAX_HISTORY_TURNS = 3
MAX_HISTORY_ANSWER_CHARS = 1200
ANSWER_CACHE_KEY_SCHEMA = "fare-assistant.answer-cache.v2"

# Per-caller limiting and the spend circuit breaker (``web.ratelimit``, ADR 0025).
#
# The window is deliberately short: it is also the rotation period of the salt
# that keys a caller's digest, so two windows of the same rider are not linkable
# to each other. The quotas are deliberately generous, because this limiter is
# not the spend ceiling -- the gateway throttle (2 rps) and reserved concurrency
# (2) remain the ceiling. Its one job is to stop a single source from consuming
# the whole aggregate allowance and starving every other rider. At 10 asks per
# 60 seconds, one source can take at most ~8% of the ~120 requests/minute the
# gateway admits, while a human asking fare questions -- or a NAT/CGNAT address
# shared by several riders, which is one key for all of them -- stays under it.
#
# Note the deliberate ordering against REQUESTS_PER_MINUTE above: the ask quota
# (10) is above the per-container in-process budget (8), so under a burst that
# lands on one warm container the shared in-process backstop can still trip
# first and return 429 to everybody. That backstop was always aggregate and is
# unchanged here; the per-caller guarantee is at the gateway scale, where the
# spend actually comes from. Lowering the quota below 8 would make the
# per-caller limit bind first but would also start refusing the deploy's own
# production smoke, which issues several asks from one address.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ASK_PER_WINDOW = 10
RATE_LIMIT_FEEDBACK_PER_WINDOW = 20
# How long one container may reuse its last spend-breaker read before checking
# again. This is the worst-case lag between an operator (or the cost alarm)
# tripping the breaker and this container stopping model calls.
SPEND_BREAKER_CACHE_SECONDS = 30
# Domain separation for the keyed caller digest. Changing it invalidates every
# in-flight counter, which is a safe (fail-open) operation.
CALLER_DIGEST_SCHEMA = "fare-assistant.caller-digest.v1"

# Both evaluator calls deliberately use the same bounded deterministic request
# settings. Their prompt bytes and model ID remain distinct release inputs.
JUDGE_MAX_TOKENS = 512
JUDGE_TEMPERATURE = 0.0


def _environment(environment: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def _provider_from_environment(environment: Mapping[str, str] | None = None) -> str:
    return _environment(environment).get("FPA_PROVIDER", DEFAULT_PROVIDER)


def _default_model(provider: str, index: int) -> str:
    try:
        return _DEFAULT_MODELS[provider][index]
    except KeyError as exc:
        raise ValueError(f"unsupported model provider: {provider!r}") from exc


@dataclass(frozen=True)
class ProviderTransport:
    """One validated, explicit model-provider transport selection.

    The endpoint itself is passed to the client but is represented only by an
    opaque digest in the public release descriptor. Credentials remain outside
    this value; direct-Anthropic custom headers fail closed because secret
    header values cannot safely become public identity inputs.
    """

    provider: str
    base_url: str | None
    aws_region: str | None


def _base_url(
    value: object,
    *,
    environment_name: str,
    origin_only: bool = False,
    require_https: bool = False,
) -> str:
    expected_scheme = "HTTPS" if require_https else "HTTP(S)"
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{environment_name} must be a trimmed absolute {expected_scheme} URL")
    if any(character.isspace() for character in value):
        raise ValueError(f"{environment_name} must not contain whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{environment_name} must contain a valid host and port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or (require_https and parsed.scheme != "https")
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            f"{environment_name} must be an absolute {expected_scheme} URL without "
            "credentials, query parameters, or a fragment"
        )
    if origin_only and parsed.path not in {"", "/"}:
        raise ValueError(f"{environment_name} must be an HTTP(S) origin without a path")

    # All three clients treat one terminal slash as the same base endpoint.
    # Remove that one redundant spelling before both use and hashing, while
    # leaving repeated slashes untouched rather than guessing their semantics.
    if value.endswith("/") and not value.endswith("//"):
        return value[:-1]
    return value


def is_canonical_aws_region(value: object) -> bool:
    """Return whether ``value`` is the canonical region spelling we accept."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and _AWS_REGION.fullmatch(value) is not None
    )


def resolve_provider_transport(
    provider: str,
    environment: Mapping[str, str] | None = None,
) -> ProviderTransport:
    """Resolve provider region/endpoint once, without SDK profile fallbacks."""
    if provider not in _DEFAULT_MODELS:
        raise ValueError(f"unsupported model provider: {provider!r}")
    values = _environment(environment)

    if provider == "bedrock":
        region = values.get("AWS_REGION", DEFAULT_AWS_REGION)
        if not is_canonical_aws_region(region):
            raise ValueError("AWS_REGION must be a canonical AWS region name")
        assert isinstance(region, str)
        default_url = f"https://bedrock-runtime.{region}.amazonaws.com"
        return ProviderTransport(
            provider=provider,
            base_url=_base_url(
                values.get("ANTHROPIC_BEDROCK_BASE_URL", default_url),
                environment_name="ANTHROPIC_BEDROCK_BASE_URL",
                require_https=True,
            ),
            aws_region=region,
        )

    if provider == "anthropic":
        custom_headers = values.get("ANTHROPIC_CUSTOM_HEADERS")
        if custom_headers is not None and custom_headers != "":
            raise ValueError(
                "ANTHROPIC_CUSTOM_HEADERS is unsupported because secret headers "
                "cannot be included in release identity"
            )
        return ProviderTransport(
            provider=provider,
            base_url=_base_url(
                values.get("ANTHROPIC_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL),
                environment_name="ANTHROPIC_BASE_URL",
                require_https=True,
            ),
            aws_region=None,
        )

    if provider == "local":
        return ProviderTransport(
            provider=provider,
            base_url=_base_url(
                values.get("FPA_OLLAMA_HOST", DEFAULT_OLLAMA_BASE_URL),
                environment_name="FPA_OLLAMA_HOST",
                origin_only=True,
            ),
            aws_region=None,
        )

    return ProviderTransport(provider=provider, base_url=None, aws_region=None)


@dataclass(frozen=True)
class ModelConfig:
    """Pinned model versions. The judge model must differ from the answer model."""

    provider: str = field(default_factory=_provider_from_environment)
    answer_model: str = field(
        default_factory=lambda: os.environ.get(
            "FPA_ANSWER_MODEL",
            _default_model(_provider_from_environment(), 0),
        )
    )
    judge_model: str = field(
        default_factory=lambda: os.environ.get(
            "FPA_JUDGE_MODEL",
            _default_model(_provider_from_environment(), 1),
        )
    )
    max_tokens: int = 1024
    temperature: float = 0.0

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> ModelConfig:
        """Resolve one internally consistent model configuration from ``environment``."""
        values = _environment(environment)
        provider = values.get("FPA_PROVIDER", DEFAULT_PROVIDER)
        return cls(
            provider=provider,
            answer_model=values.get("FPA_ANSWER_MODEL", _default_model(provider, 0)),
            judge_model=values.get("FPA_JUDGE_MODEL", _default_model(provider, 1)),
        )


@dataclass(frozen=True)
class RetrievalConfig:
    # 8 rather than 6: fare-table chunks are number-heavy and rank low on
    # BM25 even when they hold the answer (eval cases ground-001, ground-014).
    #
    # 12 rather than 8, for the same reason at three times the corpus. top_k=8
    # was set when six agencies were indexed; eighteen are now, and each new
    # agency changes IDF for every existing chunk, so a document that used to
    # place 6th within its own agency now places 9th or 11th. Measured offline
    # against the 2026-08-15 nightly's failures, nine cases whose required fact
    # sits in the corpus, in the right agency, and above rank 12 were never
    # shown it at rank 8: edge-056, edge-actransit-001, edge-actransit-004,
    # fresh-004, ground-043, ml-028, ml-actransit-002, tl-001, and (at 16)
    # refuse-019. Raising to 12 recovers eight of the nine; 16 recovers the
    # ninth and doubles the token cost of every call, so 12 is where this sits
    # until the next corpus growth moves it again.
    top_k: int = 12
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
    use_dense: bool = field(default_factory=lambda: os.environ.get("FPA_DENSE", "") == "1")
    dense_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Hybrid mixing weight when dense retrieval is enabled.
    dense_weight: float = 0.5

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> RetrievalConfig:
        """Resolve the environment-selectable retrieval mode explicitly."""
        values = _environment(environment)
        dense = values.get("FPA_DENSE", "")
        if dense not in {"", "0", "1"}:
            raise ValueError("FPA_DENSE must be empty, 0, or 1")
        return cls(use_dense=dense == "1")


@dataclass(frozen=True)
class Config:
    models: ModelConfig = field(default_factory=ModelConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> Config:
        """Resolve all environment-backed choices from one supplied mapping."""
        values = _environment(environment)
        return cls(
            models=ModelConfig.from_environment(values),
            retrieval=RetrievalConfig.from_environment(values),
        )


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    provider: str | None = None,
    endpoint_type: str | None = None,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """Estimate from the shared table; ambiguous/unknown pricing stays visible."""
    shared_provider = "aws.bedrock" if provider == "bedrock" else provider
    return cost_usd(
        Usage(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            provider=shared_provider,
            endpoint_type=endpoint_type,
        )
    )


def load_prompt(name: str) -> str:
    """Read a versioned prompt file from prompts/ (e.g. 'system')."""
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """First line of a prompt file is its version header, e.g. '# v1 2026-06-11'."""
    first = load_prompt(name).splitlines()[0]
    return first.lstrip("# ").strip()
