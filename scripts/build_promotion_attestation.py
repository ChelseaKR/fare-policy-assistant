#!/usr/bin/env python3
"""Build one canonical promotion attestation from reviewed release evidence.

The output is intentionally non-secret evidence and is written mode ``0644`` so
deployment packaging and the read-only evidence service can consume it. Inputs
are opened without following final-component symlinks, JSON is parsed with
duplicate-key rejection, and the output is staged, fsynced, and atomically
replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from assistant.release_attestation import (  # noqa: E402
    EvaluationRun,
    LogicalRelease,
    PromotionAttestation,
    PromotionAttestationError,
    RuntimeRelease,
    attestation_bytes,
    attestation_digest,
    build_promotion_attestation,
)
from evals.attestation import (  # noqa: E402
    ATTESTATION_SCHEMA as EVAL_ATTESTATION_SCHEMA,
)
from evals.attestation import (  # noqa: E402
    EvalAttestationError,
)
from evals.attestation import (  # noqa: E402
    build_attestation as rebuild_eval_attestation,
)

_RUNTIME_FIELDS = frozenset(
    {
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "artifact_code_sha256",
        "function_version",
    }
)
_LOGICAL_RELEASE_FIELDS = (
    "source_revision",
    "config_version",
    "content_version",
    "snapshot_version",
    "release_version",
    "corpus_version",
)
_EVAL_ATTESTATION_FIELDS = frozenset(
    {
        "attestation_schema",
        "subject",
        "evidence",
        "protocol",
        "promotion",
        "context_version",
        "attestation_version",
    }
)
_EVAL_SUBJECT_FIELDS = frozenset(
    {
        *_LOGICAL_RELEASE_FIELDS,
        "source_state",
        "head_revision",
        "descriptor_verified",
    }
)
_EVAL_EVIDENCE_FIELDS = frozenset(
    {
        "suite_version",
        "case_count",
        "case_manifest",
        "facts_version",
        "gtfs_input_version",
    }
)
_EVAL_PROMOTION_FIELDS = frozenset(
    {
        "eligible",
        "live",
        "uncached",
        "judges_ran",
        "gates_passed",
        "reasons",
        "evaluated_at",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RFC3339_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|\+00:00)$"
)
_RFC3339_Z = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)


class PromotionAttestationBuildError(ValueError):
    """Promotion evidence is missing, ambiguous, or unsafe."""


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PromotionAttestationBuildError(f"{context} must be a JSON object")
    return value


def _exact_fields(
    value: object,
    expected: frozenset[str],
    context: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    actual = set(mapping)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unexpected = sorted(actual - set(expected))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise PromotionAttestationBuildError(
            f"{context} has an invalid field set ({'; '.join(details)})"
        )
    return mapping


def _required(mapping: Mapping[str, object], field: str, context: str) -> object:
    if field not in mapping:
        raise PromotionAttestationBuildError(f"{context}.{field} is required")
    return mapping[field]


def _required_true(mapping: Mapping[str, object], field: str, context: str) -> None:
    if _required(mapping, field, context) is not True:
        raise PromotionAttestationBuildError(f"{context}.{field} must be true")


def _required_false(mapping: Mapping[str, object], field: str, context: str) -> None:
    if _required(mapping, field, context) is not False:
        raise PromotionAttestationBuildError(f"{context}.{field} must be false")


def _utc_timestamp(value: object, context: str, *, require_z: bool = False) -> datetime:
    pattern = _RFC3339_Z if require_z else _RFC3339_UTC
    if not isinstance(value, str) or value != value.strip() or not pattern.fullmatch(value):
        suffix = " ending in Z" if require_z else ""
        raise PromotionAttestationBuildError(f"{context} must be an RFC3339 UTC timestamp{suffix}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PromotionAttestationBuildError(f"{context} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PromotionAttestationBuildError(f"{context} must use UTC")
    return parsed.astimezone(UTC)


def parse_promoted_at(value: str) -> datetime:
    """Parse the CLI's deliberately strict RFC3339 ``Z`` timestamp."""
    return _utc_timestamp(value, "--promoted-at", require_z=True)


