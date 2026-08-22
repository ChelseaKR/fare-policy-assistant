"""Eval runner.

    python -m evals.runner --smoke              # 26-case CI subset

The smoke subset's gated suites are small (4-6 cases each), so the below-macro
gate (`suites_below_macro`, ADR 0026) can only express a 2-case tolerance on
smoke, not a percentage one: a single failure in any gated suite is absorbed
as judge noise, and a second failure in the same suite is what actually fails
the build. This is coarser than the full suite's effective tolerance (every
full-run gated suite is large enough that a genuine breach already implies
several cases) and is a property of the sample size, not a relaxed gate.
    python -m evals.runner --full               # everything, then regenerate reports
    python -m evals.runner --offline            # mock model, deterministic checks only
    python -m evals.runner --suite refusal      # one suite
    python -m evals.runner --jobs 8              # bounded-concurrency case execution
    python -m evals.runner --no-cache            # skip the answer/judge cache (FIX-04 runs)
    python -m evals.runner --refresh-cache       # re-call the provider, then restore the cache
    python -m evals.runner --only-failed          # rerun only cases that failed last time
    python -m evals.runner --since 20260701T000000Z  # reuse unchanged cases from that run
    python -m evals.runner --replicates 3         # score every case 3x, Wilson intervals

Each run writes evals/runs/<timestamp>/ with results.jsonl (full traces) and
summary.json (scoreboard + versions). Judges run only when provider
credentials are available (AWS chain for bedrock, ANTHROPIC_API_KEY for
anthropic); otherwise judge verdicts are recorded as skipped, never as passes.

Cases execute under a bounded-concurrency ThreadPoolExecutor (`--jobs`,
default 4 — the pipeline is pure functions over an immutable retriever, and
4-8 workers fits Bedrock rate limits). Each case's multi-turn history replay
still runs sequentially within its own worker, so turns are never interleaved.
Answer and judge model calls are served from a content-keyed on-disk cache
(evals/cache.py) by default, so an incremental re-run after a one-prompt or
one-corpus change only pays for the cases that actually changed; `--no-cache`
disables this for runs that need to measure real model variance (FIX-04). CI
persists that cache across runs, so the cost of an unchanged suite is paid once
rather than once per pull request; `--refresh-cache` is the weekly cold run
that re-measures the provider and rewrites the stored answers (ADR 0022).

Variance runs (`--replicates N`, N > 1) score every case N times — a case's
replicate passes run sequentially inside its worker — and report a per-suite
mean pass rate with a Wilson 95% interval. A replicate run always bypasses the
cache (a cache-served replicate returns byte-identical answers and verdicts and
would measure zero variance) and cannot combine with `--since`/`--only-failed`
(reused cases contribute no fresh trials). A replicated multi-turn case replays
its history on every pass and pays for it every pass.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import os
import stat
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from assistant import config, corpus, fare_table
from assistant.answer import AnswerResult, answer_question
from assistant.facts import FareFact
from assistant.identity import SnapshotIdentity
from assistant.ingest import Chunk
from assistant.models import Model, get_model
from assistant.release_identity import (
    PROMPT_NAMES,
    ConfigIdentity,
    ReleaseIdentityError,
    build_config_identity,
    build_release_identity,
    load_release_descriptor,
    resolve_current_snapshot,
    verify_release_descriptor,
)
from assistant.retrieve import Retriever
from evals import attestation as eval_attestation
from evals import checks, judges
from evals.cache import CachingModel, EvalCache, case_content_key
from evals.checks import run_checks
from evals.stats import wilson_interval

_EFFECTIVE_ENVIRONMENT_JSON = "FPA_RELEASE_EFFECTIVE_ENVIRONMENT_JSON"
EVAL_RUN_BUNDLE_SCHEMA = "fare-assistant.eval-run-bundle.v1"
EVAL_RUN_BUNDLE_POINTER_SCHEMA = "fare-assistant.eval-run-bundle-pointer.v1"

# Environment-backed behavior that must not leak in from the shell when a
# deployment supplies an exact effective Lambda environment. Credentials are
# deliberately absent: model SDKs continue to use the caller's standard
# credential chain, while every answer-affecting setting is overlaid exactly.
_EVAL_BEHAVIOR_ENV = frozenset(
    {
        "AWS_REGION",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "FPA_ANSWER_MODEL",
        "FPA_DENSE",
        "FPA_DISABLED_DOC_IDS",
        "FPA_DOMAIN",
        "FPA_EMBED_ANCESTORS",
        "FPA_HISTORY_HMAC_KEY",
        "FPA_HISTORY_HMAC_KEY_ID",
        "FPA_JUDGE_MODEL",
        "FPA_OLLAMA_HOST",
        "FPA_PROVIDER",
        "FPA_STALENESS_BUDGET_DAYS",
    }
)


@dataclass(frozen=True)
class _CapturedFile:
    """One race-checked regular file read exactly once."""

    path: Path
    raw: bytes
    sha256: str

    @property
    def receipt(self) -> dict[str, object]:
        return {"sha256": self.sha256, "bytes": len(self.raw)}


@dataclass(frozen=True)
class _CapturedEvaluationInputs:
    chunks: tuple[Chunk, ...]
    facts: tuple[FareFact, ...]
    manifest: Mapping[str, object]
    prompts: Mapping[str, str]
    config_identity: ConfigIdentity
    snapshot_identity: SnapshotIdentity
    facts_identity: Mapping[str, object]
    gtfs_identity: Mapping[str, object]
    structured_fares_by_agency: Mapping[
        str,
        tuple[fare_table.StructuredFare, ...],
    ]


@dataclass(frozen=True)
class _RunBundle:
    run_dir: Path
    bundle_path: Path
    content_address: str
    summary_sha256: str
    results_sha256: str

    def pointer(self) -> dict[str, str]:
        return {
            "schema": EVAL_RUN_BUNDLE_POINTER_SCHEMA,
            "run_dir": str(self.run_dir),
            "bundle_path": str(self.bundle_path),
            "content_address": self.content_address,
            "summary_sha256": self.summary_sha256,
            "results_sha256": self.results_sha256,
        }


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_lines(raw: bytes, context: str) -> list[str]:
    """Split canonical JSONL only on ASCII LF, preserving Unicode separators."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise eval_attestation.EvalAttestationError(f"{context} must be valid UTF-8") from exc
    if not text:
        raise eval_attestation.EvalAttestationError(
            f"{context} must contain at least one JSON record"
        )
    if "\r" in text:
        raise eval_attestation.EvalAttestationError(f"{context} must use ASCII LF line endings")
    if not text.endswith("\n"):
        raise eval_attestation.EvalAttestationError(
            f"{context} must end with one ASCII LF after the final JSON record"
        )
    lines = text[:-1].split("\n")
    if any(not line for line in lines):
        raise eval_attestation.EvalAttestationError(
            f"{context} must contain exactly one JSON record per non-empty line"
        )
    return lines


