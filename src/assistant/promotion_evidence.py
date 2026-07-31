"""Verify one immutable promotion-evidence set from exact local files.

This module is deliberately independent of AWS, HTTP, and deployment layout.
Callers supply the three fixed paths they intend to publish or display:

* the completed evaluation ``summary.json``;
* its exact ``results.jsonl``; and
* the canonical promotion attestation that binds those bytes to a runtime.

Only sanitized score, identity, and model metadata is returned. Evaluation
questions, answers, rationales, and retrieved passages are parsed only as
untrusted JSON fields and are never copied into the result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Never

from assistant.release_attestation import (
    LogicalRelease,
    PromotionAttestation,
    PromotionAttestationError,
    RuntimeRelease,
    attestation_bytes,
    attestation_digest,
    parse_promotion_attestation,
)
from assistant.release_identity import ReleaseIdentityError, build_release_identity

MAX_SUMMARY_BYTES: Final = 2 * 1024 * 1024
MAX_RESULTS_BYTES: Final = 64 * 1024 * 1024
MAX_PROMOTION_BYTES: Final = 256 * 1024

EVAL_ATTESTATION_SCHEMA: Final = "fare-assistant.eval-attestation.v1"
EVAL_PROTOCOL_SCHEMA: Final = "fare-assistant.eval-protocol.v1"
EVAL_CONTEXT_SCHEMA: Final = "fare-assistant.eval-context.v1"
EVAL_SUITE_SCHEMA: Final = "fare-assistant.eval-suite.v1"

_READ_CHUNK_BYTES = 1024 * 1024
_MAX_RESULTS_RECORDS = 50_000
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CORPUS_VERSION = re.compile(r"^[0-9a-f]{12}$")
_RFC3339_Z = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
_SCHEMA = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+\.v[1-9][0-9]*$")

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
        "source_state",
        "head_revision",
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
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
_LOGICAL_RELEASE_FIELDS = (
    "source_revision",
    "config_version",
    "content_version",
    "snapshot_version",
    "release_version",
    "corpus_version",
)


class PromotionEvidenceError(ValueError):
    """Promotion evidence is missing, unsafe, internally inconsistent, or invalid."""

    def __init__(self, *mismatches: str, detail: str | None = None):
        unique = tuple(dict.fromkeys(mismatches)) or ("promotion_evidence",)
        message = detail or "promotion evidence is invalid"
        super().__init__(message)
        self.mismatches = unique


@dataclass(frozen=True, slots=True)
class Score:
    """An independently verified pass count."""

    passed: int
    total: int

    @property
    def pass_rate(self) -> float:
        return round(100 * self.passed / self.total, 1)

    def as_dict(self) -> dict[str, int | float]:
        return {
            "passed": self.passed,
            "total": self.total,
            "pass_rate": self.pass_rate,
        }


@dataclass(frozen=True, slots=True)
class SuiteScore:
    """One named suite and its independently verified score."""

    name: str
    score: Score

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, **self.score.as_dict()}


@dataclass(frozen=True, slots=True)
class ServedModels:
    """Exact distinct served-model identifiers observed in result records."""

    answer: tuple[str, ...]
    judge: tuple[str, ...]

    def as_dict(self) -> dict[str, list[str]]:
        return {"answer": list(self.answer), "judge": list(self.judge)}


@dataclass(frozen=True, slots=True)
class CaseEvidence:
    """The safe, non-content-bearing subset of one evaluation result."""

    case_id: str
    suite: str
    passed: bool
    run_context_version: str
    case_semantics_version: str
    served_models: ServedModels

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "case_id": self.case_id,
            "suite": self.suite,
            "passed": self.passed,
        }
        result["run_context_version"] = self.run_context_version
        result["case_semantics_version"] = self.case_semantics_version
        result["served_models"] = self.served_models.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """A frozen, sanitized view of one verified promotion-evidence set."""

    status: Literal["verified", "warning"]
    warnings: tuple[str, ...]
    attestation: PromotionAttestation
    run_context_version: str
    age_seconds: int
    freshness_budget_seconds: int
    summary_sha256: str
    results_sha256: str
    promotion_sha256: str
    total: Score
    suites: tuple[SuiteScore, ...]
    served_models: ServedModels
    cases: tuple[CaseEvidence, ...]

    @property
    def fresh(self) -> bool:
        return self.status == "verified"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible copy containing no evaluation content."""

        runtime = self.attestation.runtime_release
        evaluation = self.attestation.evaluation
        result: dict[str, object] = {
            "status": self.status,
            "warnings": list(self.warnings),
            "fresh": self.fresh,
            "age_seconds": self.age_seconds,
            "max_age_seconds": self.freshness_budget_seconds,
            "run_id": evaluation.run_id,
            "run_at": _format_rfc3339_z(evaluation.run_at),
            "promoted_at": _format_rfc3339_z(self.attestation.promoted_at),
            "runtime_release": _runtime_release_dict(runtime),
            "run_context_version": self.run_context_version,
            "evaluation_attestation_version": evaluation.evaluation_attestation_version,
            "summary_sha256": self.summary_sha256,
            "results_sha256": self.results_sha256,
            "promotion_sha256": self.promotion_sha256,
            "total": self.total.as_dict(),
            "suites": [suite.as_dict() for suite in self.suites],
            "cases": [case.as_dict() for case in self.cases],
        }
        result["served_models"] = self.served_models.as_dict()
        return result