def _aware_utc(value: object, context: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PromotionAttestationBuildError(f"{context} must be a timezone-aware datetime")
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise PromotionAttestationBuildError(
            f"{context} must be a valid timezone-aware datetime"
        ) from exc


def _sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PromotionAttestationBuildError(f"{context} must be a 64-character lowercase SHA-256")
    return value


def _trimmed_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PromotionAttestationBuildError(f"{context} must be a non-empty, trimmed string")
    return value


def _model_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        raise PromotionAttestationBuildError(f"{context} must be an array")
    models_list: list[str] = []
    for model in value:
        normalized = _trimmed_string(model, context)
        if len(normalized) > 256 or any(
            ord(character) < 32 or ord(character) == 127 for character in normalized
        ):
            raise PromotionAttestationBuildError(
                f"{context} entries must be safe strings of at most 256 characters"
            )
        models_list.append(normalized)
    models = tuple(models_list)
    if models != tuple(sorted(set(models))):
        raise PromotionAttestationBuildError(f"{context} must be sorted and unique")
    return models


def _jsonl_lines(text: str) -> list[str]:
    """Split JSONL only on ASCII LF and require one terminal LF."""
    if not text:
        raise PromotionAttestationBuildError("results must contain at least one JSONL record")
    if "\r" in text:
        raise PromotionAttestationBuildError("results must use ASCII LF line endings")
    if not text.endswith("\n"):
        raise PromotionAttestationBuildError(
            "results must end with one ASCII LF after the final JSON record"
        )
    lines = text[:-1].split("\n")
    if any(not line for line in lines):
        raise PromotionAttestationBuildError(
            "results must contain exactly one JSON object per non-empty line"
        )
    return lines


def _scoreboard_record(
    line: str,
    line_number: int,
    *,
    context_version: str,
    expected: Mapping[str, object],
    case_ids: set[str],
) -> tuple[str, str, bool, tuple[str, ...], tuple[str, ...]]:
    """One exact results record, checked against its ordered case_manifest entry.

    Split out of `_results_scoreboard` for CQ-05 (max-complexity 10). Every
    check, its order, and its message are unchanged. Returns
    ``(case_id, suite, passed, answer_models, judge_models)``.
    """

    if not line.strip():
        raise PromotionAttestationBuildError(
            f"results line {line_number} must contain one JSON object"
        )
    value = _parse_json_text(line, f"results line {line_number}")
    record = _mapping(value, f"results line {line_number}")
    case_id = _trimmed_string(
        _required(record, "case_id", f"results line {line_number}"),
        f"results line {line_number}.case_id",
    )
    if not _CASE_ID.fullmatch(case_id):
        raise PromotionAttestationBuildError(
            f"results line {line_number}.case_id must be a 1-128 character safe identifier"
        )
    # Uniqueness is checked here, not in the caller's loop, so that the error a
    # duplicate raises stays the duplicate error rather than whichever later
    # per-record check the second copy happens to trip first
    # (tests/test_build_promotion_attestation.py::test_rejects_duplicate_case_ids_across_suites).
    if case_id in case_ids:
        raise PromotionAttestationBuildError(f"results contains duplicate case_id: {case_id}")
    case_ids.add(case_id)
    suite = _trimmed_string(
        _required(record, "suite", f"results line {line_number}"),
        f"results line {line_number}.suite",
    )
    passed = _required(record, "passed", f"results line {line_number}")
    if type(passed) is not bool:
        raise PromotionAttestationBuildError(f"results line {line_number}.passed must be a boolean")
    expected_case_id = _trimmed_string(
        expected["case_id"],
        f"summary.attestation.evidence.case_manifest[{line_number - 1}].case_id",
    )
    if not hmac.compare_digest(case_id, expected_case_id):
        raise PromotionAttestationBuildError(
            f"results line {line_number}.case_id does not match the ordered case_manifest"
        )
    result_context = _sha256(
        _required(record, "run_context_version", f"results line {line_number}"),
        f"results line {line_number}.run_context_version",
    )
    if not hmac.compare_digest(result_context, context_version):
        raise PromotionAttestationBuildError(
            f"results line {line_number}.run_context_version does not match "
            "summary.attestation.context_version"
        )
    result_semantics = _sha256(
        _required(record, "case_semantics_version", f"results line {line_number}"),
        f"results line {line_number}.case_semantics_version",
    )
    expected_semantics = _sha256(
        expected["case_semantics_version"],
        f"summary.attestation.evidence.case_manifest[{line_number - 1}].case_semantics_version",
    )
    if not hmac.compare_digest(result_semantics, expected_semantics):
        raise PromotionAttestationBuildError(
            f"results line {line_number}.case_semantics_version does not match "
            "the ordered case_manifest"
        )
    answer = _model_list(
        _required(record, "answer_models_served", f"results line {line_number}"),
        f"results line {line_number}.answer_models_served",
    )
    judge = _model_list(
        _required(record, "judge_models_served", f"results line {line_number}"),
        f"results line {line_number}.judge_models_served",
    )
    if "answer_model_served" in record and record["answer_model_served"] is not None:
        final_answer = _trimmed_string(
            record["answer_model_served"],
            f"results line {line_number}.answer_model_served",
        )
        if final_answer not in answer:
            raise PromotionAttestationBuildError(
                f"results line {line_number}.answer_model_served is absent from "
                "answer_models_served"
            )
    return case_id, suite, passed, answer, judge


def _results_scoreboard(
    results: bytes,
    *,
    context_version: str,
    case_manifest: Sequence[Mapping[str, object]],
) -> tuple[dict[str, dict[str, int]], dict[str, tuple[str, ...]]]:
    """Parse exact JSONL records and independently aggregate promotion scores."""
    try:
        text = results.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromotionAttestationBuildError("results must be valid UTF-8 JSONL") from exc
    lines = _jsonl_lines(text)
    if len(lines) != len(case_manifest):
        raise PromotionAttestationBuildError(
            "results record count must equal summary.attestation.evidence.case_count"
        )
    case_ids: set[str] = set()
    suites: dict[str, dict[str, int]] = {}
    answer_models: set[str] = set()
    judge_models: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        _case_id, suite, passed, answer, judge = _scoreboard_record(
            line,
            line_number,
            context_version=context_version,
            expected=_mapping(
                case_manifest[line_number - 1],
                f"summary.attestation.evidence.case_manifest[{line_number - 1}]",
            ),
            case_ids=case_ids,
        )
        answer_models.update(answer)
        judge_models.update(judge)
        counts = suites.setdefault(suite, {"passed": 0, "total": 0})
        counts["total"] += 1
        counts["passed"] += int(passed)
    return suites, {
        "answer": tuple(sorted(answer_models)),
        "judge": tuple(sorted(judge_models)),
    }


def _validate_eval_attestation(
    value: object,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    attestation = _exact_fields(value, _EVAL_ATTESTATION_FIELDS, "summary.attestation")
    if attestation["attestation_schema"] != EVAL_ATTESTATION_SCHEMA:
        raise PromotionAttestationBuildError(
            "summary.attestation.attestation_schema is unsupported"
        )
    subject = _exact_fields(
        attestation["subject"],
        _EVAL_SUBJECT_FIELDS,
        "summary.attestation.subject",
    )
    evidence = _exact_fields(
        attestation["evidence"],
        _EVAL_EVIDENCE_FIELDS,
        "summary.attestation.evidence",
    )
    protocol = _mapping(attestation["protocol"], "summary.attestation.protocol")
    promotion = _exact_fields(
        attestation["promotion"],
        _EVAL_PROMOTION_FIELDS,
        "summary.attestation.promotion",
    )
    if "protocol_version" not in protocol:
        raise PromotionAttestationBuildError(
            "summary.attestation.protocol.protocol_version is required"
        )
    source_protocol = dict(protocol)
    source_protocol.pop("protocol_version")
    try:
        rebuilt = rebuild_eval_attestation(
            subject=subject,
            suite_version=evidence["suite_version"],  # type: ignore[arg-type]
            case_manifest=evidence["case_manifest"],  # type: ignore[arg-type]
            facts_version=evidence["facts_version"],  # type: ignore[arg-type]
            gtfs_input_version=evidence["gtfs_input_version"],  # type: ignore[arg-type]
            protocol=source_protocol,
            promotion=promotion,
        )
    except (EvalAttestationError, TypeError, ValueError) as exc:
        raise PromotionAttestationBuildError(
            "summary.attestation is not a valid eval-attestation v1 record"
        ) from exc
    if rebuilt != dict(attestation):
        raise PromotionAttestationBuildError(
            "summary.attestation digest or canonical content is invalid"
        )
    return attestation, subject, promotion, evidence


def _runtime_release(value: object) -> RuntimeRelease:
    runtime = _exact_fields(value, _RUNTIME_FIELDS, "runtime")
    try:
        return RuntimeRelease(**{field: runtime[field] for field in _RUNTIME_FIELDS})  # type: ignore[arg-type]
    except (PromotionAttestationError, TypeError, ValueError) as exc:
        raise PromotionAttestationBuildError("runtime release identity is invalid") from exc


def _count(value: object, context: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        qualifier = "positive " if positive else "non-negative "
        raise PromotionAttestationBuildError(f"{context} must be a {qualifier}integer")
    return value


def _validate_execution(summary: Mapping[str, object]) -> int:
    execution = _mapping(_required(summary, "execution", "summary"), "summary.execution")
    cache = _mapping(
        _required(execution, "cache", "summary.execution"),
        "summary.execution.cache",
    )
    _required_false(cache, "enabled", "summary.execution.cache")

    reused = _required(execution, "reused_cases", "summary.execution")
    if type(reused) is not int or reused != 0:
        raise PromotionAttestationBuildError("summary.execution.reused_cases must be 0")

    _required_false(execution, "only_failed", "summary.execution")
    if _required(execution, "since", "summary.execution") is not None:
        raise PromotionAttestationBuildError("summary.execution.since must be null")
    return _count(
        _required(execution, "executed_cases", "summary.execution"),
        "summary.execution.executed_cases",
        positive=True,
    )


def _validate_scoreboard(
    summary: Mapping[str, object],
    result_suites: Mapping[str, Mapping[str, int]],
    executed_cases: int,
) -> None:
    total = _mapping(_required(summary, "total", "summary"), "summary.total")
    summary_total = _count(
        _required(total, "total", "summary.total"),
        "summary.total.total",
        positive=True,
    )
    summary_passed = _count(
        _required(total, "passed", "summary.total"),
        "summary.total.passed",
    )
    results_total = sum(counts["total"] for counts in result_suites.values())
    results_passed = sum(counts["passed"] for counts in result_suites.values())
    if results_total != summary_total or results_total != executed_cases:
        raise PromotionAttestationBuildError(
            "results record count, summary.total.total, and "
            "summary.execution.executed_cases must be equal"
        )
    if summary_passed != results_passed:
        raise PromotionAttestationBuildError(
            "summary.total.passed does not match the exact results"
        )

    raw_summary_suites = _mapping(
        _required(summary, "suites", "summary"),
        "summary.suites",
    )
    for name in raw_summary_suites:
        _trimmed_string(name, "summary.suites key")
    expected_names = set(result_suites)
    actual_names = set(raw_summary_suites)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise PromotionAttestationBuildError(
            "summary.suites does not exactly match results (" + "; ".join(details) + ")"
        )

    for suite_name, expected in result_suites.items():
        entry = _mapping(
            raw_summary_suites[suite_name],
            f"summary.suites.{suite_name}",
        )
        actual_total = _count(
            _required(entry, "total", f"summary.suites.{suite_name}"),
            f"summary.suites.{suite_name}.total",
            positive=True,
        )
        actual_passed = _count(
            _required(entry, "passed", f"summary.suites.{suite_name}"),
            f"summary.suites.{suite_name}.passed",
        )
        if actual_total != expected["total"] or actual_passed != expected["passed"]:
            raise PromotionAttestationBuildError(
                f"summary.suites.{suite_name} counts do not match the exact results"
            )
        pass_rate = _required(
            entry,
            "pass_rate",
            f"summary.suites.{suite_name}",
        )
        expected_pass_rate = round(100 * expected["passed"] / expected["total"], 1)
        if (
            not isinstance(pass_rate, (int, float))
            or isinstance(pass_rate, bool)
            or float(pass_rate) != expected_pass_rate
        ):
            raise PromotionAttestationBuildError(
                f"summary.suites.{suite_name}.pass_rate does not match the exact results"
            )


def _validate_served_models(
    summary: Mapping[str, object],
    observed: Mapping[str, tuple[str, ...]],
) -> None:
    claimed = _exact_fields(
        _required(summary, "served_models", "summary"),
        frozenset({"answer", "judge"}),
        "summary.served_models",
    )
    for kind in ("answer", "judge"):
        values = _model_list(
            claimed[kind],
            f"summary.served_models.{kind}",
        )
        if values != observed[kind]:
            raise PromotionAttestationBuildError(
                f"summary.served_models.{kind} does not match the exact results"
            )


# `build_attestation` below is a long sequence of independent validations over
# one summary. Each block is extracted here for CQ-05 (max-complexity 10), in
# the order the original executed them; no check, message, or precedence
# changed. tests/test_build_promotion_attestation.py exercises each rejection
# path by message, which is what pins that.


def _validate_summary_shape(summary: Mapping[str, object]) -> int:
    """The run-shape preconditions a promotable summary must declare."""

    if _required(summary, "mode", "summary") != "full":
        raise PromotionAttestationBuildError("summary.mode must be 'full'")
    _required_false(summary, "offline", "summary")
    _required_true(summary, "judges_ran", "summary")
    _required_true(summary, "promotion_requested", "summary")
    if _required(summary, "gate_status", "summary") != "passed":
        raise PromotionAttestationBuildError("summary.gate_status must be 'passed'")
    executed_cases = _validate_execution(summary)
    if "replicates" in summary:
        replicates = summary["replicates"]
        if type(replicates) is not int or replicates != 1:
            raise PromotionAttestationBuildError("summary.replicates must be 1 when present")
    return executed_cases


def _validate_subject(subject: Mapping[str, object]) -> None:
    """The attested subject must be a clean, verified, non-drifting checkout."""

    if subject["source_state"] != "clean":
        raise PromotionAttestationBuildError(
            "summary.attestation.subject.source_state must be 'clean'"
        )
    if subject["descriptor_verified"] is not True:
        raise PromotionAttestationBuildError(
            "summary.attestation.subject.descriptor_verified must be true"
        )
    if subject["head_revision"] != subject["source_revision"]:
        raise PromotionAttestationBuildError(
            "summary.attestation.subject.head_revision must equal source_revision"
        )


def _validate_promotion_flags(promotion: Mapping[str, object]) -> None:
    """Every promotion flag true, and no reason recorded against promoting."""

    required_promotion_flags = ("eligible", "live", "uncached", "judges_ran", "gates_passed")
    false_promotion_flags = [
        field for field in required_promotion_flags if promotion[field] is not True
    ]
    if false_promotion_flags:
        raise PromotionAttestationBuildError(
            "summary.attestation.promotion flags must be true ("
            + ", ".join(false_promotion_flags)
            + ")"
        )
    if promotion["reasons"] != []:
        raise PromotionAttestationBuildError("summary.attestation.promotion.reasons must be empty")


def _validate_protocol(summary: Mapping[str, object]) -> None:
    """The recorded protocol must describe a full, online, single-replicate run."""

    protocol = _mapping(
        _mapping(summary["attestation"], "summary.attestation")["protocol"],
        "summary.attestation.protocol",
    )
    if protocol.get("mode") != "full":
        raise PromotionAttestationBuildError("summary.attestation.protocol.mode must be 'full'")
    if protocol.get("offline") is not False:
        raise PromotionAttestationBuildError("summary.attestation.protocol.offline must be false")
    if "replicates" in protocol:
        protocol_replicates = protocol["replicates"]
        if type(protocol_replicates) is not int or protocol_replicates != 1:
            raise PromotionAttestationBuildError(
                "summary.attestation.protocol.replicates must be 1"
            )


def _evaluated_release(subject: Mapping[str, object], runtime: RuntimeRelease) -> LogicalRelease:
    """The release the evaluation ran against, required to equal the runtime's."""

    try:
        evaluated_release = LogicalRelease(
            **{field: subject[field] for field in _LOGICAL_RELEASE_FIELDS}  # type: ignore[arg-type]
        )
    except (PromotionAttestationError, TypeError, ValueError) as exc:
        raise PromotionAttestationBuildError(
            "summary.attestation.subject release identity is invalid"
        ) from exc
    mismatches = [
        field
        for field in _LOGICAL_RELEASE_FIELDS
        if not hmac.compare_digest(getattr(runtime, field), getattr(evaluated_release, field))
    ]
    if mismatches:
        raise PromotionAttestationBuildError(
            "runtime and evaluated release identities differ (" + ", ".join(mismatches) + ")"
        )
    return evaluated_release


def _validate_run_at(summary: Mapping[str, object], promotion: Mapping[str, object]) -> datetime:
    """`promotion.evaluated_at` must be the same instant, and the same text, as `run_at`."""

    run_at_value = _required(summary, "run_at", "summary")
    run_at = _utc_timestamp(run_at_value, "summary.run_at", require_z=True)
    evaluated_at = _utc_timestamp(
        promotion["evaluated_at"],
        "summary.attestation.promotion.evaluated_at",
        require_z=True,
    )
    if promotion["evaluated_at"] != run_at_value or evaluated_at != run_at:
        raise PromotionAttestationBuildError(
            "summary.attestation.promotion.evaluated_at must equal summary.run_at"
        )
    return run_at


def build_attestation(
    runtime_payload: Mapping[str, object],
    summary_bytes: bytes,
    results: bytes,
    *,
    promoted_at: datetime,
    observed_at: datetime | None = None,
) -> PromotionAttestation:
    """Validate three in-memory records and compose the closed contract.

    ``observed_at`` injects the validation clock for deterministic callers. If
    omitted, the current UTC time is observed so the public builder never
    accepts a future promotion timestamp.
    """
    normalized_promoted_at = _aware_utc(promoted_at, "promoted_at")
    validation_time = (
        datetime.now(UTC) if observed_at is None else _aware_utc(observed_at, "observed_at")
    )
    if normalized_promoted_at > validation_time:
        raise PromotionAttestationBuildError(
            "promoted_at must not be later than the observed current time"
        )
    runtime = _runtime_release(runtime_payload)
    summary = parse_json_object(summary_bytes, "summary")

    executed_cases = _validate_summary_shape(summary)
    eval_attestation, subject, promotion, evidence = _validate_eval_attestation(
        _required(summary, "attestation", "summary")
    )
    _validate_subject(subject)
    _validate_promotion_flags(promotion)
    _validate_protocol(summary)
    evaluated_release = _evaluated_release(subject, runtime)
    run_at = _validate_run_at(summary, promotion)

    result_suites, observed_models = _results_scoreboard(
        results,
        context_version=eval_attestation["context_version"],  # type: ignore[arg-type]
        case_manifest=evidence["case_manifest"],  # type: ignore[arg-type]
    )
    _validate_scoreboard(summary, result_suites, executed_cases)
    _validate_served_models(summary, observed_models)
    computed_results_sha256 = hashlib.sha256(results).hexdigest()
    computed_summary_sha256 = hashlib.sha256(summary_bytes).hexdigest()
    claimed_results_sha256 = _sha256(
        _required(summary, "results_sha256", "summary"),
        "summary.results_sha256",
    )
    if not hmac.compare_digest(computed_results_sha256, claimed_results_sha256):
        raise PromotionAttestationBuildError(
            "summary.results_sha256 does not match the exact results bytes"
        )

    try:
        evaluation = EvaluationRun(
            run_id=_required(summary, "run_id", "summary"),  # type: ignore[arg-type]
            run_at=run_at,
            mode="full",
            offline=False,
            cache_enabled=False,
            judges_ran=True,
            evaluated_release=evaluated_release,
            results_sha256=computed_results_sha256,
            summary_sha256=computed_summary_sha256,
            evaluation_attestation_version=eval_attestation["attestation_version"],  # type: ignore[arg-type]
            gate_status="passed",
        )
        return build_promotion_attestation(
            runtime,
            evaluation,
            promoted_at=normalized_promoted_at,
        )
    except (PromotionAttestationError, TypeError, ValueError) as exc:
        raise PromotionAttestationBuildError("promotion attestation fields are invalid") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionAttestationBuildError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_nonfinite_constant(_value: str) -> None:
    raise PromotionAttestationBuildError("JSON contains a non-finite number")


def _parse_json_text(text: str, context: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except PromotionAttestationBuildError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PromotionAttestationBuildError(f"{context} must contain valid JSON") from exc


def parse_json_object(data: bytes, context: str) -> Mapping[str, object]:
    """Parse a UTF-8 JSON object with recursive duplicate-key rejection."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromotionAttestationBuildError(f"{context} must be valid UTF-8 JSON") from exc
    return _mapping(_parse_json_text(text, context), context)


def read_regular_file(path: Path) -> bytes:
    """Read exact bytes from a regular file without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
    except OSError as exc:
        raise PromotionAttestationBuildError(f"input is missing or unreadable: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PromotionAttestationBuildError(f"input must be a regular non-symlink file: {path}")
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PromotionAttestationBuildError(f"input could not be opened safely: {path}") from exc
    chunks: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PromotionAttestationBuildError(f"opened input is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise PromotionAttestationBuildError(f"input could not be read completely: {path}") from exc
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
    ) or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PromotionAttestationBuildError(f"input changed while it was read: {path}")
    return b"".join(chunks)


def write_attestation(attestation: PromotionAttestation, output: Path) -> Path:
    """Durably replace ``output`` with canonical non-secret bytes at mode 0644."""
    if output.is_symlink():
        raise PromotionAttestationBuildError(f"refusing to replace an output symlink: {output}")
    if output.exists() and not output.is_file():
        raise PromotionAttestationBuildError(f"output must be a regular file path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = attestation_bytes(attestation)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise PromotionAttestationBuildError(
            f"promotion attestation could not be written: {output}"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_attestation_file(
    *,
    runtime_path: Path,
    summary_path: Path,
    results_path: Path,
    output_path: Path,
    promoted_at: datetime | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> PromotionAttestation:
    """Read, validate, build, and atomically publish one attestation file."""
    runtime = parse_json_object(read_regular_file(runtime_path), "runtime")
    summary = read_regular_file(summary_path)
    results = read_regular_file(results_path)
    try:
        observed_at = clock()
    except Exception as exc:
        raise PromotionAttestationBuildError(
            "promotion clock could not provide the current time"
        ) from exc
    observed_at = _aware_utc(observed_at, "promotion clock")
    selected_promoted_at = observed_at if promoted_at is None else promoted_at
    attestation = build_attestation(
        runtime,
        summary,
        results,
        promoted_at=selected_promoted_at,
        observed_at=observed_at,
    )
    write_attestation(attestation, output_path)
    return attestation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--promoted-at",
        help="promotion time as RFC3339 UTC ending in Z (defaults to current UTC time)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        promoted_at = parse_promoted_at(args.promoted_at) if args.promoted_at is not None else None
        attestation = build_attestation_file(
            runtime_path=args.runtime,
            summary_path=args.summary,
            results_path=args.results,
            output_path=args.output,
            promoted_at=promoted_at,
        )
    except (
        EvalAttestationError,
        PromotionAttestationBuildError,
        PromotionAttestationError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"promotion attestation build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "attestation_sha256": attestation_digest(attestation),
                "output_path": str(args.output),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
