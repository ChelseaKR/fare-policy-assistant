"""Strict, canonical promotion attestations for immutable releases.

The attestation joins one observed numeric Lambda release to one full, live,
uncached evaluation run.  It is intentionally a small closed contract: callers
cannot add arbitrary metadata, and parsing an attestation never silently drops
unknown fields or duplicate JSON keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

PROMOTION_ATTESTATION_SCHEMA: Final = "fare-assistant.promotion-attestation.v1"

_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CORPUS_VERSION = re.compile(r"^[0-9a-f]{12}$")
_FUNCTION_VERSION = re.compile(r"^[1-9][0-9]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RFC3339_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)

_LOGICAL_RELEASE_FIELDS = (
    "source_revision",
    "config_version",
    "content_version",
    "snapshot_version",
    "release_version",
    "corpus_version",
)
_RUNTIME_RELEASE_FIELDS = (
    *_LOGICAL_RELEASE_FIELDS,
    "artifact_code_sha256",
    "function_version",
)
_EVALUATION_FIELDS = (
    "run_id",
    "run_at",
    "mode",
    "offline",
    "cache_enabled",
    "judges_ran",
    "evaluated_release",
    "results_sha256",
    "summary_sha256",
    "evaluation_attestation_version",
    "gate_status",
)
_ATTESTATION_FIELDS = (
    "schema",
    "runtime_release",
    "evaluation",
    "promoted_at",
)


class PromotionAttestationError(ValueError):
    """The supplied promotion evidence does not satisfy the closed contract."""


# A discoverable alias for callers that name errors after this module.
ReleaseAttestationError = PromotionAttestationError


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PromotionAttestationError(f"{context} must be a non-empty, trimmed string")
    return value


def _matching_string(value: object, pattern: re.Pattern[str], context: str, message: str) -> str:
    text = _string(value, context)
    if not pattern.fullmatch(text):
        raise PromotionAttestationError(f"{context} {message}")
    return text


def _source_revision(value: object, context: str) -> str:
    return _matching_string(
        value,
        _SOURCE_REVISION,
        context,
        "must be a full 40-character lowercase Git object ID",
    )


def _sha256(value: object, context: str) -> str:
    return _matching_string(
        value,
        _SHA256,
        context,
        "must be a 64-character lowercase SHA-256",
    )


def _corpus_version(value: object, context: str) -> str:
    return _matching_string(
        value,
        _CORPUS_VERSION,
        context,
        "must be a 12-character lowercase compatibility digest",
    )


def _function_version(value: object, context: str) -> str:
    if not isinstance(value, str) or not _FUNCTION_VERSION.fullmatch(value):
        raise PromotionAttestationError(
            f"{context} must be a canonical positive numeric Lambda version"
        )
    return value


def _artifact_code_sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PromotionAttestationError(f"{context} must be a canonical AWS CodeSha256 digest")
    encoded = value
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise PromotionAttestationError(
            f"{context} must be a canonical AWS CodeSha256 digest"
        ) from exc
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != encoded:
        raise PromotionAttestationError(f"{context} must be a canonical AWS CodeSha256 digest")
    return encoded


def _run_id(value: object, context: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise PromotionAttestationError(f"{context} must be a 1-128 character safe identifier")
    return value


def _boolean(value: object, expected: bool, context: str) -> bool:
    if type(value) is not bool or value is not expected:
        literal = "true" if expected else "false"
        raise PromotionAttestationError(f"{context} must be {literal}")
    return expected


def _literal(value: object, expected: str, context: str) -> str:
    if value != expected or not isinstance(value, str):
        raise PromotionAttestationError(f"{context} must be {expected!r}")
    return expected


def _utc_datetime(value: object, context: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PromotionAttestationError(f"{context} must be a timezone-aware UTC datetime")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise PromotionAttestationError(f"{context} must be a timezone-aware UTC datetime") from exc
    if offset != timedelta(0):
        raise PromotionAttestationError(f"{context} must be a timezone-aware UTC datetime")
    return value.astimezone(UTC)


def _parse_rfc3339_utc(value: object, context: str) -> datetime:
    text = _string(value, context)
    if not _RFC3339_UTC.fullmatch(text):
        raise PromotionAttestationError(f"{context} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise PromotionAttestationError(
            f"{context} must be an RFC3339 UTC timestamp ending in Z"
        ) from exc
    return _utc_datetime(parsed, context)


def _format_rfc3339_utc(value: datetime) -> str:
    value = _utc_datetime(value, "timestamp")
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PromotionAttestationError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise PromotionAttestationError(f"{context} object keys must be strings")
    return value


def _exact_fields(
    value: object,
    expected: Sequence[str],
    context: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    actual = set(mapping)
    required = set(expected)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise PromotionAttestationError(
            f"{context} has an invalid field set ({'; '.join(details)})"
        )
    return mapping


@dataclass(frozen=True, slots=True)
class LogicalRelease:
    """The behavior-affecting release tuple evaluated by the test harness."""

    source_revision: str
    config_version: str
    content_version: str
    snapshot_version: str
    release_version: str
    corpus_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_revision",
            _source_revision(self.source_revision, "evaluated_release.source_revision"),
        )
        for field in (
            "config_version",
            "content_version",
            "snapshot_version",
            "release_version",
        ):
            object.__setattr__(
                self,
                field,
                _sha256(getattr(self, field), f"evaluated_release.{field}"),
            )
        object.__setattr__(
            self,
            "corpus_version",
            _corpus_version(self.corpus_version, "evaluated_release.corpus_version"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeRelease:
    """The exact immutable Lambda artifact observed at promotion time."""

    source_revision: str
    config_version: str
    content_version: str
    snapshot_version: str
    release_version: str
    corpus_version: str
    artifact_code_sha256: str
    function_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_revision",
            _source_revision(self.source_revision, "runtime_release.source_revision"),
        )
        for field in (
            "config_version",
            "content_version",
            "snapshot_version",
            "release_version",
        ):
            object.__setattr__(
                self,
                field,
                _sha256(getattr(self, field), f"runtime_release.{field}"),
            )
        object.__setattr__(
            self,
            "corpus_version",
            _corpus_version(self.corpus_version, "runtime_release.corpus_version"),
        )
        object.__setattr__(
            self,
            "artifact_code_sha256",
            _artifact_code_sha256(
                self.artifact_code_sha256,
                "runtime_release.artifact_code_sha256",
            ),
        )
        object.__setattr__(
            self,
            "function_version",
            _function_version(self.function_version, "runtime_release.function_version"),
        )

    @property
    def logical_release(self) -> LogicalRelease:
        """Return the behavior-affecting subset used by evaluation."""
        return LogicalRelease(**{field: getattr(self, field) for field in _LOGICAL_RELEASE_FIELDS})


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """Promotion-relevant facts from one completed evaluation run."""

    run_id: str
    run_at: datetime
    mode: str
    offline: bool
    cache_enabled: bool
    judges_ran: bool
    evaluated_release: LogicalRelease
    results_sha256: str
    summary_sha256: str
    evaluation_attestation_version: str
    gate_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _run_id(self.run_id, "evaluation.run_id"))
        object.__setattr__(self, "run_at", _utc_datetime(self.run_at, "evaluation.run_at"))
        object.__setattr__(self, "mode", _literal(self.mode, "full", "evaluation.mode"))
        object.__setattr__(
            self,
            "offline",
            _boolean(self.offline, False, "evaluation.offline"),
        )
        object.__setattr__(
            self,
            "cache_enabled",
            _boolean(self.cache_enabled, False, "evaluation.cache_enabled"),
        )
        object.__setattr__(
            self,
            "judges_ran",
            _boolean(self.judges_ran, True, "evaluation.judges_ran"),
        )
        if not isinstance(self.evaluated_release, LogicalRelease):
            raise PromotionAttestationError("evaluation.evaluated_release must be a LogicalRelease")
        object.__setattr__(
            self,
            "results_sha256",
            _sha256(self.results_sha256, "evaluation.results_sha256"),
        )
        object.__setattr__(
            self,
            "summary_sha256",
            _sha256(self.summary_sha256, "evaluation.summary_sha256"),
        )
        object.__setattr__(
            self,
            "evaluation_attestation_version",
            _sha256(
                self.evaluation_attestation_version,
                "evaluation.evaluation_attestation_version",
            ),
        )
        object.__setattr__(
            self,
            "gate_status",
            _literal(self.gate_status, "passed", "evaluation.gate_status"),
        )


def release_identity_mismatches(
    runtime_release: RuntimeRelease,
    evaluated_release: LogicalRelease,
) -> tuple[str, ...]:
    """Return logical identity field names that differ, in contract order."""
    if not isinstance(runtime_release, RuntimeRelease):
        raise PromotionAttestationError("runtime_release must be a RuntimeRelease")
    if not isinstance(evaluated_release, LogicalRelease):
        raise PromotionAttestationError("evaluated_release must be a LogicalRelease")
    return tuple(
        field
        for field in _LOGICAL_RELEASE_FIELDS
        if getattr(runtime_release, field) != getattr(evaluated_release, field)
    )


@dataclass(frozen=True, slots=True)
class PromotionAttestation:
    """A valid, matching runtime/evaluation promotion decision."""

    runtime_release: RuntimeRelease
    evaluation: EvaluationRun
    promoted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_release, RuntimeRelease):
            raise PromotionAttestationError("runtime_release must be a RuntimeRelease")
        if not isinstance(self.evaluation, EvaluationRun):
            raise PromotionAttestationError("evaluation must be an EvaluationRun")
        object.__setattr__(
            self,
            "promoted_at",
            _utc_datetime(self.promoted_at, "promoted_at"),
        )
        mismatches = release_identity_mismatches(
            self.runtime_release,
            self.evaluation.evaluated_release,
        )
        if mismatches:
            raise PromotionAttestationError(
                "evaluation release does not match runtime release (" + ", ".join(mismatches) + ")"
            )
        if self.promoted_at < self.evaluation.run_at:
            raise PromotionAttestationError("promoted_at must not precede evaluation.run_at")


def build_promotion_attestation(
    runtime_release: RuntimeRelease,
    evaluation: EvaluationRun,
    *,
    promoted_at: datetime,
) -> PromotionAttestation:
    """Build and validate one promotion attestation."""
    return PromotionAttestation(
        runtime_release=runtime_release,
        evaluation=evaluation,
        promoted_at=promoted_at,
    )


def _logical_release_mapping(release: LogicalRelease | RuntimeRelease) -> dict[str, str]:
    return {field: getattr(release, field) for field in _LOGICAL_RELEASE_FIELDS}


def _attestation_mapping(attestation: PromotionAttestation) -> dict[str, object]:
    if not isinstance(attestation, PromotionAttestation):
        raise PromotionAttestationError("attestation must be a PromotionAttestation")
    return {
        "schema": PROMOTION_ATTESTATION_SCHEMA,
        "runtime_release": {
            **_logical_release_mapping(attestation.runtime_release),
            "artifact_code_sha256": attestation.runtime_release.artifact_code_sha256,
            "function_version": attestation.runtime_release.function_version,
        },
        "evaluation": {
            "run_id": attestation.evaluation.run_id,
            "run_at": _format_rfc3339_utc(attestation.evaluation.run_at),
            "mode": attestation.evaluation.mode,
            "offline": attestation.evaluation.offline,
            "cache_enabled": attestation.evaluation.cache_enabled,
            "judges_ran": attestation.evaluation.judges_ran,
            "evaluated_release": _logical_release_mapping(attestation.evaluation.evaluated_release),
            "results_sha256": attestation.evaluation.results_sha256,
            "summary_sha256": attestation.evaluation.summary_sha256,
            "evaluation_attestation_version": (
                attestation.evaluation.evaluation_attestation_version
            ),
            "gate_status": attestation.evaluation.gate_status,
        },
        "promoted_at": _format_rfc3339_utc(attestation.promoted_at),
    }


def attestation_bytes(attestation: PromotionAttestation) -> bytes:
    """Serialize an attestation as canonical UTF-8 JSON plus one newline."""
    mapping = _attestation_mapping(attestation)
    try:
        encoded = json.dumps(
            mapping,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PromotionAttestationError("attestation is not canonical-JSON compatible") from exc
    return encoded + b"\n"


def attestation_digest(attestation: PromotionAttestation) -> str:
    """Return the lowercase SHA-256 of :func:`attestation_bytes`."""
    return hashlib.sha256(attestation_bytes(attestation)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise PromotionAttestationError("attestation JSON contains a duplicate object key")
        parsed[key] = value
    return parsed


def _reject_nonfinite_constant(_value: str) -> None:
    raise PromotionAttestationError("attestation JSON contains a non-finite number")


def _load_json(payload: bytes | str) -> object:
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PromotionAttestationError("attestation must be valid UTF-8 JSON") from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise PromotionAttestationError("attestation must be bytes or text")
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except PromotionAttestationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PromotionAttestationError("attestation must be valid JSON") from exc


def _parse_logical_release(value: object) -> LogicalRelease:
    mapping = _exact_fields(
        value,
        _LOGICAL_RELEASE_FIELDS,
        "evaluation.evaluated_release",
    )
    return LogicalRelease(**{field: mapping[field] for field in _LOGICAL_RELEASE_FIELDS})  # type: ignore[arg-type]


def _parse_runtime_release(value: object) -> RuntimeRelease:
    mapping = _exact_fields(value, _RUNTIME_RELEASE_FIELDS, "runtime_release")
    return RuntimeRelease(**{field: mapping[field] for field in _RUNTIME_RELEASE_FIELDS})  # type: ignore[arg-type]


def _parse_evaluation(value: object) -> EvaluationRun:
    mapping = _exact_fields(value, _EVALUATION_FIELDS, "evaluation")
    return EvaluationRun(
        run_id=mapping["run_id"],  # type: ignore[arg-type]
        run_at=_parse_rfc3339_utc(mapping["run_at"], "evaluation.run_at"),
        mode=mapping["mode"],  # type: ignore[arg-type]
        offline=mapping["offline"],  # type: ignore[arg-type]
        cache_enabled=mapping["cache_enabled"],  # type: ignore[arg-type]
        judges_ran=mapping["judges_ran"],  # type: ignore[arg-type]
        evaluated_release=_parse_logical_release(mapping["evaluated_release"]),
        results_sha256=mapping["results_sha256"],  # type: ignore[arg-type]
        summary_sha256=mapping["summary_sha256"],  # type: ignore[arg-type]
        evaluation_attestation_version=mapping["evaluation_attestation_version"],  # type: ignore[arg-type]
        gate_status=mapping["gate_status"],  # type: ignore[arg-type]
    )


def parse_promotion_attestation(payload: bytes | str) -> PromotionAttestation:
    """Parse untrusted JSON with duplicate-key and exact-field rejection."""
    mapping = _exact_fields(_load_json(payload), _ATTESTATION_FIELDS, "attestation")
    if mapping["schema"] != PROMOTION_ATTESTATION_SCHEMA:
        raise PromotionAttestationError("attestation.schema is unsupported")
    return PromotionAttestation(
        runtime_release=_parse_runtime_release(mapping["runtime_release"]),
        evaluation=_parse_evaluation(mapping["evaluation"]),
        promoted_at=_parse_rfc3339_utc(mapping["promoted_at"], "promoted_at"),
    )


def _freshness_budget(value: object) -> timedelta:
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise PromotionAttestationError("freshness budget must be a positive timedelta")
    return value


def attestation_is_fresh(
    attestation: PromotionAttestation,
    freshness_budget: timedelta,
    *,
    clock: Callable[[], datetime],
) -> bool:
    """Return whether promotion age is in ``[0, budget]`` at ``clock()``.

    Both ends are deliberate: exact-boundary evidence is fresh, while evidence
    stamped in the future is not.  The injected clock makes that policy
    deterministic in deployment code and tests.
    """
    if not isinstance(attestation, PromotionAttestation):
        raise PromotionAttestationError("attestation must be a PromotionAttestation")
    budget = _freshness_budget(freshness_budget)
    if not callable(clock):
        raise PromotionAttestationError("clock must be callable")
    try:
        now = clock()
    except Exception as exc:
        raise PromotionAttestationError("clock could not provide the current time") from exc
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PromotionAttestationError("clock must return a timezone-aware datetime")
    try:
        now_utc = now.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise PromotionAttestationError("clock must return a timezone-aware datetime") from exc
    age = now_utc - attestation.promoted_at
    return timedelta(0) <= age <= budget


__all__ = [
    "PROMOTION_ATTESTATION_SCHEMA",
    "EvaluationRun",
    "LogicalRelease",
    "PromotionAttestation",
    "PromotionAttestationError",
    "ReleaseAttestationError",
    "RuntimeRelease",
    "attestation_bytes",
    "attestation_digest",
    "attestation_is_fresh",
    "build_promotion_attestation",
    "parse_promotion_attestation",
    "release_identity_mismatches",
]