def _invalid(code: str, detail: str) -> Never:
    raise PromotionEvidenceError(code, detail=detail)


def _file_fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_bounded_regular_file(path: Path, *, limit: int, context: str) -> bytes:
    """Read exact bytes without following a final symlink or accepting a race."""

    if not isinstance(path, Path):
        _invalid(f"{context}.file", f"{context} path must be a pathlib.Path")
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise PromotionEvidenceError(
            f"{context}.file",
            detail=f"{context} file is missing or unreadable",
        ) from exc
    if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
        _invalid(
            f"{context}.file",
            f"{context} must be a regular non-symlink file",
        )
    if before_path.st_size > limit:
        _invalid(f"{context}.size", f"{context} exceeds its {limit}-byte limit")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PromotionEvidenceError(
            f"{context}.file",
            detail=f"{context} could not be opened safely",
        ) from exc

    chunks: list[bytes] = []
    opened: os.stat_result | None = None
    after_descriptor: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            _invalid(f"{context}.file", f"opened {context} is not a regular file")
        if _file_fingerprint(opened) != _file_fingerprint(before_path):
            _invalid(f"{context}.changed", f"{context} changed while it was opened")
        if opened.st_size > limit:
            _invalid(f"{context}.size", f"{context} exceeds its {limit}-byte limit")
        consumed = 0
        while True:
            requested = min(_READ_CHUNK_BYTES, limit - consumed + 1)
            chunk = os.read(descriptor, requested)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > limit:
                _invalid(f"{context}.size", f"{context} exceeds its {limit}-byte limit")
            chunks.append(chunk)
        after_descriptor = os.fstat(descriptor)
    except PromotionEvidenceError:
        raise
    except OSError as exc:
        raise PromotionEvidenceError(
            f"{context}.file",
            detail=f"{context} could not be read completely",
        ) from exc
    finally:
        os.close(descriptor)

    assert opened is not None
    assert after_descriptor is not None
    try:
        after_path = path.lstat()
    except OSError as exc:
        raise PromotionEvidenceError(
            f"{context}.changed",
            detail=f"{context} changed while it was read",
        ) from exc
    if _file_fingerprint(opened) != _file_fingerprint(after_descriptor) or _file_fingerprint(
        opened
    ) != _file_fingerprint(after_path):
        _invalid(f"{context}.changed", f"{context} changed while it was read")
    payload = b"".join(chunks)
    if len(payload) != opened.st_size:
        _invalid(f"{context}.changed", f"{context} changed while it was read")
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid("json.duplicate_key", "JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    _invalid("json.nonfinite", "JSON contains a non-finite numeric value")


def _parse_json(data: bytes, *, context: str) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromotionEvidenceError(
            f"{context}.json",
            detail=f"{context} must be valid UTF-8 JSON",
        ) from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except PromotionEvidenceError as exc:
        raise PromotionEvidenceError(
            f"{context}.json",
            *exc.mismatches,
            detail=str(exc),
        ) from exc
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise PromotionEvidenceError(
            f"{context}.json",
            detail=f"{context} must contain valid JSON",
        ) from exc


def _mapping(value: object, *, code: str, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _invalid(code, f"{context} must be a JSON object")
    return value


def _exact_fields(
    value: object,
    expected: frozenset[str],
    *,
    code: str,
    context: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, code=code, context=context)
    if set(mapping) != set(expected):
        _invalid(code, f"{context} has an invalid field set")
    return mapping


def _required(mapping: Mapping[str, object], field: str, *, code: str) -> object:
    if field not in mapping:
        _invalid(code, f"{code} is required")
    return mapping[field]


def _true(value: object, *, code: str) -> None:
    if value is not True:
        _invalid(code, f"{code} must be true")


def _false(value: object, *, code: str) -> None:
    if value is not False:
        _invalid(code, f"{code} must be false")


def _sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _invalid(code, f"{code} must be a lowercase SHA-256")
    return value


def _safe_text(value: object, *, code: str, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _invalid(code, f"{code} must be a safe, trimmed string")
    return value


def _count(value: object, *, code: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        _invalid(code, f"{code} must be an integer of at least {minimum}")
    return value


def _parse_rfc3339_z(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339_Z.fullmatch(value):
        _invalid(code, f"{code} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PromotionEvidenceError(
            code,
            detail=f"{code} must be an RFC3339 UTC timestamp ending in Z",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _invalid(code, f"{code} must use UTC")
    return parsed.astimezone(UTC)


def _format_rfc3339_z(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PromotionEvidenceError(
            "evaluation.attestation",
            detail="evaluation attestation is not canonical-JSON compatible",
        ) from exc


def _canonical_digest(schema: str, value: object) -> str:
    if not _SCHEMA.fullmatch(schema):
        _invalid("evaluation.attestation", "evaluation attestation schema is invalid")
    return hashlib.sha256(schema.encode("ascii") + b"\0" + _canonical_json(value)).hexdigest()


def _logical_release_from_subject(subject: Mapping[str, object]) -> LogicalRelease:
    try:
        return LogicalRelease(
            source_revision=subject["source_revision"],  # type: ignore[arg-type]
            config_version=subject["config_version"],  # type: ignore[arg-type]
            content_version=subject["content_version"],  # type: ignore[arg-type]
            snapshot_version=subject["snapshot_version"],  # type: ignore[arg-type]
            release_version=subject["release_version"],  # type: ignore[arg-type]
            corpus_version=subject["corpus_version"],  # type: ignore[arg-type]
        )
    except (KeyError, PromotionAttestationError, TypeError, ValueError) as exc:
        raise PromotionEvidenceError(
            "evaluation.subject.release",
            detail="evaluation subject release identity is invalid",
        ) from exc


def _logical_release_dict(value: LogicalRelease | RuntimeRelease) -> dict[str, str]:
    return {field: getattr(value, field) for field in _LOGICAL_RELEASE_FIELDS}


def _runtime_release_dict(value: RuntimeRelease) -> dict[str, str]:
    return {
        **_logical_release_dict(value),
        "artifact_code_sha256": value.artifact_code_sha256,
        "function_version": value.function_version,
    }


@dataclass(frozen=True, slots=True)
class _EvalIdentity:
    context_version: str
    attestation_version: str
    evaluated_at: datetime
    subject_release: LogicalRelease
    case_manifest: tuple[tuple[str, str], ...]


def _eval_case_manifest(
    value: object,
    *,
    case_count: int,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        _invalid(
            "evaluation.evidence.case_manifest",
            "evaluation case_manifest must be an ordered array",
        )
    if len(value) != case_count:
        _invalid(
            "evaluation.evidence.case_count",
            "evaluation case_count must equal the exact case_manifest length",
        )

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    expected_fields = frozenset({"case_id", "case_semantics_version"})
    for index, raw in enumerate(value):
        entry = _exact_fields(
            raw,
            expected_fields,
            code=f"evaluation.evidence.case_manifest.{index}",
            context=f"summary.attestation.evidence.case_manifest[{index}]",
        )
        case_id = _safe_text(
            entry["case_id"],
            code=f"evaluation.evidence.case_manifest.{index}.case_id",
        )
        if not _CASE_ID.fullmatch(case_id):
            _invalid(
                f"evaluation.evidence.case_manifest.{index}.case_id",
                "evaluation case_id is not a safe identifier",
            )
        if case_id in seen:
            _invalid(
                "evaluation.evidence.case_manifest.duplicate",
                f"evaluation case_manifest contains duplicate case_id: {case_id}",
            )
        seen.add(case_id)
        semantics = _sha256(
            entry["case_semantics_version"],
            code=f"evaluation.evidence.case_manifest.{index}.case_semantics_version",
        )
        entries.append((case_id, semantics))
    return tuple(entries)


def _validate_eval_attestation(value: object) -> _EvalIdentity:
    attestation = _exact_fields(
        value,
        _EVAL_ATTESTATION_FIELDS,
        code="evaluation.attestation",
        context="summary.attestation",
    )
    if attestation["attestation_schema"] != EVAL_ATTESTATION_SCHEMA:
        _invalid("evaluation.attestation.schema", "evaluation attestation schema is unsupported")

    subject = _exact_fields(
        attestation["subject"],
        _EVAL_SUBJECT_FIELDS,
        code="evaluation.subject",
        context="summary.attestation.subject",
    )
    if subject["source_state"] != "clean":
        _invalid("evaluation.subject.source_state", "evaluated source must be clean")
    source_revision = subject["source_revision"]
    if (
        not isinstance(source_revision, str)
        or not _SOURCE_REVISION.fullmatch(source_revision)
        or subject["head_revision"] != source_revision
    ):
        _invalid(
            "evaluation.subject.source_revision",
            "evaluated source revision must equal the clean full Git HEAD",
        )
    _true(
        subject["descriptor_verified"],
        code="evaluation.subject.descriptor_verified",
    )
    for field in ("config_version", "content_version", "snapshot_version", "release_version"):
        _sha256(subject[field], code=f"evaluation.subject.{field}")
    corpus = subject["corpus_version"]
    if not isinstance(corpus, str) or not _CORPUS_VERSION.fullmatch(corpus):
        _invalid(
            "evaluation.subject.corpus_version",
            "evaluation subject corpus_version is invalid",
        )
    subject_release = _logical_release_from_subject(subject)
    try:
        expected_release = build_release_identity(
            subject_release.source_revision,
            subject_release.config_version,
            content_version=subject_release.content_version,
            snapshot_version=subject_release.snapshot_version,
        )
    except ReleaseIdentityError as exc:
        raise PromotionEvidenceError(
            "evaluation.subject.release_version",
            detail="evaluated release tuple is invalid",
        ) from exc
    if not hmac.compare_digest(expected_release.release_version, subject_release.release_version):
        _invalid(
            "evaluation.subject.release_version",
            "evaluated release_version does not match its release tuple",
        )

    evidence = _exact_fields(
        attestation["evidence"],
        _EVAL_EVIDENCE_FIELDS,
        code="evaluation.evidence",
        context="summary.attestation.evidence",
    )
    suite_version = _sha256(
        evidence["suite_version"],
        code="evaluation.evidence.suite_version",
    )
    for field in ("facts_version", "gtfs_input_version"):
        _sha256(evidence[field], code=f"evaluation.evidence.{field}")
    case_count = _count(
        evidence["case_count"],
        code="evaluation.evidence.case_count",
        positive=True,
    )
    if case_count > _MAX_RESULTS_RECORDS:
        _invalid(
            "evaluation.evidence.case_count",
            "evaluation case_count exceeds the supported results limit",
        )
    case_manifest = _eval_case_manifest(
        evidence["case_manifest"],
        case_count=case_count,
    )
    canonical_manifest = [
        {
            "case_id": case_id,
            "case_semantics_version": semantics,
        }
        for case_id, semantics in case_manifest
    ]
    expected_suite_version = _canonical_digest(
        EVAL_SUITE_SCHEMA,
        {"case_manifest": canonical_manifest},
    )
    if not hmac.compare_digest(suite_version, expected_suite_version):
        _invalid(
            "evaluation.evidence.suite_version",
            "evaluation suite_version does not match the exact ordered case_manifest",
        )

    protocol = _mapping(
        attestation["protocol"],
        code="evaluation.protocol",
        context="summary.attestation.protocol",
    )
    protocol_version = _sha256(
        _required(
            protocol,
            "protocol_version",
            code="evaluation.protocol.protocol_version",
        ),
        code="evaluation.protocol.protocol_version",
    )
    source_protocol = dict(protocol)
    source_protocol.pop("protocol_version")
    if not source_protocol:
        _invalid("evaluation.protocol", "evaluation protocol must not be empty")
    expected_protocol_version = _canonical_digest(EVAL_PROTOCOL_SCHEMA, source_protocol)
    if not hmac.compare_digest(protocol_version, expected_protocol_version):
        _invalid(
            "evaluation.protocol.protocol_version",
            "evaluation protocol digest is invalid",
        )
    if protocol.get("mode") != "full":
        _invalid("evaluation.protocol.mode", "evaluation protocol mode must be full")
    _false(protocol.get("offline"), code="evaluation.protocol.offline")
    _true(protocol.get("run_judges"), code="evaluation.protocol.run_judges")
    _false(protocol.get("cache_enabled"), code="evaluation.protocol.cache_enabled")
    if protocol.get("replicates") != 1 or type(protocol.get("replicates")) is not int:
        _invalid("evaluation.protocol.replicates", "evaluation protocol replicates must be 1")

    promotion = _exact_fields(
        attestation["promotion"],
        _EVAL_PROMOTION_FIELDS,
        code="evaluation.promotion",
        context="summary.attestation.promotion",
    )
    for field in ("eligible", "live", "uncached", "judges_ran", "gates_passed"):
        _true(promotion[field], code=f"evaluation.promotion.{field}")
    if promotion["reasons"] != []:
        _invalid("evaluation.promotion.reasons", "eligible promotion reasons must be empty")
    evaluated_at = _parse_rfc3339_z(
        promotion["evaluated_at"],
        code="evaluation.promotion.evaluated_at",
    )

    context = {
        "subject": dict(subject),
        "evidence": dict(evidence),
        "protocol": dict(protocol),
    }
    context_version = _sha256(
        attestation["context_version"],
        code="evaluation.context_version",
    )
    expected_context_version = _canonical_digest(EVAL_CONTEXT_SCHEMA, context)
    if not hmac.compare_digest(context_version, expected_context_version):
        _invalid("evaluation.context_version", "evaluation context digest is invalid")

    without_version = {
        "attestation_schema": attestation["attestation_schema"],
        **context,
        "promotion": dict(promotion),
        "context_version": context_version,
    }
    attestation_version = _sha256(
        attestation["attestation_version"],
        code="evaluation.attestation_version",
    )
    expected_attestation_version = _canonical_digest(
        EVAL_ATTESTATION_SCHEMA,
        without_version,
    )
    if not hmac.compare_digest(attestation_version, expected_attestation_version):
        _invalid(
            "evaluation.attestation_version",
            "evaluation attestation digest is invalid",
        )
    return _EvalIdentity(
        context_version=context_version,
        attestation_version=attestation_version,
        evaluated_at=evaluated_at,
        subject_release=subject_release,
        case_manifest=case_manifest,
    )


def _model_list(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _invalid(code, f"{code} must be an array")
    models = tuple(_safe_text(item, code=code, maximum=256) for item in value)
    if models != tuple(sorted(set(models))):
        _invalid(code, f"{code} must be sorted and unique")
    return models


def _required_record_models(
    record: Mapping[str, object],
    *,
    line_number: int,
) -> ServedModels:
    answer = _model_list(
        _required(
            record,
            "answer_models_served",
            code=f"results.line_{line_number}.answer_models_served",
        ),
        code=f"results.line_{line_number}.answer_models_served",
    )
    judge = _model_list(
        _required(
            record,
            "judge_models_served",
            code=f"results.line_{line_number}.judge_models_served",
        ),
        code=f"results.line_{line_number}.judge_models_served",
    )
    if "answer_model_served" in record:
        final_answer = record["answer_model_served"]
        if final_answer is not None:
            model = _safe_text(
                final_answer,
                code=f"results.line_{line_number}.answer_model_served",
                maximum=256,
            )
            if model not in answer:
                _invalid(
                    f"results.line_{line_number}.answer_model_served",
                    "final served answer model is absent from the complete served-model set",
                )
    return ServedModels(answer=answer, judge=judge)


def _required_sha(record: Mapping[str, object], field: str, *, line_number: int) -> str:
    return _sha256(
        _required(
            record,
            field,
            code=f"results.line_{line_number}.{field}",
        ),
        code=f"results.line_{line_number}.{field}",
    )


def _jsonl_lines(text: str) -> list[str]:
    """Split JSONL only on ASCII LF and require one terminal LF."""
    if not text:
        _invalid("results.empty", "results must contain at least one JSONL record")
    if "\r" in text:
        _invalid("results.line_endings", "results must use ASCII LF line endings")
    if not text.endswith("\n"):
        _invalid(
            "results.terminal_lf",
            "results must end with one ASCII LF after the final JSON record",
        )
    lines = text[:-1].split("\n")
    if any(not line for line in lines):
        _invalid(
            "results.empty_line",
            "results must contain exactly one JSON object per non-empty line",
        )
    return lines


def _parse_results(
    data: bytes,
    *,
    context_version: str,
    case_manifest: tuple[tuple[str, str], ...],
) -> tuple[CaseEvidence, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromotionEvidenceError(
            "results.json",
            detail="results must be valid UTF-8 JSONL",
        ) from exc
    lines = _jsonl_lines(text)
    if len(lines) > _MAX_RESULTS_RECORDS:
        _invalid("results.count", "results contains too many records")
    seen: set[str] = set()
    cases: list[CaseEvidence] = []
    for line_number, line in enumerate(lines, 1):
        if line_number > len(case_manifest):
            _invalid(
                "results.case_manifest",
                "result record count does not match the attested case_manifest",
            )
        if not line:
            _invalid(
                f"results.line_{line_number}",
                "every results line must contain exactly one JSON object",
            )
        value = _parse_json(line.encode("utf-8"), context=f"results.line_{line_number}")
        record = _mapping(
            value,
            code=f"results.line_{line_number}",
            context=f"results line {line_number}",
        )
        case_id = _safe_text(
            _required(
                record,
                "case_id",
                code=f"results.line_{line_number}.case_id",
            ),
            code=f"results.line_{line_number}.case_id",
        )
        if not _CASE_ID.fullmatch(case_id):
            _invalid(
                f"results.line_{line_number}.case_id",
                "result case_id is not a safe identifier",
            )
        if case_id in seen:
            _invalid("results.case_id.duplicate", f"duplicate result case_id: {case_id}")
        seen.add(case_id)
        suite = _safe_text(
            _required(
                record,
                "suite",
                code=f"results.line_{line_number}.suite",
            ),
            code=f"results.line_{line_number}.suite",
        )
        passed = _required(
            record,
            "passed",
            code=f"results.line_{line_number}.passed",
        )
        if type(passed) is not bool:
            _invalid(
                f"results.line_{line_number}.passed",
                "result passed value must be a boolean",
            )
        expected_case_id, expected_semantics = case_manifest[line_number - 1]
        if not hmac.compare_digest(case_id, expected_case_id):
            _invalid(
                f"results.line_{line_number}.case_id",
                "result case_id does not match the ordered attested case_manifest",
            )
        run_context = _required_sha(record, "run_context_version", line_number=line_number)
        if not hmac.compare_digest(run_context, context_version):
            _invalid(
                f"results.line_{line_number}.run_context_version",
                "result run context does not match the evaluation attestation",
            )
        case_semantics = _required_sha(
            record,
            "case_semantics_version",
            line_number=line_number,
        )
        if not hmac.compare_digest(case_semantics, expected_semantics):
            _invalid(
                f"results.line_{line_number}.case_semantics_version",
                "result case semantics do not match the ordered attested case_manifest",
            )
        cases.append(
            CaseEvidence(
                case_id=case_id,
                suite=suite,
                passed=passed,
                run_context_version=run_context,
                case_semantics_version=case_semantics,
                served_models=_required_record_models(record, line_number=line_number),
            )
        )
    if len(cases) != len(case_manifest):
        _invalid(
            "results.case_manifest",
            "result record count does not match the attested case_manifest",
        )
    return tuple(cases)


def _aggregate_cases(
    cases: tuple[CaseEvidence, ...],
) -> tuple[Score, tuple[SuiteScore, ...]]:
    suite_counts: dict[str, list[int]] = {}
    total_passed = 0
    for case in cases:
        counts = suite_counts.setdefault(case.suite, [0, 0])
        counts[1] += 1
        if case.passed:
            counts[0] += 1
            total_passed += 1
    return (
        Score(passed=total_passed, total=len(cases)),
        tuple(
            SuiteScore(name=name, score=Score(passed=counts[0], total=counts[1]))
            for name, counts in sorted(suite_counts.items())
        ),
    )


def _validate_scoreboard(
    summary: Mapping[str, object],
    *,
    total: Score,
    suites: tuple[SuiteScore, ...],
) -> None:
    summary_total = _mapping(
        _required(summary, "total", code="summary.total"),
        code="summary.total",
        context="summary.total",
    )
    total_passed = _count(
        _required(summary_total, "passed", code="summary.total.passed"),
        code="summary.total.passed",
    )
    total_cases = _count(
        _required(summary_total, "total", code="summary.total.total"),
        code="summary.total.total",
        positive=True,
    )
    if total_passed > total_cases or (total_passed, total_cases) != (
        total.passed,
        total.total,
    ):
        _invalid("summary.total", "summary total does not match exact result records")

    summary_suites = _mapping(
        _required(summary, "suites", code="summary.suites"),
        code="summary.suites",
        context="summary.suites",
    )
    expected = {suite.name: suite.score for suite in suites}
    if set(summary_suites) != set(expected):
        _invalid("summary.suites", "summary suites do not exactly match result records")
    for name, score in expected.items():
        entry = _mapping(
            summary_suites[name],
            code=f"summary.suites.{name}",
            context=f"summary.suites.{name}",
        )
        passed = _count(
            _required(entry, "passed", code=f"summary.suites.{name}.passed"),
            code=f"summary.suites.{name}.passed",
        )
        count = _count(
            _required(entry, "total", code=f"summary.suites.{name}.total"),
            code=f"summary.suites.{name}.total",
            positive=True,
        )
        if passed > count or (passed, count) != (score.passed, score.total):
            _invalid(
                f"summary.suites.{name}",
                "summary suite score does not match exact result records",
            )
        pass_rate = _required(
            entry,
            "pass_rate",
            code=f"summary.suites.{name}.pass_rate",
        )
        if (
            not isinstance(pass_rate, (int, float))
            or isinstance(pass_rate, bool)
            or float(pass_rate) != score.pass_rate
        ):
            _invalid(
                f"summary.suites.{name}.pass_rate",
                "summary suite pass rate does not match exact result records",
            )


def _validate_execution(summary: Mapping[str, object], *, result_count: int) -> None:
    execution = _mapping(
        _required(summary, "execution", code="summary.execution"),
        code="summary.execution",
        context="summary.execution",
    )
    cache = _mapping(
        _required(execution, "cache", code="summary.execution.cache"),
        code="summary.execution.cache",
        context="summary.execution.cache",
    )
    _false(cache.get("enabled"), code="summary.execution.cache.enabled")
    _false(execution.get("only_failed"), code="summary.execution.only_failed")
    if execution.get("since") is not None:
        _invalid("summary.execution.since", "summary.execution.since must be null")
    if execution.get("reused_cases") != 0 or type(execution.get("reused_cases")) is not int:
        _invalid(
            "summary.execution.reused_cases",
            "summary.execution.reused_cases must be zero",
        )
    executed_cases = execution.get("executed_cases")
    if type(executed_cases) is not int or executed_cases != result_count:
        _invalid(
            "summary.execution.executed_cases",
            "every result case must have been executed in this uncached run",
        )


def _union_served_models(cases: tuple[CaseEvidence, ...]) -> ServedModels:
    return ServedModels(
        answer=tuple(sorted({model for case in cases for model in case.served_models.answer})),
        judge=tuple(sorted({model for case in cases for model in case.served_models.judge})),
    )


def _validate_summary_served_models(
    summary: Mapping[str, object],
    observed: ServedModels,
) -> ServedModels:
    value = _mapping(
        _required(summary, "served_models", code="summary.served_models"),
        code="summary.served_models",
        context="summary.served_models",
    )
    if set(value) != {"answer", "judge"}:
        _invalid("summary.served_models", "summary.served_models has an invalid field set")
    claimed = ServedModels(
        answer=_model_list(value["answer"], code="summary.served_models.answer"),
        judge=_model_list(value["judge"], code="summary.served_models.judge"),
    )
    if claimed != observed:
        _invalid(
            "summary.served_models",
            "summary served models do not match exact result records",
        )
    return claimed


def _logical_release_matches(left: LogicalRelease, right: LogicalRelease) -> bool:
    return all(
        hmac.compare_digest(getattr(left, field), getattr(right, field))
        for field in _LOGICAL_RELEASE_FIELDS
    )


def _clock_now(clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        _invalid("clock", "clock must be callable")
    try:
        now = clock()
    except Exception as exc:
        raise PromotionEvidenceError(
            "clock",
            detail="clock could not provide the current time",
        ) from exc
    if not isinstance(now, datetime) or now.tzinfo is None:
        _invalid("clock", "clock must return a timezone-aware datetime")
    try:
        return now.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise PromotionEvidenceError(
            "clock",
            detail="clock must return a valid timezone-aware datetime",
        ) from exc


def _freshness_budget(value: timedelta) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        _invalid("freshness_budget", "freshness_budget must be a positive timedelta")
    return value


def verify_promotion_evidence(
    *,
    summary_path: Path,
    results_path: Path,
    promotion_path: Path,
    freshness_budget: timedelta,
    clock: Callable[[], datetime],
) -> PromotionEvidence:
    """Verify exact promotion evidence and return a frozen sanitized result.

    Integrity, promotion eligibility, and future-dated evidence fail closed.
    Evidence older than ``freshness_budget`` remains authentic but returns
    ``status == "warning"`` with the ``evaluation.stale`` warning.
    """

    budget = _freshness_budget(freshness_budget)
    now = _clock_now(clock)
    summary_bytes = _read_bounded_regular_file(
        summary_path,
        limit=MAX_SUMMARY_BYTES,
        context="summary",
    )
    results_bytes = _read_bounded_regular_file(
        results_path,
        limit=MAX_RESULTS_BYTES,
        context="results",
    )
    promotion_bytes = _read_bounded_regular_file(
        promotion_path,
        limit=MAX_PROMOTION_BYTES,
        context="promotion",
    )

    try:
        attestation = parse_promotion_attestation(promotion_bytes)
    except PromotionAttestationError as exc:
        raise PromotionEvidenceError(
            "promotion.attestation",
            detail="promotion attestation is invalid",
        ) from exc
    canonical_promotion = attestation_bytes(attestation)
    if not hmac.compare_digest(promotion_bytes, canonical_promotion):
        _invalid(
            "promotion.canonical",
            "promotion attestation bytes are not canonical",
        )

    summary_sha256 = hashlib.sha256(summary_bytes).hexdigest()
    results_sha256 = hashlib.sha256(results_bytes).hexdigest()
    evaluation = attestation.evaluation
    if not hmac.compare_digest(summary_sha256, evaluation.summary_sha256):
        _invalid(
            "summary.sha256",
            "exact summary bytes do not match the promotion attestation",
        )
    if not hmac.compare_digest(results_sha256, evaluation.results_sha256):
        _invalid(
            "results.sha256",
            "exact result bytes do not match the promotion attestation",
        )

    summary = _mapping(
        _parse_json(summary_bytes, context="summary"),
        code="summary.json",
        context="summary",
    )
    if summary.get("mode") != "full":
        _invalid("summary.mode", "summary.mode must be full")
    _false(summary.get("offline"), code="summary.offline")
    _true(summary.get("judges_ran"), code="summary.judges_ran")
    _true(summary.get("promotion_requested"), code="summary.promotion_requested")
    if summary.get("gate_status") != "passed":
        _invalid("summary.gate_status", "summary.gate_status must be passed")
    if "replicates" in summary and (
        type(summary["replicates"]) is not int or summary["replicates"] != 1
    ):
        _invalid("summary.replicates", "summary.replicates must be one when present")

    eval_identity = _validate_eval_attestation(
        _required(summary, "attestation", code="summary.attestation")
    )
    if not _logical_release_matches(
        eval_identity.subject_release,
        evaluation.evaluated_release,
    ):
        _invalid(
            "evaluation.release",
            "summary evaluation release differs from the promoted release",
        )
    if not hmac.compare_digest(
        eval_identity.attestation_version,
        evaluation.evaluation_attestation_version,
    ):
        _invalid(
            "evaluation.attestation_version",
            "summary evaluation attestation differs from the promoted evaluation",
        )

    run_id = summary.get("run_id")
    if run_id != evaluation.run_id:
        _invalid("evaluation.run_id", "summary and promotion run IDs differ")
    summary_run_at = _parse_rfc3339_z(summary.get("run_at"), code="evaluation.run_at")
    if summary_run_at != evaluation.run_at or eval_identity.evaluated_at != evaluation.run_at:
        _invalid("evaluation.run_at", "summary, evaluation, and promotion run times differ")

    claimed_results_sha256 = _sha256(
        summary.get("results_sha256"),
        code="summary.results_sha256",
    )
    if not hmac.compare_digest(claimed_results_sha256, results_sha256):
        _invalid(
            "summary.results_sha256",
            "summary results digest does not match the exact result bytes",
        )
    if (
        "corpus_version" in summary
        and summary["corpus_version"] != eval_identity.subject_release.corpus_version
    ):
        _invalid(
            "summary.corpus_version",
            "summary corpus version differs from the promoted release",
        )

    cases = _parse_results(
        results_bytes,
        context_version=eval_identity.context_version,
        case_manifest=eval_identity.case_manifest,
    )
    total, suites = _aggregate_cases(cases)
    _validate_execution(summary, result_count=total.total)
    _validate_scoreboard(summary, total=total, suites=suites)
    served_models = _validate_summary_served_models(summary, _union_served_models(cases))

    age = now - evaluation.run_at
    if age < timedelta(0):
        _invalid("evaluation.run_at.future", "evaluation run time is in the future")
    if attestation.promoted_at > now:
        _invalid(
            "promotion.promoted_at.future",
            "promotion time is in the future",
        )
    stale = age > budget
    warnings = ("evaluation.stale",) if stale else ()
    return PromotionEvidence(
        status="warning" if stale else "verified",
        warnings=warnings,
        attestation=attestation,
        run_context_version=eval_identity.context_version,
        age_seconds=int(age.total_seconds()),
        freshness_budget_seconds=int(budget.total_seconds()),
        summary_sha256=summary_sha256,
        results_sha256=results_sha256,
        promotion_sha256=attestation_digest(attestation),
        total=total,
        suites=suites,
        served_models=served_models,
        cases=cases,
    )


__all__ = [
    "MAX_PROMOTION_BYTES",
    "MAX_RESULTS_BYTES",
    "MAX_SUMMARY_BYTES",
    "CaseEvidence",
    "PromotionEvidence",
    "PromotionEvidenceError",
    "Score",
    "ServedModels",
    "SuiteScore",
    "verify_promotion_evidence",
]