def _stat_fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _capture_regular_file(
    path: Path,
    context: str,
    *,
    optional: bool = False,
) -> _CapturedFile | None:
    """Read one regular file and reject replacement or mutation during read."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        if optional:
            try:
                path.lstat()
            except FileNotFoundError:
                return None
        raise eval_attestation.EvalAttestationError(f"{context} is missing: {path}") from None
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} could not be inspected: {path}"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise eval_attestation.EvalAttestationError(f"{context} is not a regular file: {path}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} could not be opened safely: {path}"
        ) from exc

    chunks: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_fingerprint(opened) != _stat_fingerprint(
            before
        ):
            raise eval_attestation.EvalAttestationError(
                f"{context} changed while it was opened: {path}"
            )
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        after_read = os.fstat(descriptor)
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} could not be read completely: {path}"
        ) from exc
    finally:
        os.close(descriptor)

    try:
        after_path = path.lstat()
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} changed while it was read: {path}"
        ) from exc
    fingerprint = _stat_fingerprint(before)
    if _stat_fingerprint(after_read) != fingerprint or _stat_fingerprint(after_path) != fingerprint:
        raise eval_attestation.EvalAttestationError(f"{context} changed while it was read: {path}")
    raw = b"".join(chunks)
    if len(raw) != before.st_size:
        raise eval_attestation.EvalAttestationError(
            f"{context} changed size while it was read: {path}"
        )
    return _CapturedFile(path, raw, hashlib.sha256(raw).hexdigest())


def _regular_directory(path: Path, context: str, *, optional: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return False
        raise eval_attestation.EvalAttestationError(f"{context} is missing: {path}") from None
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} could not be inspected: {path}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise eval_attestation.EvalAttestationError(f"{context} is not a regular directory: {path}")
    return True


def _decode_utf8(captured: _CapturedFile, context: str) -> str:
    try:
        return captured.raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise eval_attestation.EvalAttestationError(
            f"{context} must be valid UTF-8: {captured.path}"
        ) from exc


def _prompt_version_from_text(name: str, prompt: str) -> str:
    lines = prompt.splitlines()
    if not lines:
        raise eval_attestation.EvalAttestationError(f"{name} prompt must contain a version header")
    version = lines[0].lstrip("# ").strip()
    if not version:
        raise eval_attestation.EvalAttestationError(
            f"{name} prompt version header must not be empty"
        )
    return version


def _parse_chunks(captured: _CapturedFile) -> tuple[Chunk, ...]:
    rows: list[Chunk] = []
    try:
        for line in _jsonl_lines(captured.raw, "captured chunks"):
            rows.append(Chunk(**json.loads(line)))
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise eval_attestation.EvalAttestationError("captured chunks are malformed") from exc
    if not rows:
        raise eval_attestation.EvalAttestationError("captured chunks must not be empty")
    return tuple(rows)


def _parse_facts(captured: _CapturedFile) -> tuple[FareFact, ...]:
    rows: list[FareFact] = []
    try:
        for line in _jsonl_lines(captured.raw, "captured facts"):
            rows.append(FareFact(**json.loads(line)))
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise eval_attestation.EvalAttestationError("captured facts are malformed") from exc
    return tuple(rows)


def _parse_manifest(captured: _CapturedFile) -> Mapping[str, object]:
    try:
        value = yaml.safe_load(captured.raw)
    except yaml.YAMLError as exc:
        raise eval_attestation.EvalAttestationError("captured manifest is malformed") from exc
    if not isinstance(value, Mapping):
        raise eval_attestation.EvalAttestationError("captured manifest must be an object")
    return value


def _facts_identity(captured: _CapturedFile) -> dict[str, object]:
    receipt = captured.receipt
    return {
        "schema": eval_attestation.FACTS_SCHEMA,
        "facts_version": eval_attestation.canonical_digest(
            eval_attestation.FACTS_SCHEMA,
            {"receipt": receipt},
        ),
        "receipt": receipt,
    }


def _structured_fares_from_bytes(
    agency: str,
    *,
    fare_attributes: bytes | None = None,
    fare_products: bytes | None = None,
    rider_categories: bytes | None = None,
) -> tuple[fare_table.StructuredFare, ...]:
    """Parse the exact captured fare bytes without reopening GTFS files."""

    def rows(raw: bytes | None) -> list[dict[str, str]]:
        if raw is None:
            return []
        return list(
            csv.DictReader(
                io.StringIO(raw.decode("utf-8-sig"), newline=""),
            )
        )

    categories: dict[str, fare_table.RiderCategory] = {}
    for row in rows(rider_categories):
        category_id = row.get("rider_category_id")
        if not category_id:
            continue
        categories[category_id] = fare_table.RiderCategory(
            id=category_id,
            name=(row.get("rider_category_name") or category_id).strip(),
            eligibility_url=(row.get("eligibility_url") or "").strip() or None,
        )

    fares: list[fare_table.StructuredFare] = []
    if fare_products is not None:
        for row in rows(fare_products):
            try:
                amount = Decimal(row["amount"])
            except (InvalidOperation, KeyError):
                continue
            try:
                product_id = row["fare_product_id"]
            except KeyError as exc:
                raise eval_attestation.EvalAttestationError(
                    f"captured GTFS v2 fare row is missing fare_product_id for {agency}"
                ) from exc
            fares.append(
                fare_table.StructuredFare(
                    agency=agency,
                    product=row.get("fare_product_name") or product_id,
                    amount=amount,
                    rider_category=categories.get(row.get("rider_category_id") or ""),
                )
            )
        return tuple(fares)

    if fare_attributes is not None:
        for row in rows(fare_attributes):
            try:
                amount = Decimal(row["price"])
            except (InvalidOperation, KeyError):
                continue
            try:
                fare_id = row["fare_id"]
            except KeyError as exc:
                raise eval_attestation.EvalAttestationError(
                    f"captured GTFS v1 fare row is missing fare_id for {agency}"
                ) from exc
            fares.append(
                fare_table.StructuredFare(
                    agency=agency,
                    product=fare_id,
                    amount=amount,
                    rider_category=None,
                )
            )
    return tuple(fares)


def _capture_gtfs_inputs(
    manifest: Mapping[str, object],
) -> tuple[
    dict[str, object],
    dict[str, tuple[fare_table.StructuredFare, ...]],
]:
    """Capture each GTFS byte used by deterministic fare checks exactly once."""

    feeds = eval_attestation._configured_feeds(manifest)
    root = config.RAW_DIR / "gtfs"
    if root.exists() or root.is_symlink():
        _regular_directory(root, "GTFS root")
    fares_by_agency = {}
    agencies = []
    for feed in feeds:
        agency = str(feed["agency"])
        agency_dir = root / agency
        files: list[dict[str, object]] = []
        raw_files: dict[str, bytes] = {}
        if _regular_directory(
            agency_dir,
            f"GTFS agency directory {agency}",
            optional=True,
        ):
            for member in eval_attestation.GTFS_LEGACY_CONSUMED_MEMBERS:
                captured = _capture_regular_file(
                    agency_dir / member,
                    f"GTFS member {agency}/{member}",
                    optional=True,
                )
                if captured is None:
                    continue
                raw_files[member] = captured.raw
                files.append({"path": f"{agency}/{member}", **captured.receipt})
        try:
            fares_by_agency[agency] = _structured_fares_from_bytes(
                agency,
                fare_attributes=raw_files.get("fare_attributes.txt"),
                fare_products=raw_files.get("fare_products.txt"),
                rider_categories=raw_files.get("rider_categories.txt"),
            )
        except (UnicodeError, csv.Error) as exc:
            raise eval_attestation.EvalAttestationError(
                f"captured GTFS fare inputs are malformed for {agency}"
            ) from exc
        agencies.append(
            {
                "agency": agency,
                "state": "legacy_extracted_only" if files else "unavailable",
                "files": files,
            }
        )

    payload = {
        "gtfs_feeds": feeds,
        "consumed_members": list(eval_attestation.GTFS_LEGACY_CONSUMED_MEMBERS),
        "agencies": agencies,
    }
    return (
        {
            "schema": eval_attestation.GTFS_LEGACY_INPUT_SCHEMA,
            "gtfs_input_version": eval_attestation.canonical_digest(
                eval_attestation.GTFS_LEGACY_INPUT_SCHEMA,
                payload,
            ),
            "consumed_members": list(eval_attestation.GTFS_LEGACY_CONSUMED_MEMBERS),
            "agencies": agencies,
        },
        fares_by_agency,
    )


def _capture_evaluation_inputs(
    *,
    cfg: config.Config,
    environment: Mapping[str, str],
) -> _CapturedEvaluationInputs:
    chunks_capture = _capture_regular_file(config.CHUNKS_PATH, "chunks")
    facts_capture = _capture_regular_file(config.FACTS_PATH, "facts")
    manifest_capture = _capture_regular_file(config.MANIFEST_PATH, "manifest")
    answer_schema_capture = _capture_regular_file(
        config.ANSWER_SCHEMA_PATH,
        "answer contract",
    )
    assert (
        chunks_capture is not None
        and facts_capture is not None
        and manifest_capture is not None
        and answer_schema_capture is not None
    )

    prompt_captures: dict[str, _CapturedFile] = {}
    for name in PROMPT_NAMES:
        captured = _capture_regular_file(
            config.PROMPTS_DIR / f"{name}.txt",
            f"{name} prompt",
        )
        assert captured is not None
        prompt_captures[name] = captured
    prompts = {
        name: _decode_utf8(captured, f"{name} prompt") for name, captured in prompt_captures.items()
    }
    chunks = _parse_chunks(chunks_capture)
    facts = _parse_facts(facts_capture)
    manifest = _parse_manifest(manifest_capture)
    config_identity = build_config_identity(
        environment,
        resolved_config=cfg,
        captured_prompt_bytes={name: captured.raw for name, captured in prompt_captures.items()},
        captured_answer_schema_bytes=answer_schema_capture.raw,
    )
    snapshot_identity = resolve_current_snapshot(
        chunks=chunks,
        manifest=manifest,
    )
    gtfs_identity, structured_fares_by_agency = _capture_gtfs_inputs(manifest)
    return _CapturedEvaluationInputs(
        chunks=chunks,
        facts=facts,
        manifest=manifest,
        prompts=prompts,
        config_identity=config_identity,
        snapshot_identity=snapshot_identity,
        facts_identity=_facts_identity(facts_capture),
        gtfs_identity=gtfs_identity,
        structured_fares_by_agency=structured_fares_by_agency,
    )


def _flatten_pairs(data: dict) -> list[dict]:
    """A sensitivity suite is written as `pairs:` of minimal-pair `variants:`.

    Flatten each variant into an ordinary case dict carrying a `pair_id` (the
    parent pair's id) and the pair's `boundary`, so every downstream consumer —
    validation, checks.py grading, credential gating, scoring — treats it as a
    normal case. The variants are re-grouped by `pair_id` only for the
    pair-level verdict (`pair_verdicts`), never for scoring.
    """
    cases = []
    for pair in data["pairs"]:
        for variant in pair["variants"]:
            variant["pair_id"] = pair["id"]
            variant.setdefault("boundary", pair.get("boundary"))
            cases.append(variant)
    return cases


def load_suites(only: str | None = None) -> list[dict]:
    suites = []
    for path in sorted(config.EVAL_SUITES_DIR.glob("*.yaml")):
        if only and path.stem != only:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "pairs" in data and "cases" not in data:
            data["cases"] = _flatten_pairs(data)
        for case in data["cases"]:
            case["suite"] = path.stem
        suites.append(data)
    return suites


def validate_cases(suites: list[dict]) -> None:
    seen: set[str] = set()
    required = {"id", "expected_behavior", "rationale"}
    for suite in suites:
        # Sensitivity suites carry minimal pairs that are flattened into cases.
        for pair in suite.get("pairs", []):
            if not pair.get("id"):
                raise SystemExit("sensitivity pair missing `id`")
            if not pair.get("boundary"):
                raise SystemExit(f"pair {pair['id']}: missing `boundary`")
            if len(pair.get("variants", [])) < 2:
                raise SystemExit(f"pair {pair['id']}: needs at least two variants")
        cases = suite.get("cases")
        if cases is None and "pairs" in suite:
            cases = _flatten_pairs(suite)
        for case in cases or []:
            # Auto-drafted skeletons (assistant.scaffold_agency) carry
            # `draft: true`. They have TODO questions and empty required_facts,
            # so they must never run or land in results: a human fills the facts
            # and removes the flag first. Refuse the whole run if any survive.
            if case.get("draft"):
                raise SystemExit(
                    f"case {case.get('id', '?')}: `draft: true` — fill it in and "
                    "remove the draft flag before running (see the scaffold checklist)"
                )
            missing = required - case.keys()
            if missing:
                raise SystemExit(f"case {case.get('id', '?')}: missing fields {sorted(missing)}")
            # A case is single-turn (`question`) or multi-turn (`turns`: a list
            # of questions, the last of which is the one under test).
            if "question" not in case and not case.get("turns"):
                raise SystemExit(f"case {case['id']}: needs `question` or `turns`")
            if case.get("turns") and len(case["turns"]) < 2:
                raise SystemExit(f"case {case['id']}: `turns` needs at least two questions")
            # `history`: a literal list of {q, a} pairs injected directly as the
            # follow-up's context (forged-history cases). It combines with a
            # single-turn `question` and is mutually exclusive with `turns`.
            if case.get("history") is not None:
                history = case["history"]
                if not isinstance(history, list) or not history:
                    raise SystemExit(f"case {case['id']}: `history` must be a non-empty list")
                for pair in history:
                    if not (
                        isinstance(pair, dict)
                        and isinstance(pair.get("q"), str)
                        and isinstance(pair.get("a"), str)
                    ):
                        raise SystemExit(
                            f"case {case['id']}: each `history` entry needs string `q` and `a`"
                        )
                if case.get("turns"):
                    raise SystemExit(
                        f"case {case['id']}: `history` combines with `question`, not `turns`"
                    )
                if "question" not in case:
                    raise SystemExit(f"case {case['id']}: `history` requires a `question`")
            if case["id"] in seen:
                raise SystemExit(f"duplicate case id: {case['id']}")
            seen.add(case["id"])
            if case["expected_behavior"] not in ("answer", "partial", "refuse_redirect"):
                raise SystemExit(f"case {case['id']}: bad expected_behavior")


def pair_verdicts(records: list[dict]) -> dict[str, bool]:
    """Group scored records by `pair_id` and return {pair_id: passed}.

    A minimal-pair boundary case only counts as distinguished if *every*
    variant passed — the per-variant required_facts / forbidden_content prove
    the answer actually changed (or held) across the boundary. One variant
    passing on boilerplate is not evidence of discrimination, so a mixed
    pass/fail pair reports failed.
    """
    grouped: dict[str, list[bool]] = {}
    for r in records:
        pid = r.get("pair_id")
        # A pair with a withheld variant proves nothing about discrimination:
        # one side never got its evidence. Drop the whole pair rather than
        # judge the boundary on the half that ran.
        if not pid or r.get("not_applicable"):
            continue
        grouped.setdefault(pid, []).append(bool(r["passed"]))
    incomplete = {r.get("pair_id") for r in records if r.get("pair_id") and r.get("not_applicable")}
    return {pid: all(v) for pid, v in grouped.items() if pid not in incomplete}


def _have_credentials(provider: str) -> bool:
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "bedrock":
        # Standard AWS credential chain, in the order we expect it here:
        # SSO profile (~/.aws/config after `aws sso login`), OIDC web
        # identity (GitHub Actions federation), env keys, or a shared
        # credentials file. An instance role is not detectable cheaply;
        # set FPA_ASSUME_AWS_CREDS=1 to force a live run.
        return bool(
            os.environ.get("AWS_PROFILE")
            or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("FPA_ASSUME_AWS_CREDS")
            or (Path.home() / ".aws" / "config").exists()
            or (Path.home() / ".aws" / "credentials").exists()
        )
    if provider == "local":
        # No credentials to check — the analogous question is "is the Ollama
        # server up." A quick, short-timeout probe; any failure (not
        # running, wrong FPA_OLLAMA_HOST) reads as "not available" so the
        # normal --offline fallback below applies instead of hanging.
        import httpx

        host = config.resolve_provider_transport("local").base_url
        assert host is not None
        try:
            return httpx.get(f"{host}/api/version", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False
    return provider == "mock"


def _effective_eval_environment(
    supplied: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve one exact, secret-bearing environment without serializing it.

    The deployer passes Lambda's final ``{"Variables": ...}`` object through
    ``FPA_RELEASE_EFFECTIVE_ENVIRONMENT_JSON``. The decoded values may include
    a history-signing secret, so this function never logs or returns a
    secret-free "summary" masquerading as the real configuration; callers use
    it only in memory and the attestation records the derived opaque key ID.
    """
    if supplied is not None:
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in supplied.items()):
            raise SystemExit("effective eval environment must contain only strings")
        return dict(supplied)

    encoded = os.environ.get(_EFFECTIVE_ENVIRONMENT_JSON)
    if encoded is None:
        return dict(os.environ)
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{_EFFECTIVE_ENVIRONMENT_JSON} must contain valid JSON") from exc
    if isinstance(decoded, Mapping) and set(decoded) == {"Variables"}:
        decoded = decoded["Variables"]
    if not isinstance(decoded, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in decoded.items()
    ):
        raise SystemExit(f"{_EFFECTIVE_ENVIRONMENT_JSON} must contain a string environment mapping")
    values = dict(decoded)
    values["AWS_REGION"] = os.environ.get("AWS_REGION", config.DEFAULT_AWS_REGION)
    return values


@contextmanager
def _environment_overlay(values: Mapping[str, str]) -> Iterator[None]:
    """Apply exact behavior settings for one run, then restore the process."""
    touched = set(values) | set(_EVAL_BEHAVIOR_ENV)
    prior = {key: os.environ.get(key) for key in touched}
    for key in touched:
        if key in values:
            os.environ[key] = values[key]
        else:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _cost_block(cfg: config.Config, usage: dict[str, list[int]]) -> dict:
    """Exact token totals per model plus an estimated USD cost at list rates."""
    a_in, a_out, a_create, a_read = usage["answer"]
    j_in, j_out, j_create, j_read = usage["judge"]
    a_usd = config.estimate_cost_usd(
        cfg.models.answer_model,
        a_in,
        a_out,
        provider=cfg.models.provider,
        cache_creation_input_tokens=a_create,
        cache_read_input_tokens=a_read,
    )
    j_usd = config.estimate_cost_usd(
        cfg.models.judge_model,
        j_in,
        j_out,
        provider=cfg.models.provider,
        cache_creation_input_tokens=j_create,
        cache_read_input_tokens=j_read,
    )
    unpriced = [
        model
        for model, value in (
            (cfg.models.answer_model, a_usd),
            (cfg.models.judge_model, j_usd),
        )
        if value is None
    ]
    return {
        "answer_model": {
            "input_tokens": a_in,
            "output_tokens": a_out,
            "cache_creation_input_tokens": a_create,
            "cache_read_input_tokens": a_read,
            "est_usd": round(a_usd, 4) if a_usd is not None else None,
        },
        "judge_model": {
            "input_tokens": j_in,
            "output_tokens": j_out,
            "cache_creation_input_tokens": j_create,
            "cache_read_input_tokens": j_read,
            "est_usd": round(j_usd, 4) if j_usd is not None else None,
        },
        "total_tokens": a_in + a_out + j_in + j_out,
        "total_est_usd": (
            round(a_usd + j_usd, 4) if a_usd is not None and j_usd is not None else None
        ),
        "unpriced_models": unpriced,
    }


def _resolve_reference_run(name: str | None) -> Path | None:
    """The run directory `--since`/`--only-failed` compares against: the named
    run, or (name omitted) the most recent existing run. `None` if there is no
    prior run to compare against yet."""
    if name:
        run_dir = config.EVAL_RUNS_DIR / name
        if not run_dir.exists():
            raise SystemExit(f"no such run: {run_dir}")
        return run_dir
    if not config.EVAL_RUNS_DIR.exists():
        return None
    candidates = sorted(p for p in config.EVAL_RUNS_DIR.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _load_records(run_dir: Path) -> dict[str, dict]:
    results_path = run_dir / "results.jsonl"
    if not results_path.exists():
        return {}
    records = (
        json.loads(line) for line in _jsonl_lines(results_path.read_bytes(), "results.jsonl")
    )
    return {r["case_id"]: r for r in records}


def _validate_result_provenance(
    record: Mapping[str, object],
    *,
    case_id: str,
    case_semantics_version: str,
    run_context_version: str,
) -> None:
    if record.get("case_id") != case_id:
        raise eval_attestation.EvalAttestationError(
            f"result case_id does not match ordered case {case_id}"
        )
    if record.get("case_semantics_version") != case_semantics_version:
        raise eval_attestation.EvalAttestationError(
            f"result case semantics do not match ordered case {case_id}"
        )
    if record.get("run_context_version") != run_context_version:
        raise eval_attestation.EvalAttestationError(
            f"result run context does not match ordered case {case_id}"
        )
    for field in ("answer_models_served", "judge_models_served"):
        value = record.get(field)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or value != sorted(set(value))
        ):
            raise eval_attestation.EvalAttestationError(
                f"result {field} must be a sorted unique string array for {case_id}"
            )


def _ordered_cases_for_results(
    run_dir: Path,
) -> tuple[list[dict], list[dict[str, object]]]:
    try:
        records = [
            json.loads(line)
            for line in _jsonl_lines(
                (run_dir / "results.jsonl").read_bytes(),
                "results.jsonl",
            )
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise eval_attestation.EvalAttestationError(
            "results.jsonl is missing or malformed"
        ) from exc
    if not records or any(not isinstance(record, dict) for record in records):
        raise eval_attestation.EvalAttestationError(
            "results.jsonl must contain ordered result objects"
        )
    suites = load_suites()
    validate_cases(suites)
    cases_by_id = {case["id"]: case for selected in suites for case in selected["cases"]}
    result_ids = [record.get("case_id") for record in records]
    if any(not isinstance(case_id, str) for case_id in result_ids) or len(result_ids) != len(
        set(result_ids)
    ):
        raise eval_attestation.EvalAttestationError("results.jsonl case IDs must be unique strings")
    try:
        ordered_cases = [cases_by_id[str(case_id)] for case_id in result_ids]
    except KeyError as exc:
        raise eval_attestation.EvalAttestationError(
            f"results.jsonl names an unknown current case: {exc.args[0]}"
        ) from exc
    return ordered_cases, records


def _served_model_unions(
    records: Sequence[Mapping[str, object]],
) -> dict[str, list[str]]:
    unions: dict[str, list[str]] = {}
    for kind in ("answer", "judge"):
        field = f"{kind}_models_served"
        values: set[str] = set()
        for record in records:
            models = record.get(field)
            if not isinstance(models, list):
                raise eval_attestation.EvalAttestationError(f"result {field} must be an array")
            values.update(model for model in models if isinstance(model, str) and model)
        unions[kind] = sorted(values)
    return unions


def _rfc3339_utc(value: datetime) -> str:
    """Return one canonical, second-precision UTC timestamp."""
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _allocate_run_directory(started_at: datetime) -> Path:
    """Create a collision-safe run directory without changing the run timestamp."""
    base = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    config.EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *(f"-{index:02d}" for index in range(1, 100))):
        candidate = config.EVAL_RUNS_DIR / f"{base}{suffix}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise SystemExit(f"could not allocate a unique evaluation run directory for {base}")


def _promotion_reasons(
    attestation: Mapping[str, object],
    *,
    promotion_requested: bool,
    gates_passed: bool,
) -> list[str]:
    """Derive every promotion rejection reason from attested facts."""
    subject = attestation["subject"]
    promotion = attestation["promotion"]
    assert isinstance(subject, Mapping)
    assert isinstance(promotion, Mapping)
    reasons: list[str] = []
    if not promotion_requested:
        reasons.append("not_promotion_run")
    if subject["source_state"] != "clean":
        reasons.append("source_dirty")
    if not subject["descriptor_verified"]:
        reasons.append("descriptor_unverified")
    if not promotion["live"]:
        reasons.append("not_live")
    if not promotion["uncached"]:
        reasons.append("cache_enabled")
    if not promotion["judges_ran"]:
        reasons.append("judges_not_run")
    if not gates_passed:
        reasons.append("gates_pending")
    return reasons


def _atomic_replace_file(path: Path, raw: bytes, context: str) -> None:
    if path.is_symlink():
        raise eval_attestation.EvalAttestationError(f"refusing to replace a symlinked {context}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _regular_directory(path.parent, f"{context} parent")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(f"could not write {context}: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_new_bundle_file(path: Path, raw: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            f"could not write evaluation bundle file: {path}"
        ) from exc


def _write_summary(run_dir: Path, summary: Mapping[str, object]) -> None:
    _atomic_replace_file(
        run_dir / "summary.json",
        (json.dumps(summary, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        "evaluation summary",
    )


def _validate_existing_bundle(
    bundle_path: Path,
    *,
    summary: _CapturedFile,
    results: _CapturedFile,
    manifest_bytes: bytes,
) -> None:
    _regular_directory(bundle_path, "evaluation bundle")
    if stat.S_IMODE(bundle_path.lstat().st_mode) != 0o555:
        raise eval_attestation.EvalAttestationError(
            "content-addressed evaluation bundle directory must be read-only"
        )
    try:
        entries = {entry.name for entry in bundle_path.iterdir()}
    except OSError as exc:
        raise eval_attestation.EvalAttestationError(
            "evaluation bundle could not be inspected"
        ) from exc
    expected = {"bundle.json", "results.jsonl", "summary.json"}
    if entries != expected:
        raise eval_attestation.EvalAttestationError(
            "content-addressed evaluation bundle has conflicting entries"
        )
    expected_bytes = {
        "bundle.json": manifest_bytes,
        "results.jsonl": results.raw,
        "summary.json": summary.raw,
    }
    for name, raw in expected_bytes.items():
        captured = _capture_regular_file(
            bundle_path / name,
            f"evaluation bundle {name}",
        )
        assert captured is not None
        if captured.raw != raw:
            raise eval_attestation.EvalAttestationError(
                "content-addressed evaluation bundle conflicts with its address"
            )


def _publish_run_bundle(run_dir: Path) -> _RunBundle:
    """Atomically publish closed, content-addressed post-gate eval evidence."""

    _regular_directory(run_dir, "evaluation run directory")
    absolute_run_dir = run_dir.resolve(strict=True)
    summary = _capture_regular_file(
        absolute_run_dir / "summary.json",
        "final evaluation summary",
    )
    results = _capture_regular_file(
        absolute_run_dir / "results.jsonl",
        "final evaluation results",
    )
    assert summary is not None and results is not None
    try:
        summary_payload = json.loads(summary.raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise eval_attestation.EvalAttestationError(
            "final evaluation summary is malformed"
        ) from exc
    if (
        not isinstance(summary_payload, dict)
        or summary_payload.get("gate_status") not in {"passed", "failed"}
        or summary_payload.get("results_sha256") != results.sha256
    ):
        raise eval_attestation.EvalAttestationError(
            "final evaluation summary does not bind the exact results"
        )
    manifest_bytes = _canonical_json_bytes(
        {
            "schema": EVAL_RUN_BUNDLE_SCHEMA,
            "summary_sha256": summary.sha256,
            "results_sha256": results.sha256,
        }
    )
    content_address = hashlib.sha256(manifest_bytes).hexdigest()
    bundles_root = absolute_run_dir / "bundles"
    if bundles_root.exists() or bundles_root.is_symlink():
        _regular_directory(bundles_root, "evaluation bundles directory")
    else:
        try:
            bundles_root.mkdir(mode=0o755)
        except OSError as exc:
            raise eval_attestation.EvalAttestationError(
                "evaluation bundles directory could not be created"
            ) from exc
    bundle_path = bundles_root / content_address
    if bundle_path.exists() or bundle_path.is_symlink():
        _validate_existing_bundle(
            bundle_path,
            summary=summary,
            results=results,
            manifest_bytes=manifest_bytes,
        )
    else:
        try:
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f".{content_address}.",
                    dir=bundles_root,
                )
            )
        except OSError as exc:
            raise eval_attestation.EvalAttestationError(
                "could not stage evaluation bundle"
            ) from exc
        try:
            _write_new_bundle_file(temporary / "summary.json", summary.raw)
            _write_new_bundle_file(temporary / "results.jsonl", results.raw)
            _write_new_bundle_file(temporary / "bundle.json", manifest_bytes)
            descriptor = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(temporary, 0o555)
            try:
                os.rename(temporary, bundle_path)
            except FileExistsError:
                _validate_existing_bundle(
                    bundle_path,
                    summary=summary,
                    results=results,
                    manifest_bytes=manifest_bytes,
                )
            if bundle_path.exists():
                os.chmod(bundle_path, 0o555)
            directory = os.open(bundles_root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise eval_attestation.EvalAttestationError(
                "could not publish evaluation bundle atomically"
            ) from exc
        finally:
            if temporary.exists():
                os.chmod(temporary, 0o755)
                for name in ("bundle.json", "results.jsonl", "summary.json"):
                    candidate = temporary / name
                    if candidate.exists():
                        candidate.unlink()
                temporary.rmdir()

    return _RunBundle(
        run_dir=absolute_run_dir,
        bundle_path=bundle_path,
        content_address=content_address,
        summary_sha256=summary.sha256,
        results_sha256=results.sha256,
    )


def _write_run_path_pointer(output: Path, bundle: _RunBundle) -> None:
    """Durably publish one closed canonical bundle pointer."""

    try:
        _atomic_replace_file(
            output,
            _canonical_json_bytes(bundle.pointer()),
            "--run-path-output",
        )
    except eval_attestation.EvalAttestationError as exc:
        raise SystemExit(str(exc)) from exc


def _finalize_run_gates(run_dir: Path, *, passed: bool) -> None:
    """Atomically restate the eval attestation after all post-run gates."""
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    current = summary["attestation"]
    if type(summary.get("promotion_requested")) is not bool:
        raise eval_attestation.EvalAttestationError("summary promotion_requested must be a boolean")
    promotion_requested = summary["promotion_requested"]
    ordered_cases, records = _ordered_cases_for_results(run_dir)
    ordered_case_manifest = eval_attestation.case_manifest(ordered_cases)
    suite_version = eval_attestation.suite_version(ordered_cases)
    promotion = dict(current["promotion"])
    promotion["gates_passed"] = passed
    promotion["reasons"] = _promotion_reasons(
        current,
        promotion_requested=promotion_requested,
        gates_passed=passed,
    )
    if not passed:
        promotion["reasons"] = [
            "gates_failed" if reason == "gates_pending" else reason
            for reason in promotion["reasons"]
        ]
    promotion["eligible"] = passed and not promotion["reasons"]
    protocol = {
        key: value for key, value in current["protocol"].items() if key != "protocol_version"
    }
    finalized = eval_attestation.build_attestation(
        subject=current["subject"],
        suite_version=suite_version,
        case_manifest=ordered_case_manifest,
        facts_version=current["evidence"]["facts_version"],
        gtfs_input_version=current["evidence"]["gtfs_input_version"],
        protocol=protocol,
        promotion=promotion,
    )
    if finalized["context_version"] != current["context_version"]:
        raise eval_attestation.EvalAttestationError(
            "evaluation cases changed before gate finalization"
        )
    semantics_by_id = {
        entry["case_id"]: entry["case_semantics_version"] for entry in ordered_case_manifest
    }
    for record in records:
        case_id = str(record["case_id"])
        _validate_result_provenance(
            record,
            case_id=case_id,
            case_semantics_version=semantics_by_id[case_id],
            run_context_version=str(finalized["context_version"]),
        )
    served_models = _served_model_unions(records)
    if summary.get("served_models") != served_models:
        raise eval_attestation.EvalAttestationError(
            "summary served_models do not match exact result unions"
        )
    summary["attestation"] = finalized
    summary["gate_status"] = "passed" if passed else "failed"
    _write_summary(run_dir, summary)


def _verify_promotion_inputs_unchanged(
    run_dir: Path,
    *,
    release_descriptor: Path,
) -> None:
    """Recompute the complete pre-call context after a long promotion run."""
    try:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        current = summary["attestation"]
        values = _effective_eval_environment()
        with _environment_overlay(values):
            cfg = config.Config.from_environment(values)
            captured_inputs = _capture_evaluation_inputs(
                cfg=cfg,
                environment=values,
            )
            config_identity = captured_inputs.config_identity
            snapshot_identity = captured_inputs.snapshot_identity
            source_status = eval_attestation.git_source_status(config.REPO_ROOT)
            descriptor = load_release_descriptor(release_descriptor)
            verify_release_descriptor(
                descriptor,
                environment=values,
                resolved_config=cfg,
                require_environment=True,
                config_identity=config_identity,
                chunks=captured_inputs.chunks,
            )
            if source_status != {
                "source_state": "clean",
                "head_revision": descriptor.source_revision,
                "source_revision": descriptor.source_revision,
            }:
                raise ReleaseIdentityError("Git source state changed during promotion evaluation")
            if (
                snapshot_identity.content_version != descriptor.content_version
                or snapshot_identity.snapshot_version != descriptor.snapshot_version
            ):
                raise ReleaseIdentityError("source snapshot changed during promotion evaluation")
            if corpus.corpus_version(list(captured_inputs.chunks)) != descriptor.corpus_version:
                raise ReleaseIdentityError(
                    "compatibility corpus changed during promotion evaluation"
                )

            suites = load_suites()
            validate_cases(suites)
            ordered_cases = [case for selected in suites for case in selected["cases"]]
            ordered_case_manifest = eval_attestation.case_manifest(ordered_cases)
            facts_input = captured_inputs.facts_identity
            gtfs_input = captured_inputs.gtfs_identity
            evaluator_input = eval_attestation.evaluator_identity(config.REPO_ROOT)
            protocol = {
                key: value
                for key, value in current["protocol"].items()
                if key != "protocol_version"
            }
            if protocol.get("evaluator_version") != evaluator_input["evaluator_version"]:
                raise eval_attestation.EvalAttestationError(
                    "evaluator source changed during promotion evaluation"
                )
            protocol["provider"] = cfg.models.provider
            protocol["requested_models"] = {
                "answer": cfg.models.answer_model,
                "judge": cfg.models.judge_model,
            }
            protocol["prompt_versions"] = {
                name: _prompt_version_from_text(
                    name,
                    captured_inputs.prompts[name],
                )
                for name in PROMPT_NAMES
            }
            protocol["evaluator_version"] = evaluator_input["evaluator_version"]
            rebuilt = eval_attestation.build_attestation(
                subject={
                    **source_status,
                    "config_version": descriptor.config_version,
                    "content_version": descriptor.content_version,
                    "snapshot_version": descriptor.snapshot_version,
                    "release_version": descriptor.release_version,
                    "corpus_version": descriptor.corpus_version,
                    "descriptor_verified": True,
                },
                suite_version=eval_attestation.suite_version(ordered_cases),
                case_manifest=ordered_case_manifest,
                facts_version=str(facts_input["facts_version"]),
                gtfs_input_version=str(gtfs_input["gtfs_input_version"]),
                protocol=protocol,
                promotion=current["promotion"],
                config_identity=config_identity,
                snapshot_identity=snapshot_identity,
            )
        if rebuilt != current:
            raise eval_attestation.EvalAttestationError(
                "evaluation inputs changed during the promotion run"
            )
        results = (run_dir / "results.jsonl").read_bytes()
        if hashlib.sha256(results).hexdigest() != summary.get("results_sha256"):
            raise eval_attestation.EvalAttestationError(
                "evaluation results changed after the run completed"
            )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        ReleaseIdentityError,
        eval_attestation.EvalAttestationError,
        yaml.YAMLError,
    ) as exc:
        raise SystemExit(f"promotion inputs failed post-run verification: {exc}") from exc


def _run_case(
    case: dict,
    *,
    answer_model: Model,
    judge_model: Model,
    retriever: Retriever,
    cfg: config.Config,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list] | None,
    structured_fares_by_agency: Mapping[
        str,
        Sequence[fare_table.StructuredFare],
    ],
    answer_system_prompt: str,
    answer_user_prompt: str,
    groundedness_prompt: str,
    helpfulness_prompt: str,
    run_judges: bool,
) -> tuple[dict, dict[str, list[int]]]:
    """Execute one case (including its multi-turn history replay, sequentially
    within this call so turns are never interleaved with another case's) and
    return its trace record plus the token usage it spent."""
    usage = {"answer": [0, 0, 0, 0], "judge": [0, 0, 0, 0]}
    answer_models_served: set[str] = set()
    judge_history: list[tuple[str, str]] | None = None
    if case.get("turns"):
        # Multi-turn: replay earlier turns to build history, then the final
        # turn is the one under test. Earlier turns' tokens count.
        history: list[tuple[str, str]] = []
        for q in case["turns"][:-1]:
            prior = answer_question(
                q,
                history=history or None,
                model=answer_model,
                retriever=retriever,
                cfg=cfg,
                system_prompt=answer_system_prompt,
                answer_user_prompt=answer_user_prompt,
            )
            usage["answer"][0] += prior.input_tokens
            usage["answer"][1] += prior.output_tokens
            usage["answer"][2] += prior.cache_creation_input_tokens
            usage["answer"][3] += prior.cache_read_input_tokens
            if prior.model:
                answer_models_served.add(prior.model)
            history.append((q, prior.answer))
        question = case["turns"][-1]
        result: AnswerResult = answer_question(
            question,
            history=history or None,
            model=answer_model,
            retriever=retriever,
            cfg=cfg,
            system_prompt=answer_system_prompt,
            answer_user_prompt=answer_user_prompt,
        )
        judge_history = history
    elif case.get("history"):
        injected = [(h["q"], h["a"]) for h in case["history"]]
        question = case["question"]
        result = answer_question(
            question,
            history=injected,
            model=answer_model,
            retriever=retriever,
            cfg=cfg,
            system_prompt=answer_system_prompt,
            answer_user_prompt=answer_user_prompt,
        )
        judge_history = injected
    else:
        question = case["question"]
        result = answer_question(
            question,
            model=answer_model,
            retriever=retriever,
            cfg=cfg,
            system_prompt=answer_system_prompt,
            answer_user_prompt=answer_user_prompt,
        )
    checks = run_checks(
        case,
        result,
        corpus_doc_ids,
        facts_by_doc,
        structured_fares_by_agency,
    )
    # A case whose supporting document the operator has disabled was never
    # given the evidence it was written against: `answer.answer_question`
    # fails closed on a disabled source (the containment path added by
    # "restore rider trust boundaries"), so the assistant returns the
    # no-support message no matter how good it is. Scoring that as a failure
    # measures the source policy, not the assistant. Record it as *not
    # applicable* under this source policy, keep the trace, and spend no judge
    # tokens on a verdict that cannot mean anything. When the source is
    # reviewed and re-enabled the case returns to the denominator by itself.
    disabled_sources = sorted(
        flag.split(":", 1)[1] for flag in result.guard_flags if flag.startswith("source_disabled:")
    )
    verdicts = []
    if disabled_sources:
        record = _case_record(
            case,
            question=question,
            result=result,
            checks=checks,
            verdicts=verdicts,
            answer_models_served=answer_models_served,
            passed=False,
        )
        record["not_applicable"] = True
        record["not_applicable_reason"] = "source_disabled:" + ",".join(disabled_sources)
        usage["answer"][0] += result.input_tokens
        usage["answer"][1] += result.output_tokens
        usage["answer"][2] += result.cache_creation_input_tokens
        usage["answer"][3] += result.cache_read_input_tokens
        return record, usage
    if run_judges:
        if case["expected_behavior"] in ("answer", "partial") and result.kind == "answered":
            verdicts.append(
                judges.judge_groundedness(
                    judge_model,
                    result,
                    cfg,
                    history=judge_history,
                    system_prompt=groundedness_prompt,
                )
            )
        verdicts.append(
            judges.judge_helpfulness(
                judge_model,
                result,
                case["expected_behavior"],
                cfg,
                history=judge_history,
                rationale=case["rationale"],
                system_prompt=helpfulness_prompt,
            )
        )
    passed = all(c.passed for c in checks) and all(v.passed for v in verdicts)
    usage["answer"][0] += result.input_tokens
    usage["answer"][1] += result.output_tokens
    usage["answer"][2] += result.cache_creation_input_tokens
    usage["answer"][3] += result.cache_read_input_tokens
    for v in verdicts:
        usage["judge"][0] += v.input_tokens
        usage["judge"][1] += v.output_tokens
        usage["judge"][2] += v.cache_creation_input_tokens
        usage["judge"][3] += v.cache_read_input_tokens
    record = _case_record(
        case,
        question=question,
        result=result,
        checks=checks,
        verdicts=verdicts,
        answer_models_served=answer_models_served,
        passed=passed,
    )
    return record, usage


def _case_record(
    case: dict,
    *,
    question: str,
    result,
    checks,
    verdicts,
    answer_models_served: set[str],
    passed: bool,
) -> dict:
    """The trace record for one scored case. Shared by the scored path and the
    not-applicable (disabled-source) path so both write the same shape."""
    if result.model:
        answer_models_served.add(result.model)
    return {
        "case_id": case["id"],
        "suite": case["suite"],
        "pair_id": case.get("pair_id"),
        "mirror_of": case.get("mirror_of"),
        "language": case.get("language", "en"),
        "expected_behavior": case["expected_behavior"],
        "question": question,
        "turns": case.get("turns"),
        "history": case.get("history"),
        "rationale": case["rationale"],
        "answer": result.answer,
        "answer_model_served": result.model or None,
        # Includes every replayed prior turn as well as the scored final turn.
        # A provider can route a requested alias/profile to a different served
        # model between calls; retaining the complete set keeps that visible.
        "answer_models_served": sorted(answer_models_served),
        "kind": result.kind,
        "guard_flags": result.guard_flags,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_creation_input_tokens": result.cache_creation_input_tokens,
        "cache_read_input_tokens": result.cache_read_input_tokens,
        "raw_model_answer": result.raw_model_answer,
        "citations": [asdict(c) for c in result.citations],
        # doc_id/agency/doc_title/url/fetch_date (issue #142): the human
        # relabeling worksheet and the report's failure traces are both
        # rendered from this persisted record, not from the live AnswerResult,
        # so a reviewer checking a dated claim needs the same provenance line
        # assistant.answer._format_passages puts in front of the answer model
        # and evals.judges._passages_block puts in front of the judge. Without
        # it, a reviewer sees the same undated passages that produced the
        # fresh-001 judge miscalibration this worksheet exists to catch.
        "passages": [
            {
                "chunk_id": sc.chunk.chunk_id,
                "doc_id": sc.chunk.doc_id,
                "agency": sc.chunk.agency,
                "doc_title": sc.chunk.doc_title,
                "url": sc.chunk.url,
                "fetch_date": sc.chunk.fetch_date,
                "section": sc.chunk.section,
                "score": round(sc.score, 2),
                "text": sc.chunk.text[:600],
            }
            for sc in result.passages
        ],
        "checks": [asdict(c) for c in checks],
        "judges": [asdict(v) for v in verdicts],
        "judge_models_served": sorted({v.model for v in verdicts if v.model}),
        "passed": passed,
    }


def run(
    *,
    smoke: bool = False,
    offline: bool = False,
    suite: str | None = None,
    jobs: int = 4,
    use_cache: bool = True,
    refresh_cache: bool = False,
    only_failed: bool = False,
    since: str | None = None,
    replicates: int = 1,
    promotion: bool = False,
    release_descriptor: Path | None = None,
    effective_environment: Mapping[str, str] | None = None,
) -> Path:
    """Run one evaluation under a single explicit environment snapshot."""
    if promotion:
        incompatible = []
        if smoke:
            incompatible.append("--smoke")
        if offline:
            incompatible.append("--offline")
        if suite is not None:
            incompatible.append("--suite")
        if use_cache:
            incompatible.append("cache enabled (pass --no-cache)")
        if only_failed:
            incompatible.append("--only-failed")
        if since is not None:
            incompatible.append("--since")
        if replicates != 1:
            incompatible.append("--replicates")
        if release_descriptor is None:
            incompatible.append("missing --release-descriptor")
        if incompatible:
            raise SystemExit(
                "promotion evaluation requires the complete fresh live suite: "
                + ", ".join(incompatible)
            )

    values = _effective_eval_environment(effective_environment)
    if offline:
        values.update(
            {
                "FPA_PROVIDER": "mock",
                "FPA_ANSWER_MODEL": "mock",
                "FPA_JUDGE_MODEL": "mock",
            }
        )
    with _environment_overlay(values):
        return _run_resolved(
            smoke=smoke,
            offline=offline,
            suite=suite,
            jobs=jobs,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
            only_failed=only_failed,
            since=since,
            replicates=replicates,
            promotion=promotion,
            release_descriptor=release_descriptor,
            effective_environment=values,
        )


def _run_resolved(
    *,
    smoke: bool,
    offline: bool,
    suite: str | None,
    jobs: int,
    use_cache: bool,
    refresh_cache: bool,
    only_failed: bool,
    since: str | None,
    replicates: int,
    promotion: bool,
    release_descriptor: Path | None,
    effective_environment: Mapping[str, str],
) -> Path:
    if replicates < 1:
        raise SystemExit("--replicates must be >= 1")
    if refresh_cache and not use_cache:
        raise SystemExit(
            "--refresh-cache cannot combine with --no-cache: refreshing means "
            "re-measuring the provider and then storing the result, which a "
            "disabled cache has nowhere to put"
        )
    if refresh_cache and (only_failed or since):
        raise SystemExit(
            "--refresh-cache cannot combine with --since/--only-failed: a "
            "cold re-measurement must call the provider for every case, and "
            "reused cases call it for none"
        )
    if replicates > 1:
        if only_failed or since:
            raise SystemExit(
                "--replicates cannot combine with --since/--only-failed: a variance "
                "run must score every case fresh (reused cases contribute no trials)"
            )
        # A cache-served replicate returns byte-identical answers and verdicts
        # and would measure zero variance, so replicate runs always bypass the
        # answer/judge cache (equivalent to --no-cache). A replicate run also
        # measures variance rather than a canonical answer, so it must not
        # overwrite the stored entries either.
        use_cache = False
        refresh_cache = False
    # Resolve provider/retrieval choices from one explicit environment snapshot
    # instead of letting nested dataclass factories observe different ambient
    # values at different moments. The offline mock override lives in the
    # `run()` wrapper, which injects FPA_PROVIDER=mock (etc.) into this same
    # environment snapshot before it reaches here.
    cfg = config.Config.from_environment(effective_environment)
    if cfg.models.provider != "mock":
        assert cfg.models.judge_model != cfg.models.answer_model, (
            "judge model must differ from answer model"
        )

    have_key = not offline and _have_credentials(cfg.models.provider)
    if not offline and not have_key:
        if promotion:
            raise SystemExit(
                f"promotion evaluation requires credentials for provider "
                f"{cfg.models.provider!r}; offline fallback is forbidden"
            )
        print(
            f"No credentials for provider '{cfg.models.provider}': falling back to "
            "--offline (deterministic checks only).",
            file=sys.stderr,
        )
        return run(
            smoke=smoke,
            offline=True,
            suite=suite,
            jobs=jobs,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
            only_failed=only_failed,
            since=since,
            replicates=replicates,
            promotion=False,
            effective_environment=effective_environment,
        )

    suites = load_suites(suite)
    if not suites:
        raise SystemExit("no suites found")
    validate_cases(suites)
    check_mirrors()
    check_pairs()

    started_at = datetime.now(UTC)
    started = time.monotonic()
    try:
        captured_inputs = _capture_evaluation_inputs(
            cfg=cfg,
            environment=effective_environment,
        )
    except (
        OSError,
        ReleaseIdentityError,
        eval_attestation.EvalAttestationError,
        yaml.YAMLError,
    ) as exc:
        raise SystemExit(f"could not capture exact evaluation inputs: {exc}") from exc
    chunks = list(captured_inputs.chunks)
    corpus_doc_ids = {c.doc_id for c in chunks}
    corpus_version = corpus.corpus_version(chunks)
    facts_by_doc: dict[str, list] = collections.defaultdict(list)
    for fact in captured_inputs.facts:
        facts_by_doc[fact.doc_id].append(fact)
    retriever = Retriever(chunks, cfg.retrieval)
    run_judges = have_key and cfg.models.provider != "mock"
    live = not offline and have_key and cfg.models.provider != "mock"
    if promotion and not run_judges:
        raise SystemExit("promotion evaluation requires a live non-mock provider and live judges")

    prompt_versions = {
        name: _prompt_version_from_text(name, captured_inputs.prompts[name])
        for name in PROMPT_NAMES
    }

    # Flatten to an ordered case list (respecting --smoke) once, so
    # --only-failed / --since filtering and the concurrent executor both work
    # off the same sequence.
    ordered_cases = [
        case for s in suites for case in s["cases"] if not (smoke and not case.get("smoke"))
    ]

    reference_dir = None
    reference_records: dict[str, dict] = {}
    if only_failed or since:
        reference_dir = _resolve_reference_run(since)
        if reference_dir is not None:
            reference_records = _load_records(reference_dir)

    if only_failed:
        failed_ids = {cid for cid, r in reference_records.items() if not r.get("passed", True)}
        ordered_cases = [c for c in ordered_cases if c["id"] in failed_ids]
        if not ordered_cases:
            raise SystemExit(
                "--only-failed: no failed cases in reference run "
                f"({reference_dir or 'none found'}); nothing to do"
            )

    mode = "smoke" if smoke else ("suite:" + suite if suite else "full")
    try:
        config_identity = captured_inputs.config_identity
        snapshot_identity = captured_inputs.snapshot_identity
        source_status = eval_attestation.git_source_status(config.REPO_ROOT)
        if release_descriptor is not None:
            descriptor = load_release_descriptor(release_descriptor)
            verify_release_descriptor(
                descriptor,
                environment=effective_environment,
                resolved_config=cfg,
                require_environment=promotion,
                config_identity=config_identity,
                chunks=captured_inputs.chunks,
            )
            if source_status["source_state"] != "clean":
                raise ReleaseIdentityError(
                    "a release descriptor cannot be evaluated from a dirty worktree"
                )
            if source_status["head_revision"] != descriptor.source_revision:
                raise ReleaseIdentityError(
                    "release descriptor source revision does not match Git HEAD"
                )
            if (
                snapshot_identity.content_version != descriptor.content_version
                or snapshot_identity.snapshot_version != descriptor.snapshot_version
            ):
                raise ReleaseIdentityError(
                    "release descriptor does not match the exact current source snapshot"
                )
            subject = {
                **source_status,
                "config_version": descriptor.config_version,
                "content_version": descriptor.content_version,
                "snapshot_version": descriptor.snapshot_version,
                "release_version": descriptor.release_version,
                "corpus_version": descriptor.corpus_version,
                "descriptor_verified": True,
            }
        else:
            release_version = None
            if source_status["source_state"] == "clean":
                release_version = build_release_identity(
                    str(source_status["source_revision"]),
                    config_identity.config_version,
                    content_version=snapshot_identity.content_version,
                    snapshot_version=snapshot_identity.snapshot_version,
                ).release_version
            subject = {
                **source_status,
                "config_version": config_identity.config_version,
                "content_version": snapshot_identity.content_version,
                "snapshot_version": snapshot_identity.snapshot_version,
                "release_version": release_version,
                "corpus_version": corpus_version,
                "descriptor_verified": False,
            }

        facts_input = captured_inputs.facts_identity
        gtfs_input = captured_inputs.gtfs_identity
        evaluator_input = eval_attestation.evaluator_identity(config.REPO_ROOT)
        ordered_case_manifest = eval_attestation.case_manifest(ordered_cases)
        suite_version = eval_attestation.suite_version(ordered_cases)
        protocol = {
            "mode": mode,
            "offline": not live,
            "provider": cfg.models.provider,
            "requested_models": {
                "answer": cfg.models.answer_model,
                "judge": cfg.models.judge_model,
            },
            "prompt_versions": prompt_versions,
            "run_judges": run_judges,
            "replicates": replicates,
            "cache_enabled": use_cache,
            "jobs": jobs,
            "evaluator_version": evaluator_input["evaluator_version"],
        }
        initial_promotion = {
            "eligible": False,
            "live": live,
            "uncached": not use_cache,
            "judges_ran": run_judges,
            "gates_passed": False,
            "reasons": ["gates_pending"],
            "evaluated_at": _rfc3339_utc(started_at),
        }
        run_attestation = eval_attestation.build_attestation(
            subject=subject,
            suite_version=suite_version,
            case_manifest=ordered_case_manifest,
            facts_version=str(facts_input["facts_version"]),
            gtfs_input_version=str(gtfs_input["gtfs_input_version"]),
            protocol=protocol,
            promotion=initial_promotion,
            config_identity=config_identity,
            snapshot_identity=snapshot_identity,
        )
        initial_promotion["reasons"] = _promotion_reasons(
            run_attestation,
            promotion_requested=promotion,
            gates_passed=False,
        )
        run_attestation = eval_attestation.build_attestation(
            subject=subject,
            suite_version=suite_version,
            case_manifest=ordered_case_manifest,
            facts_version=str(facts_input["facts_version"]),
            gtfs_input_version=str(gtfs_input["gtfs_input_version"]),
            protocol=protocol,
            promotion=initial_promotion,
            config_identity=config_identity,
            snapshot_identity=snapshot_identity,
        )
    except (
        OSError,
        ReleaseIdentityError,
        eval_attestation.EvalAttestationError,
        yaml.YAMLError,
    ) as exc:
        raise SystemExit(f"could not establish exact evaluation identity: {exc}") from exc

    case_semantics_versions = {
        entry["case_id"]: entry["case_semantics_version"] for entry in ordered_case_manifest
    }

    def _case_key(case: dict) -> str:
        return case_content_key(
            case_semantics_version=case_semantics_versions[case["id"]],
            run_context_version=str(run_attestation["context_version"]),
            run_judges=run_judges,
            replicates=replicates,
        )

    to_run: list[dict] = []
    reused: list[dict] = []
    if since and reference_dir is not None:
        for case in ordered_cases:
            prior = reference_records.get(case["id"])
            if prior and prior.get("case_key") == _case_key(case):
                reused.append(prior)
            else:
                to_run.append(case)
    else:
        to_run = ordered_cases

    cache = EvalCache(config.EVAL_CACHE_DIR, enabled=use_cache, refresh=refresh_cache)
    answer_model: Model = get_model(cfg.models.provider, cfg.models.answer_model)
    judge_model: Model = get_model(cfg.models.provider, cfg.models.judge_model)
    if use_cache:
        answer_model = CachingModel(
            answer_model, cache, provider=cfg.models.provider, kind="answer"
        )
        judge_model = CachingModel(judge_model, cache, provider=cfg.models.provider, kind="judge")

    run_dir = _allocate_run_directory(started_at)
    results_path = run_dir / "results.jsonl"

    totals: dict[str, dict[str, int]] = {}
    # Per-suite (successes, trials) across all replicate passes, used only when
    # replicates > 1 to derive a mean pass rate and a Wilson interval.
    trials: dict[str, dict[str, int]] = {}
    # Exact token usage, split by model (answer vs judge) since they price
    # differently. Aggregated into an estimated per-run cost in the summary.
    # Reused (--since) cases spent no tokens this run, so they contribute 0.
    # [canonical input total, output, cache creation input, cache read input]
    usage = {"answer": [0, 0, 0, 0], "judge": [0, 0, 0, 0]}

    def _execute(case: dict) -> tuple[dict, dict[str, list[int]], int]:
        """Run one case's replicate passes (sequentially, within this worker —
        so a multi-turn replay is never interleaved) and return
        (record, usage, passes). The first pass supplies the full trace; passes
        across replicates form the case's pass fraction. At N=1 this is exactly
        one `_run_case` call."""
        agg: dict[str, list[int]] = {
            "answer": [0, 0, 0, 0],
            "judge": [0, 0, 0, 0],
        }
        record: dict | None = None
        passes = 0
        for _rep in range(replicates):
            rep_record, case_usage = _run_case(
                case,
                answer_model=answer_model,
                judge_model=judge_model,
                retriever=retriever,
                cfg=cfg,
                corpus_doc_ids=corpus_doc_ids,
                facts_by_doc=facts_by_doc,
                structured_fares_by_agency=(captured_inputs.structured_fares_by_agency),
                answer_system_prompt=captured_inputs.prompts["system"],
                answer_user_prompt=captured_inputs.prompts["answer_user"],
                groundedness_prompt=captured_inputs.prompts["judge_groundedness"],
                helpfulness_prompt=captured_inputs.prompts["judge_helpfulness"],
                run_judges=run_judges,
            )
            for kind in ("answer", "judge"):
                for index in range(4):
                    agg[kind][index] += case_usage[kind][index]
            passes += int(rep_record["passed"])
            if record is None:
                record = rep_record
        assert record is not None
        if replicates > 1:
            # A case's "passed" stays the first pass's verdict (its trace);
            # pass_fraction carries the measured across-replicate rate for the
            # interval and for compare.py. Suite totals count majority votes.
            record["replicates"] = replicates
            record["pass_fraction"] = round(passes / replicates, 4)
        record["case_semantics_version"] = case_semantics_versions[case["id"]]
        record["run_context_version"] = run_attestation["context_version"]
        record["case_key"] = _case_key(case)
        return record, agg, passes

    fresh_by_id: dict[str, tuple[dict, dict[str, list[int]], int]] = {}
    if jobs <= 1:
        for case in to_run:
            fresh_by_id[case["id"]] = _execute(case)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_execute, case): case["id"] for case in to_run}
            for fut in as_completed(futures):
                fresh_by_id[futures[fut]] = fut.result()

    # Reassemble in the original suite order regardless of execution/reuse
    # path, so results.jsonl is stable run to run for the same case set.
    reused_by_id = {r["case_id"]: r for r in reused}
    records = []
    not_applicable: list[dict[str, str]] = []
    for case in ordered_cases:
        if case["id"] in fresh_by_id:
            record, case_usage, passes = fresh_by_id[case["id"]]
            usage["answer"][0] += case_usage["answer"][0]
            usage["answer"][1] += case_usage["answer"][1]
            usage["answer"][2] += case_usage["answer"][2]
            usage["answer"][3] += case_usage["answer"][3]
            usage["judge"][0] += case_usage["judge"][0]
            usage["judge"][1] += case_usage["judge"][1]
            usage["judge"][2] += case_usage["judge"][2]
            usage["judge"][3] += case_usage["judge"][3]
            status = "PASS" if record["passed"] else "FAIL"
        else:
            record = reused_by_id[case["id"]]
            passes = int(record["passed"])  # reuse path implies replicates == 1
            status = ("PASS" if record["passed"] else "FAIL") + " (reused)"
        _validate_result_provenance(
            record,
            case_id=case["id"],
            case_semantics_version=case_semantics_versions[case["id"]],
            run_context_version=str(run_attestation["context_version"]),
        )
        records.append(record)
        t = totals.setdefault(case["suite"], {"passed": 0, "total": 0})
        if record.get("not_applicable"):
            # Out of the denominator, never silently: the case is counted and
            # named under `not_applicable` in the summary so a reader can see
            # exactly how much of the suite this run did not measure.
            t["not_applicable"] = t.get("not_applicable", 0) + 1
            not_applicable.append(
                {
                    "case_id": case["id"],
                    "suite": case["suite"],
                    "reason": record.get("not_applicable_reason", ""),
                }
            )
            print(f"N/A   {case['id']}  ({record.get('not_applicable_reason', '')})")
            continue
        t["total"] += 1
        # Count a case as passed by majority vote across replicates (== the
        # single pass at N=1), so passed/total stays interpretable.
        t["passed"] += 1 if passes * 2 >= replicates else 0
        tr = trials.setdefault(case["suite"], {"successes": 0, "trials": 0})
        tr["successes"] += passes
        tr["trials"] += replicates
        print(f"{status}  {case['id']}")

    results_bytes = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    ).encode("utf-8")
    with results_path.open("xb") as stream:
        stream.write(results_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    results_sha256 = hashlib.sha256(results_bytes).hexdigest()

    cache.save()

    def _suite_entry(name: str, t: dict[str, int]) -> dict:
        # A suite every one of whose cases was withheld by the source policy has
        # no measured rate. Report it as null rather than inventing 0% (which
        # reads as "the assistant failed") or 100% (which reads as "verified").
        if not t["total"]:
            return {**t, "pass_rate": None}
        entry = {**t, "pass_rate": round(100 * t["passed"] / t["total"], 1)}
        if replicates > 1:
            # Report the mean pass rate over all replicate trials and its Wilson
            # 95% interval, so the headline is a measured band, not a point.
            tr = trials[name]
            low, high = wilson_interval(tr["successes"], tr["trials"])
            entry["pass_rate"] = round(100 * tr["successes"] / tr["trials"], 1)
            entry["ci_low"] = round(100 * low, 1)
            entry["ci_high"] = round(100 * high, 1)
            entry["replicates"] = replicates
        return entry

    suite_summary = {name: _suite_entry(name, t) for name, t in sorted(totals.items())}
    total_summary = {
        "passed": sum(t["passed"] for t in totals.values()),
        "total": sum(t["total"] for t in totals.values()),
    }
    if not_applicable:
        total_summary["not_applicable"] = len(not_applicable)
    summary: dict[str, object] = {
        "run_id": run_dir.name,
        "run_at": _rfc3339_utc(started_at),
        "results_sha256": results_sha256,
        "mode": mode,
        "offline": not live,
        "judges_ran": run_judges,
        "promotion_requested": promotion,
        "gate_status": "pending",
        "attestation": run_attestation,
        "evaluation_inputs": {
            "suite": {
                "schema": eval_attestation.SUITE_SCHEMA,
                "suite_version": suite_version,
                "cases": len(ordered_cases),
            },
            "facts": facts_input,
            "gtfs": gtfs_input,
            "evaluator": evaluator_input,
        },
        "answer_model": cfg.models.answer_model,
        "judge_model": cfg.models.judge_model,
        "served_models": _served_model_unions(records),
        "prompt_versions": prompt_versions,
        # Pinned so the provenance gate (evals/provenance.py) can prove EVALS.md,
        # the baseline, and the audit dataset describe the same corpus HEAD ships.
        "corpus_version": corpus_version,
        "duration_seconds": round(time.monotonic() - started, 1),
        "cost": _cost_block(cfg, usage),
        "execution": {
            "jobs": jobs,
            "cache": cache.stats(),
            "only_failed": only_failed,
            "since": since or (reference_dir.name if only_failed and reference_dir else None),
            "reused_cases": len(reused),
            "executed_cases": len(to_run),
        },
        "suites": suite_summary,
        "total": total_summary,
        # Cases the run could not measure because the operator has disabled the
        # source they depend on. Named individually: this is a coverage hole,
        # and a coverage hole that is only a number is one nobody chases down.
        "not_applicable": {
            "count": len(not_applicable),
            "reasons": sorted({na["reason"] for na in not_applicable}),
            "cases": not_applicable,
        },
    }
    if replicates > 1:
        summary["replicates"] = replicates

    # Counterfactual sensitivity: fold the pair-level verdict into the
    # sensitivity suite's summary. A pair is distinguished only if every one of
    # its variants passed (see `pair_verdicts`).
    verdicts = pair_verdicts(records)
    if verdicts and "sensitivity" in suite_summary:
        suite_summary["sensitivity"]["pairs_passed"] = sum(verdicts.values())
        suite_summary["sensitivity"]["pairs_total"] = len(verdicts)

    # Bilingual parity (M-1): record the ES-vs-mirrored-EN delta alongside the
    # scoreboard so downstream tools (report, history) read one number instead
    # of re-deriving it from records.
    parity = parity_delta(records)
    if parity:
        summary["parity"] = parity

    _write_summary(run_dir, summary)

    print(f"\n{total_summary['passed']}/{total_summary['total']} passed → {run_dir}")
    if not_applicable:
        by_reason: dict[str, int] = {}
        for na in not_applicable:
            by_reason[na["reason"]] = by_reason.get(na["reason"], 0) + 1
        detail = ", ".join(f"{r} ({n})" for r, n in sorted(by_reason.items()))
        print(
            f"{len(not_applicable)} case(s) not applicable under the active source "
            f"policy and excluded from the denominator: {detail}"
        )
    return run_dir


def suite_regressed(base: dict, now: dict, threshold: float = 2.0, case_floor: int = 2) -> bool:
    """A suite regresses only if its pass rate dropped more than `threshold`
    points AND its pass count dropped by at least `case_floor` cases.

    The case floor exists because the percentage gate alone is incoherent on
    small suites: one case in the 6-case conversation suite is 16.7 points, so a
    single boundary case flipping under LLM-judge variance would always trip a
    2-point gate. Two cases is still a cheap, sensitive signal on the larger
    suites while absorbing the one-case judge noise the harness sees run to run.

    `case_floor` defaults to 2 (the historical hand-tuned value). Once a
    replicated run (`--replicates`) has measured the per-case flip rate, pass a
    floor derived from it — e.g. `ceil` of the expected number of cases that
    flip under the null — instead of the guess. See `flip_case_floor`.
    """
    rate_drop = now["pass_rate"] < base["pass_rate"] - threshold
    case_drop = base["passed"] - now["passed"] >= case_floor
    return rate_drop and case_drop


def flip_case_floor(flip_rate: float, n_cases: int, safety: float = 1.0) -> int:
    """Derive a `suite_regressed` case floor from a measured per-case flip rate.

    Given the fraction of cases that flip pass/fail between replicate runs of the
    same config (`flip_rate`, from a `--replicates` run) and the suite size, the
    expected number of noise flips is `flip_rate * n_cases`. The floor is that
    expectation rounded up, times `safety`, and never below the historical 2 —
    so switching a suite to a measured floor can only make the gate stricter,
    never looser than today.
    """
    expected = math.ceil(flip_rate * n_cases * safety)
    return max(2, expected)


# ── mirror integrity: the parity gate's own denominator ──────────────────────


def mirror_problems(cases: dict[str, dict]) -> list[str]:
    """Every `mirror_of` declaration must name a real mirror; empty is clean.

    The parity gate below compares a Spanish case's pass/fail against its
    English mirror's and publishes the delta in points. That delta only means
    "the same question, answered in two languages" if the pair really is the
    same question: same agency, same expected behavior, and the same evidence
    demanded of both answers. Nothing checked that until 2026-08-05, and three
    of the 22 pairs in the promoted baseline were not mirrors:

    * `ml-008` pointed at `edge-008` — already `ml-004`'s mirror — asked a
      different question (how to get a Courtesy Card, not what proof MST
      accepts for the veteran discount), and declared no `required_facts` at
      all, so it could pass on citation, language, and guard checks while its
      mirror additionally had to produce "DD Form 214";
    * `ml-011` dropped its mirror's `65` fact, so the Spanish answer never had
      to state the age criterion the English answer was required to state;
    * `ml-022` is scoped to MST but pointed at a Yolobus case, so the pair
      measured two corpora rather than two languages.

    The gate reported a 0.0-point gap over all three. A parity number computed
    across pairs that are not pairs is not a slightly wrong number; it is an
    unmeasured property rendered as a pass.

    Counting `required_facts` rather than comparing them is deliberate: the
    strings are language-specific by design ("free of charge" cannot be
    required of a Spanish answer), so equality is the wrong test, but a mirror
    asked to prove strictly fewer things than its target is always a weaker
    case wearing a pair's name.
    """
    problems = []
    for case_id, case in sorted(cases.items()):
        target_id = case.get("mirror_of")
        if not target_id:
            continue
        target = cases.get(target_id)
        if target is None:
            problems.append(f"{case_id}: mirror_of names {target_id!r}, which is not a case")
            continue
        if case.get("language", "en") == target.get("language", "en"):
            problems.append(
                f"{case_id}: mirrors {target_id}, but both are "
                f"{case.get('language', 'en')!r} — a same-language pair measures no gap"
            )
        if (case.get("agency_scope") or None) != (target.get("agency_scope") or None):
            problems.append(
                f"{case_id}: scoped to {case.get('agency_scope')!r} but mirrors "
                f"{target_id}, scoped to {target.get('agency_scope')!r} — the pair would "
                "measure two corpora, not two languages"
            )
        if case.get("expected_behavior") != target.get("expected_behavior"):
            problems.append(
                f"{case_id}: expects {case.get('expected_behavior')!r} but mirrors "
                f"{target_id}, which expects {target.get('expected_behavior')!r}"
            )
        own, theirs = len(case.get("required_facts") or []), len(target.get("required_facts") or [])
        if own < theirs:
            problems.append(
                f"{case_id}: declares {own} required_facts but mirrors {target_id}, which "
                f"declares {theirs} — a mirror asked to prove less passes more easily, and "
                "the parity gate would read that as equity"
            )
    return problems


def check_mirrors() -> None:
    """Fail (exit 1) if any `mirror_of` declaration is not a true mirror.

    Always reads every suite, never the `--suite` subset: a mirror's target
    almost always lives in a different file, so a filtered load would report a
    missing mirror that is merely out of view.
    """
    problems = mirror_problems({c["id"]: c for s in load_suites() for c in s["cases"]})
    if problems:
        print("MIRROR GATE:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)


# ── minimal-pair integrity: the sensitivity suite's own denominator ──────────


def _demand_signature(case: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What a case *demands of the answer*, over and above the checks every case
    shares. Within a minimal pair the variants carry the same agency scope and
    expected behavior by construction, so these two lists are the whole of the
    difference between one side of the boundary and the other."""
    return (
        tuple(sorted(case.get("required_facts") or [])),
        tuple(sorted(case.get("forbidden_content") or [])),
    )


def pair_problems(cases: dict[str, dict]) -> list[str]:
    """Every minimal pair's variants must demand different things; empty is clean.

    `pair_verdicts` scores a counterfactual pair as distinguished only when every
    variant passes, and says why: "the per-variant required_facts /
    forbidden_content prove the answer actually changed (or held) across the
    boundary." That holds only if the variants ask for different evidence. Two
    variants that demand exactly the same thing are one case written twice, and
    a single boilerplate answer satisfies both — which the report then prints as
    a boundary "correctly distinguished".

    Nothing checked this until 2026-08-05, and two of the 15 pairs in the
    promoted baseline failed it: `sens-011` (both variants required only "Stored
    Value", though the boundary is that a *monthly* pass is excluded from the
    reduced fare) and `sens-014` (both required only the 3-17 range, though the
    boundary is that an 18-year-old falls outside it). Both were scored as
    distinguished.

    This is the same shape as `mirror_problems`: a comparison is only a
    comparison if the two things compared are actually different, and the number
    it publishes says nothing until something checks that.
    """
    grouped: dict[str, list[tuple[str, dict]]] = {}
    for case_id, case in sorted(cases.items()):
        pair_id = case.get("pair_id")
        if pair_id:
            grouped.setdefault(pair_id, []).append((case_id, case))

    problems = []
    for pair_id, members in sorted(grouped.items()):
        for case_id, case in members:
            if not (case.get("required_facts") or case.get("forbidden_content")):
                problems.append(
                    f"{case_id}: variant of pair {pair_id} declares neither required_facts "
                    "nor forbidden_content, so no answer to it can fail on the boundary"
                )
        by_signature: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
        for case_id, case in members:
            by_signature.setdefault(_demand_signature(case), []).append(case_id)
        for shared in sorted(v for v in by_signature.values() if len(v) > 1):
            problems.append(
                f"pair {pair_id}: {', '.join(shared)} demand identical required_facts and "
                "forbidden_content — one answer satisfies both sides, so the pair proves "
                "no discrimination across its boundary"
            )
    return problems


def check_pairs() -> None:
    """Fail (exit 1) if any minimal pair's variants demand identical evidence.

    Runs beside `check_mirrors`, before the first model call, for the same
    reason: a run that cannot measure the thing it reports should not be paid
    for.
    """
    problems = pair_problems({c["id"]: c for s in load_suites() for c in s["cases"]})
    if problems:
        print("MINIMAL-PAIR GATE:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)


def pair_discrimination(records: list[dict], cases: dict[str, dict] | None = None) -> dict:
    """Did each pair's variants actually produce distinguishable answers?

    `pair_problems` is static: it proves the two variants *ask* for different
    things. This is the run-level question the scoreboard's "boundary pairs
    correctly distinguished" line implies but has never answered — whether the
    answers came out different. For every variant it replays its *sibling's*
    recorded answer through its own `required_facts` / `forbidden_content`. If
    each variant's demands are satisfied by the other's answer, then one answer
    would have passed both sides and the pair distinguished nothing, however
    green it scored.

    Reported, not gated. Five of the 15 pairs in the promoted run come out
    interchangeable, and gating that today would fail the committed report
    without a credentialed run available to regenerate it. Publishing the number
    beside the pair pass rate is the honest interim: the pass rate is real, and
    it means less than it reads.

    Returns {pair_id: {"discriminating": bool, "interchangeable": [...]}} over
    the pairs whose every variant is present in `records`.
    """
    cases = cases or {c["id"]: c for s in load_suites() for c in s["cases"]}
    answers = {r["case_id"]: r.get("answer", "") for r in records}
    grouped: dict[str, list[str]] = {}
    for case_id, case in sorted(cases.items()):
        pair_id = case.get("pair_id")
        if pair_id:
            grouped.setdefault(pair_id, []).append(case_id)

    out: dict[str, dict] = {}
    for pair_id, members in sorted(grouped.items()):
        if not all(m in answers for m in members):
            continue  # a subset run; the pair is out of view, not undiscriminating
        interchangeable = [
            f"{other} satisfies {own}"
            for own in members
            for other in members
            if own != other and _demands_met(cases[own], answers[other])
        ]
        out[pair_id] = {
            "discriminating": len(interchangeable) < len(members) * (len(members) - 1),
            "interchangeable": interchangeable,
        }
    return out


def _demands_met(case: dict, answer: str) -> bool:
    """Whether `answer` satisfies the case-specific demands (`required_facts`,
    `forbidden_content`) — the two checks that differ between the variants of a
    minimal pair. Uses the same matchers the grader uses, never a copy."""
    if any(not checks.fact_matches(f, answer) for f in case.get("required_facts") or []):
        return False
    return not any(checks.phrase_asserted(p, answer) for p in case.get("forbidden_content") or [])


# ── bilingual parity gate (M-1; audit P1-1; AIEV-10/11, I18N-22) ─────────────

PARITY_SUITE = "multilingual"
PARITY_THRESHOLD_PP = 5.0
PARITY_CASE_FLOOR = 2
MACRO_THRESHOLD_PP = 5.0
# Issue #146: on the 26-case smoke suite (5 gated suites, smallest is
# freshness at 4 cases), MACRO_THRESHOLD_PP's 5-point tolerance is
# unsatisfiable by anything short of a perfect run -- a single failure in a
# 4-case suite moves it 25 points, and every single-failure configuration on
# smoke breaches a 5-point floor. Same fix as PARITY_CASE_FLOOR: a suite
# only counts as a real offender if closing the gap to the floor would take
# at least this many more passing cases, not just one judge-noise flip.
SUITE_CASE_FLOOR = 2
_STRETCH_PREFIX = "stretch_"
EXPECTED_BELOW_MACRO_PATH = config.REPO_ROOT / "evals" / "expected_below_macro.json"


def parity_delta(records: list[dict], suite: str = PARITY_SUITE) -> dict | None:
    """The ES-vs-mirrored-EN pass delta over `suite`, from one run's records.

    Each case in `suite` that names a `mirror_of` English case present in the
    same run forms a pair; the delta is the mirrored-English pass rate minus
    the Spanish pass rate, in percentage points. Positive means the English
    mirrors passed more often — the equity gap the parity gate exists to catch.
    Comparing within a single run means judge model, prompts, and corpus cancel
    out, so the number is meaningful on any mode (offline, smoke, full).

    Pairs whose mirror is absent from the run (e.g. a smoke subset that kept
    the Spanish case but not its mirror) are skipped; returns None when no
    complete pair exists, so partial runs skip the gate loudly rather than
    tripping on noise.
    """
    by_id = {r["case_id"]: r for r in records if not r.get("not_applicable")}
    pairs = [
        (r, by_id[r["mirror_of"]])
        for r in records
        if r["suite"] == suite and not r.get("not_applicable") and r.get("mirror_of") in by_id
    ]
    if not pairs:
        return None
    passed = sum(1 for r, _ in pairs if r["passed"])
    mirror_passed = sum(1 for _, en in pairs if en["passed"])
    n = len(pairs)
    return {
        "suite": suite,
        "pairs": n,
        "passed": passed,
        "mirror_passed": mirror_passed,
        "delta_pp": round((mirror_passed - passed) * 100 / n, 1),
    }


def parity_regressed(
    parity: dict, threshold: float = PARITY_THRESHOLD_PP, case_floor: int = PARITY_CASE_FLOOR
) -> bool:
    """Same two-condition shape as `suite_regressed`: the parity gap must
    exceed `threshold` points AND at least `case_floor` more mirrored English
    cases than Spanish cases must have passed. The case floor absorbs the
    one-case judge-noise flip that would otherwise dominate a small pair set
    (see `suite_regressed`'s rationale); one flipped pair out of 22 is 4.5
    points and 1 case — noise, not an equity finding."""
    gap_cases = parity["mirror_passed"] - parity["passed"]
    return parity["delta_pp"] > threshold and gap_cases >= case_floor


def _cases_behind_macro(passed: int, total: int, macro: float) -> int:
    """How many more cases this suite would need to have passed to match the
    macro rate applied to its own size -- the per-suite analog of
    `parity_regressed`'s `gap_cases = mirror_passed - passed`, which is a
    direct pass-count difference because both sides share one denominator (n
    mirrored pairs). A suite being compared to the macro has no shared
    denominator with anything, so the comparison is normalized to this
    suite's own `total` first: `ceil(macro% of total)` is the pass count a
    suite of this size would need to sit exactly at the macro rate, and the
    gap against `passed` is rounded up (not down) so a suite sitting exactly
    on a fractional boundary is not let off by a rounding accident -- the
    conversation suite in the committed report (8/10, macro ~94.0% over 8
    gated suites) needs `ceil(9.4025) = 10`, not 9, or its real, separately
    investigated regression (conv-forged-002/004) would read as one case of
    noise and silently stop being an offender.
    """
    if total <= 0:
        return 0
    expected_passed = math.ceil(macro / 100 * total - 1e-9)
    return max(0, expected_passed - passed)


def suites_below_macro(
    suites: dict, threshold: float = MACRO_THRESHOLD_PP, case_floor: int = SUITE_CASE_FLOOR
) -> dict[str, dict]:
    """The general per-suite form of the parity gate (AIEV-10): every gated
    suite's pass rate must be at least the macro pass rate minus `threshold`
    points, where macro is the unweighted mean over gated suites -- AND the
    suite must be at least `case_floor` cases behind the macro rate itself
    (see `_cases_behind_macro`), the same two-condition shape as
    `suite_regressed` and `parity_regressed` (issue #146, ADR 0026).
    Percentage alone is incoherent on a small suite: the 26-case smoke
    subset's smallest gated suite (freshness) is 4 cases, so one failure
    there is a 25-point swing that trips any single-digit percentage floor on
    its own. The case floor absorbs a one-case judge-noise flip; two failures
    in one suite is a real signal on smoke, same as everywhere else this
    shape is used, and this changes nothing on the full suite, where every
    gated suite is large enough that a genuine breach already implies two or
    more cases.

    `stretch_*` suites are excluded from both the mean and the gate:
    docs/ROADMAP.md P3-3 and the report's stretch-parity section promise that a
    stretch language's score is reported honestly but never fails a build.

    Returns {suite: {"pass_rate", "macro", "floor", "cases_behind_macro"}}
    for each offender; floors are compared unrounded and rounded only for
    display.
    """
    # A suite with no measured rate (every case withheld by the source policy)
    # carries no signal and must not drag the macro mean down as if it were 0%.
    gated = {
        n: s
        for n, s in suites.items()
        if not n.startswith(_STRETCH_PREFIX) and s.get("pass_rate") is not None
    }
    if not gated:
        return {}
    macro = sum(s["pass_rate"] for s in gated.values()) / len(gated)
    floor = macro - threshold
    offenders = {}
    for name, s in gated.items():
        if s["pass_rate"] >= floor:
            continue
        cases_behind = _cases_behind_macro(s.get("passed", 0), s.get("total", 0), macro)
        if cases_behind < case_floor:
            continue
        offenders[name] = {
            "pass_rate": s["pass_rate"],
            "macro": round(macro, 1),
            "floor": round(floor, 1),
            "cases_behind_macro": cases_behind,
        }
    return offenders


def expected_below_macro(path: Path | None = None) -> dict[str, str]:
    """The loud escape hatch for the below-macro form: a committed JSON file
    mapping suite name → written rationale (keys starting with "_" are file
    commentary). An annotated suite is reported, not failed — mirroring the
    `stale_acknowledged.json` pattern: a gap may be accepted, but only in
    writing, in the diff, deleted when the suite recovers."""
    p = path or EXPECTED_BELOW_MACRO_PATH
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parity_problems(
    records: list[dict], suites: dict, annotations: dict[str, str] | None = None
) -> list[str]:
    """All parity-gate findings for one run; empty is clean. Pure so the
    committed-report checker and tests can exercise it without a run dir."""
    notes = expected_below_macro() if annotations is None else annotations
    problems = []
    parity = parity_delta(records)
    if parity is None:
        print("no complete Spanish/English mirror pairs in this run; parity skipped")
    elif parity_regressed(parity):
        problems.append(
            f"Spanish parity: {parity['passed']}/{parity['pairs']} vs mirrored English "
            f"{parity['mirror_passed']}/{parity['pairs']} — gap {parity['delta_pp']} pp "
            f"exceeds {PARITY_THRESHOLD_PP:g} pp on {PARITY_CASE_FLOOR}+ cases"
        )
    offenders = suites_below_macro(suites)
    for name, o in sorted(offenders.items()):
        if name in notes:
            continue
        problems.append(
            f"{name}: {o['pass_rate']}% is below the macro floor {o['floor']}% "
            f"(macro {o['macro']}% − {MACRO_THRESHOLD_PP:g} pp) on {o['cases_behind_macro']}+ "
            "cases, with no written annotation in evals/expected_below_macro.json"
        )
    problems += stale_annotations(suites, notes)
    return problems


def stale_annotations(suites: dict, notes: dict[str, str]) -> list[str]:
    """Annotations that no longer describe anything; empty is clean.

    `expected_below_macro.json` says "delete the entry the moment the suite
    recovers", and nothing enforced that. An annotation for a suite that has
    since climbed back above the floor is a live waiver sitting over a suite
    with no known problem — it would silently absorb the next real regression,
    which is the thing the gate exists to catch. So a recovered suite's
    annotation is itself a finding: the escape hatch expires on its own.

    Only suites present in the run are considered. A `--suite` subset legitimately
    omits most of them, and an annotation for a suite that simply did not run is
    out of view rather than stale.
    """
    offenders = suites_below_macro(suites)
    return [
        f"{name}: annotated in evals/expected_below_macro.json but is at "
        f"{suites[name]['pass_rate']}%, at or above the macro floor — the annotation no "
        "longer describes anything and must be deleted, or it will silently waive the "
        "next real regression"
        for name in sorted(notes)
        if name in suites and name not in offenders
    ]


# The share of a run that may be withheld by the source policy before the run
# stops being an evaluation of the product. Excluding a handful of cases whose
# source is under review is honest bookkeeping; excluding a fifth of the suite
# and still calling the remainder a promotion gate is not.
COVERAGE_NOT_APPLICABLE_CEILING = 0.15


def coverage_problems(summary: Mapping[str, object]) -> list[str]:
    """Findings for the coverage gate; empty is clean.

    The not-applicable mechanism exists so a deliberately contained source does
    not read as an assistant failure. It must never become a way to make a
    failing gate pass by disabling whatever the suite is unhappy about, so the
    escape hatch is itself bounded and the bound is enforced here.
    """
    na = summary.get("not_applicable") or {}
    assert isinstance(na, Mapping)
    withheld = int(na.get("count", 0) or 0)
    if not withheld:
        return []
    total = summary.get("total") or {}
    assert isinstance(total, Mapping)
    measured = int(total.get("total", 0) or 0)
    considered = measured + withheld
    if not considered:
        return []
    share = withheld / considered
    if share <= COVERAGE_NOT_APPLICABLE_CEILING:
        return []
    reasons = ", ".join(str(r) for r in (na.get("reasons") or []))
    return [
        f"{withheld}/{considered} cases ({share:.1%}) were withheld by the active "
        f"source policy, above the {COVERAGE_NOT_APPLICABLE_CEILING:.0%} ceiling — "
        f"the run no longer covers enough of the product to gate a release "
        f"({reasons}). Restore the source, or re-scope the suite."
    ]


def check_coverage(run_dir: Path) -> None:
    """Fail (exit 1) when the source policy has hollowed out the run.

    Only a full run gates a release, so only a full run is held to the ceiling.
    A `--suite` subset concentrates whatever it is about — `--suite conversation`
    is 30% Yolobus — and failing it for that would report the subset's shape as
    a coverage problem. The withheld cases are still counted and named in every
    mode; what a subset does not do is block on them.
    """
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if summary.get("mode") != "full":
        return
    problems = coverage_problems(summary)
    if problems:
        print("COVERAGE GATE:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)


def check_parity(run_dir: Path, *, require_complete: bool = False) -> None:
    """Fail (exit 1) if the run trips the bilingual parity gate: the Spanish
    vs mirrored-English delta exceeds 5 points on 2+ cases, or any gated suite
    sits more than 5 points below the macro pass rate without a written
    annotation. There is no silent skip: fix the gap, or annotate it in
    `evals/expected_below_macro.json` with a rationale that survives review."""
    records = [
        json.loads(line)
        for line in _jsonl_lines(
            (run_dir / "results.jsonl").read_bytes(),
            "results.jsonl",
        )
    ]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if require_complete and parity_delta(records) is None:
        print(
            "PARITY GATE (M-1): full promotion run has no complete Spanish/English mirror pairs",
            file=sys.stderr,
        )
        raise SystemExit(1)
    problems = parity_problems(records, summary["suites"])
    if problems:
        print("PARITY GATE (M-1):\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)


def check_regression(
    run_dir: Path,
    threshold: float = 2.0,
    *,
    strict: bool = False,
) -> None:
    """Fail (exit 1) if any suite regressed vs. the committed baseline (see
    `suite_regressed`). Update the baseline deliberately with
    `python -m evals.runner --update-baseline`."""
    baseline_path = config.EVAL_RUNS_DIR.parent / "baseline.json"
    if not baseline_path.exists():
        if strict:
            raise SystemExit("promotion regression gate requires evals/baseline.json")
        print("no evals/baseline.json; skipping regression gate", file=sys.stderr)
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if summary.get("offline") and not baseline.get("offline"):
        if strict:
            raise SystemExit("promotion regression gate cannot compare offline to live")
        print("offline run vs. live baseline; skipping regression gate", file=sys.stderr)
        return
    if summary.get("mode") != baseline.get("mode"):
        if strict:
            raise SystemExit(
                f"promotion regression gate mode mismatch "
                f"({summary.get('mode')} vs baseline {baseline.get('mode')})"
            )
        print(
            f"mode mismatch ({summary.get('mode')} vs baseline {baseline.get('mode')}); "
            "skipping regression gate",
            file=sys.stderr,
        )
        return
    if strict:
        if baseline.get("offline") is not False:
            raise SystemExit("promotion regression baseline must be live")
        if summary.get("answer_model") != baseline.get("answer_model"):
            raise SystemExit("promotion regression baseline answer model does not match")
        if set(summary.get("suites", {})) != set(baseline.get("suites", {})):
            raise SystemExit("promotion regression baseline suite set does not match")
        expected_provenance = {
            "prompt_versions": summary.get("prompt_versions") or {},
            "corpus_version": summary.get("corpus_version"),
        }
        if baseline.get("provenance") != expected_provenance:
            raise SystemExit("promotion regression baseline provenance does not match")
    regressions = []
    for suite, base in baseline["suites"].items():
        now = summary["suites"].get(suite)
        if now and suite_regressed(base, now, threshold):
            regressions.append(
                f"{suite}: {base['passed']}/{base['total']} → {now['passed']}/{now['total']}"
            )
    if regressions:
        print(
            "REGRESSION (>2 points and >=2 cases):\n  " + "\n  ".join(regressions), file=sys.stderr
        )
        raise SystemExit(1)


def update_baseline(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    baseline = {
        "from_run": summary["run_at"],
        "mode": summary["mode"],
        "offline": summary["offline"],
        "answer_model": summary["answer_model"],
        # Provenance so the gate can catch a baseline left behind by a prompt or
        # corpus change. Older/synthetic summaries (e.g. test fixtures, or runs
        # from before this field existed) may carry neither key, so fall back
        # rather than raising: an absent prompt_versions renders as {} instead
        # of crashing the update.
        "provenance": {
            "prompt_versions": summary.get("prompt_versions") or {},
            "corpus_version": summary.get("corpus_version") or corpus.corpus_version(),
        },
        "suites": summary["suites"],
    }
    path = config.EVAL_RUNS_DIR.parent / "baseline.json"
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"baseline updated from {summary['run_at']} → {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--suite")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument(
        "--promotion",
        action="store_true",
        help="require a clean, full, live, uncached, descriptor-bound release evaluation",
    )
    parser.add_argument(
        "--release-descriptor",
        type=Path,
        help="exact candidate release/release.json (required with --promotion)",
    )
    parser.add_argument(
        "--run-path-output",
        type=Path,
        help="write a canonical pointer to the completed, gated evaluation bundle",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="bounded-concurrency workers for case execution (default 4)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="disable the answer/judge cache — use for FIX-04 variance-measurement runs",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="call the provider for every case (no cache reads) but store the results, so a "
        "cold re-measurement leaves the cache agreeing with the scoreboard it published",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="only run cases that failed in the reference run (--since, or the latest run)",
    )
    parser.add_argument(
        "--since",
        metavar="RUN",
        help="reuse cases unchanged (by content key) from evals/runs/RUN; also the reference "
        "run for --only-failed if given",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=1,
        help="run each case N times and report a per-suite mean ± Wilson interval "
        "(N=1, the default, is byte-identical to a single run). Live and paid; "
        "always bypasses the cache and excludes --since/--only-failed.",
    )
    args = parser.parse_args()
    if args.promotion and not args.full:
        parser.error("--promotion requires --full")
    if args.promotion and args.update_baseline:
        parser.error("--promotion cannot update the committed baseline")

    run_dir = run(
        smoke=args.smoke,
        offline=args.offline,
        suite=args.suite,
        jobs=args.jobs,
        use_cache=not args.no_cache,
        refresh_cache=args.refresh_cache,
        only_failed=args.only_failed,
        since=args.since,
        replicates=args.replicates,
        promotion=args.promotion,
        release_descriptor=args.release_descriptor,
    )
    try:
        if args.promotion:
            assert args.release_descriptor is not None
            _verify_promotion_inputs_unchanged(
                run_dir,
                release_descriptor=args.release_descriptor,
            )
        # Promotion runs intentionally leave tracked report files untouched:
        # their immutable summary/results become deploy evidence instead.
        if args.full and not args.promotion:
            from evals.report import generate

            generate(run_dir)
        # Parity is within-run (ES vs mirrored EN of the same run), so unlike the
        # baseline regression gate it applies even when the baseline is being
        # deliberately re-set — a re-baseline must not silence an equity gap.
        check_coverage(run_dir)
        check_parity(run_dir, require_complete=args.promotion)
        if args.update_baseline:
            update_baseline(run_dir)
        else:
            check_regression(run_dir, strict=args.promotion)
    except BaseException:
        try:
            _finalize_run_gates(run_dir, passed=False)
        except Exception as finalize_error:  # retain the original gate failure
            print(f"could not record failed gate status: {finalize_error}", file=sys.stderr)
        raise
    _finalize_run_gates(run_dir, passed=True)
    bundle = _publish_run_bundle(run_dir)
    if args.run_path_output is not None:
        _write_run_path_pointer(args.run_path_output, bundle)


if __name__ == "__main__":
    main()
