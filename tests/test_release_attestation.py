"""Closed-contract tests for immutable-release promotion attestations."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from assistant.release_attestation import (
    PROMOTION_ATTESTATION_SCHEMA,
    EvaluationRun,
    LogicalRelease,
    PromotionAttestation,
    PromotionAttestationError,
    RuntimeRelease,
    attestation_bytes,
    attestation_digest,
    attestation_is_fresh,
    build_promotion_attestation,
    parse_promotion_attestation,
    release_identity_mismatches,
)

_SOURCE_REVISION = "a" * 40
_CONFIG_VERSION = "b" * 64
_CONTENT_VERSION = "c" * 64
_SNAPSHOT_VERSION = "d" * 64
_RELEASE_VERSION = "e" * 64
_CORPUS_VERSION = "f" * 12
_RESULTS_SHA256 = "1" * 64
_SUMMARY_SHA256 = "2" * 64
_EVALUATION_ATTESTATION_VERSION = "3" * 64
_ARTIFACT_CODE_SHA256 = base64.b64encode(bytes(range(32))).decode("ascii")
_RUN_AT = datetime(2026, 7, 30, 20, 15, 1, 123456, tzinfo=UTC)
_PROMOTED_AT = datetime(2026, 7, 30, 20, 30, 2, tzinfo=UTC)


def _logical_release() -> LogicalRelease:
    return LogicalRelease(
        source_revision=_SOURCE_REVISION,
        config_version=_CONFIG_VERSION,
        content_version=_CONTENT_VERSION,
        snapshot_version=_SNAPSHOT_VERSION,
        release_version=_RELEASE_VERSION,
        corpus_version=_CORPUS_VERSION,
    )


def _runtime_release() -> RuntimeRelease:
    return RuntimeRelease(
        source_revision=_SOURCE_REVISION,
        config_version=_CONFIG_VERSION,
        content_version=_CONTENT_VERSION,
        snapshot_version=_SNAPSHOT_VERSION,
        release_version=_RELEASE_VERSION,
        corpus_version=_CORPUS_VERSION,
        artifact_code_sha256=_ARTIFACT_CODE_SHA256,
        function_version="11",
    )


def _evaluation(**changes: object) -> EvaluationRun:
    values: dict[str, object] = {
        "run_id": "eval-20260730T201501Z",
        "run_at": _RUN_AT,
        "mode": "full",
        "offline": False,
        "cache_enabled": False,
        "judges_ran": True,
        "evaluated_release": _logical_release(),
        "results_sha256": _RESULTS_SHA256,
        "summary_sha256": _SUMMARY_SHA256,
        "evaluation_attestation_version": _EVALUATION_ATTESTATION_VERSION,
        "gate_status": "passed",
    }
    values.update(changes)
    return EvaluationRun(**values)  # type: ignore[arg-type]


def _attestation(**changes: object) -> PromotionAttestation:
    values: dict[str, object] = {
        "runtime_release": _runtime_release(),
        "evaluation": _evaluation(),
        "promoted_at": _PROMOTED_AT,
    }
    values.update(changes)
    return build_promotion_attestation(**values)  # type: ignore[arg-type]


def _payload(attestation: PromotionAttestation | None = None) -> dict[str, object]:
    parsed = json.loads(attestation_bytes(attestation or _attestation()))
    assert isinstance(parsed, dict)
    return parsed


def _json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _nested(payload: dict[str, object], *path: str) -> dict[str, object]:
    current: object = payload
    for part in path:
        assert isinstance(current, dict)
        current = current[part]
    assert isinstance(current, dict)
    return current


def test_builds_exact_closed_contract_and_round_trips() -> None:
    attestation = _attestation()
    payload = _payload(attestation)

    assert set(payload) == {"schema", "runtime_release", "evaluation", "promoted_at"}
    assert payload["schema"] == PROMOTION_ATTESTATION_SCHEMA
    assert set(_nested(payload, "runtime_release")) == {
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "artifact_code_sha256",
        "function_version",
    }
    assert set(_nested(payload, "evaluation")) == {
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
    }
    assert set(_nested(payload, "evaluation", "evaluated_release")) == {
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
    }
    assert payload["promoted_at"] == "2026-07-30T20:30:02Z"
    assert _nested(payload, "evaluation")["run_at"] == "2026-07-30T20:15:01.123456Z"
    assert parse_promotion_attestation(attestation_bytes(attestation)) == attestation
    assert attestation_bytes(parse_promotion_attestation(attestation_bytes(attestation))) == (
        attestation_bytes(attestation)
    )


def test_canonical_serialization_and_digest_are_stable() -> None:
    serialized = attestation_bytes(_attestation())

    assert serialized.endswith(b"\n")
    assert serialized.count(b"\n") == 1
    assert b" " not in serialized
    assert attestation_digest(_attestation()) == hashlib.sha256(serialized).hexdigest()
    assert (
        attestation_digest(_attestation())
        == "52cd8cbf50938204522c4c3d83073fb9d2d26e3f38965e62498e87c1db8f6db0"
    )


def test_dataclasses_are_immutable() -> None:
    for value in (_logical_release(), _runtime_release(), _evaluation(), _attestation()):
        field = dataclasses.fields(value)[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field.name, "changed")


_MISSING_FIELD_CASES = [
    *(((), field) for field in ("schema", "runtime_release", "evaluation", "promoted_at")),
    *(
        (("runtime_release",), field)
        for field in (
            "source_revision",
            "config_version",
            "content_version",
            "snapshot_version",
            "release_version",
            "corpus_version",
            "artifact_code_sha256",
            "function_version",
        )
    ),
    *(
        (("evaluation",), field)
        for field in (
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
    ),
    *(
        (("evaluation", "evaluated_release"), field)
        for field in (
            "source_revision",
            "config_version",
            "content_version",
            "snapshot_version",
            "release_version",
            "corpus_version",
        )
    ),
]


@pytest.mark.parametrize(("path", "field"), _MISSING_FIELD_CASES)
def test_every_object_rejects_missing_fields(path: tuple[str, ...], field: str) -> None:
    payload = _payload()
    del _nested(payload, *path)[field]

    with pytest.raises(PromotionAttestationError, match="invalid field set"):
        parse_promotion_attestation(_json(payload))


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("runtime_release",),
        ("evaluation",),
        ("evaluation", "evaluated_release"),
    ],
)
def test_every_object_rejects_unknown_fields(path: tuple[str, ...]) -> None:
    payload = _payload()
    _nested(payload, *path)["unexpected"] = "never accepted"

    with pytest.raises(PromotionAttestationError, match="invalid field set"):
        parse_promotion_attestation(_json(payload))


def test_rejects_duplicate_top_level_key() -> None:
    raw = attestation_bytes(_attestation()).decode()
    duplicate = raw.replace(
        f'"schema":"{PROMOTION_ATTESTATION_SCHEMA}"',
        f'"schema":"{PROMOTION_ATTESTATION_SCHEMA}","schema":"{PROMOTION_ATTESTATION_SCHEMA}"',
        1,
    )

    with pytest.raises(PromotionAttestationError, match="duplicate"):
        parse_promotion_attestation(duplicate)


def test_rejects_duplicate_nested_key() -> None:
    raw = attestation_bytes(_attestation()).decode()
    duplicate = raw.replace(
        '"function_version":"11"',
        '"function_version":"11","function_version":"12"',
    )

    with pytest.raises(PromotionAttestationError, match="duplicate"):
        parse_promotion_attestation(duplicate)


def test_rejects_nonfinite_json_constants_before_contract_validation() -> None:
    raw = attestation_bytes(_attestation()).decode()
    nonfinite = raw.replace('"offline":false', '"offline":NaN')

    with pytest.raises(PromotionAttestationError, match="non-finite"):
        parse_promotion_attestation(nonfinite)


@pytest.mark.parametrize(
    "value",
    [
        "A" * 40,
        "a" * 39,
        "a" * 41,
        "g" * 40,
    ],
)
def test_rejects_invalid_source_revision(value: str) -> None:
    with pytest.raises(PromotionAttestationError, match="40-character lowercase"):
        dataclasses.replace(_runtime_release(), source_revision=value)


@pytest.mark.parametrize(
    "field",
    ["config_version", "content_version", "snapshot_version", "release_version"],
)
@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_rejects_invalid_logical_sha256_fields(field: str, value: str) -> None:
    with pytest.raises(PromotionAttestationError, match="64-character lowercase"):
        dataclasses.replace(_logical_release(), **{field: value})


@pytest.mark.parametrize("value", ["A" * 12, "a" * 11, "a" * 13, "g" * 12])
def test_rejects_invalid_corpus_version(value: str) -> None:
    with pytest.raises(PromotionAttestationError, match="12-character lowercase"):
        dataclasses.replace(_runtime_release(), corpus_version=value)


@pytest.mark.parametrize(
    "field",
    ["summary_sha256", "evaluation_attestation_version"],
)
@pytest.mark.parametrize("value", ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_rejects_invalid_required_evaluation_digests(field: str, value: str) -> None:
    with pytest.raises(PromotionAttestationError, match="must be"):
        _evaluation(**{field: value})


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64",
        base64.b64encode(b"short").decode(),
        _ARTIFACT_CODE_SHA256.rstrip("="),
        _ARTIFACT_CODE_SHA256[:-2] + "9=",
    ],
)
def test_rejects_noncanonical_aws_artifact_digest(value: str) -> None:
    with pytest.raises(PromotionAttestationError, match="canonical AWS"):
        dataclasses.replace(_runtime_release(), artifact_code_sha256=value)


@pytest.mark.parametrize("value", ["0", "01", "-1", "1.0", "$LATEST", 1, True])
def test_rejects_noncanonical_function_version(value: object) -> None:
    with pytest.raises(PromotionAttestationError, match="positive numeric"):
        dataclasses.replace(_runtime_release(), function_version=value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "smoke", "must be 'full'"),
        ("offline", True, "must be false"),
        ("offline", 0, "must be false"),
        ("cache_enabled", True, "must be false"),
        ("cache_enabled", 0, "must be false"),
        ("judges_ran", False, "must be true"),
        ("judges_ran", 1, "must be true"),
        ("gate_status", "failed", "must be 'passed'"),
    ],
)
def test_rejects_nonpromotable_evaluation_flags(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(PromotionAttestationError, match=message):
        _evaluation(**{field: value})


@pytest.mark.parametrize("run_id", ["", " contains-space", "contains space", "x" * 129])
def test_rejects_unsafe_run_id(run_id: str) -> None:
    with pytest.raises(PromotionAttestationError, match="safe identifier"):
        _evaluation(run_id=run_id)


def test_rejects_wrong_schema() -> None:
    payload = _payload()
    payload["schema"] = "fare-assistant.promotion-attestation.v2"

    with pytest.raises(PromotionAttestationError, match="unsupported"):
        parse_promotion_attestation(_json(payload))


_LOGICAL_REPLACEMENTS = {
    "source_revision": "2" * 40,
    "config_version": "3" * 64,
    "content_version": "4" * 64,
    "snapshot_version": "5" * 64,
    "release_version": "6" * 64,
    "corpus_version": "7" * 12,
}


@pytest.mark.parametrize("field", tuple(_LOGICAL_REPLACEMENTS))
def test_comparison_returns_each_mismatched_logical_field(field: str) -> None:
    evaluated = dataclasses.replace(
        _logical_release(),
        **{field: _LOGICAL_REPLACEMENTS[field]},
    )

    assert release_identity_mismatches(_runtime_release(), evaluated) == (field,)


def test_comparison_returns_all_mismatches_in_contract_order() -> None:
    evaluated = dataclasses.replace(_logical_release(), **_LOGICAL_REPLACEMENTS)

    assert release_identity_mismatches(_runtime_release(), evaluated) == (
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
    )


def test_runtime_only_realization_fields_do_not_change_logical_agreement() -> None:
    runtime = dataclasses.replace(
        _runtime_release(),
        artifact_code_sha256=base64.b64encode(b"x" * 32).decode(),
        function_version="12",
    )

    assert release_identity_mismatches(runtime, _logical_release()) == ()
    assert runtime.logical_release == _logical_release()


@pytest.mark.parametrize("field", tuple(_LOGICAL_REPLACEMENTS))
def test_attestation_rejects_every_release_identity_mismatch(field: str) -> None:
    evaluated = dataclasses.replace(
        _logical_release(),
        **{field: _LOGICAL_REPLACEMENTS[field]},
    )

    with pytest.raises(PromotionAttestationError, match=field):
        _attestation(evaluation=_evaluation(evaluated_release=evaluated))


def test_valid_value_tampering_is_detected_by_external_digest() -> None:
    original = _attestation()
    payload = _payload(original)
    _nested(payload, "evaluation")["results_sha256"] = "9" * 64
    changed = parse_promotion_attestation(_json(payload))

    assert changed.evaluation.results_sha256 == "9" * 64
    assert attestation_digest(changed) != attestation_digest(original)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("summary_sha256", "8" * 64),
        ("evaluation_attestation_version", "9" * 64),
    ],
)
def test_each_required_evaluation_digest_changes_canonical_digest(
    field: str,
    replacement: str,
) -> None:
    original = _attestation()
    changed_evaluation = dataclasses.replace(original.evaluation, **{field: replacement})
    changed = _attestation(evaluation=changed_evaluation)

    assert attestation_bytes(changed) != attestation_bytes(original)
    assert attestation_digest(changed) != attestation_digest(original)


def test_release_tampering_fails_closed_even_when_new_value_is_well_formed() -> None:
    payload = _payload()
    _nested(payload, "runtime_release")["release_version"] = "9" * 64

    with pytest.raises(PromotionAttestationError, match="release_version"):
        parse_promotion_attestation(_json(payload))


def test_promotion_cannot_precede_evaluation() -> None:
    with pytest.raises(PromotionAttestationError, match="must not precede"):
        _attestation(promoted_at=_RUN_AT - timedelta(microseconds=1))


@pytest.mark.parametrize(
    "field",
    ["run_at", "promoted_at"],
)
def test_builder_rejects_naive_timestamps(field: str) -> None:
    naive = datetime(2026, 7, 30, 20, 15, 1)

    with pytest.raises(PromotionAttestationError, match="timezone-aware UTC"):
        if field == "run_at":
            _evaluation(run_at=naive)
        else:
            _attestation(promoted_at=naive)


@pytest.mark.parametrize(
    "field",
    ["run_at", "promoted_at"],
)
def test_builder_rejects_non_utc_timestamps(field: str) -> None:
    non_utc = datetime(2026, 7, 30, 13, 15, 1, tzinfo=timezone(timedelta(hours=-7)))

    with pytest.raises(PromotionAttestationError, match="timezone-aware UTC"):
        if field == "run_at":
            _evaluation(run_at=non_utc)
        else:
            _attestation(promoted_at=non_utc)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-30T20:30:02+00:00",
        "2026-07-30T13:30:02-07:00",
        "2026-07-30 20:30:02Z",
        "2026-07-30T20:30Z",
        "2026-02-30T20:30:02Z",
    ],
)
def test_parser_requires_valid_rfc3339_utc_z_timestamp(timestamp: str) -> None:
    payload = _payload()
    payload["promoted_at"] = timestamp

    with pytest.raises(PromotionAttestationError, match="RFC3339 UTC"):
        parse_promotion_attestation(_json(payload))


def test_parser_accepts_rfc3339_utc_fraction_and_canonicalizes_it() -> None:
    payload = _payload()
    payload["promoted_at"] = "2026-07-30T20:30:02.1Z"

    parsed = parse_promotion_attestation(_json(payload))

    assert parsed.promoted_at.microsecond == 100_000
    assert json.loads(attestation_bytes(parsed))["promoted_at"] == ("2026-07-30T20:30:02.100000Z")


def test_freshness_is_inclusive_at_zero_and_exact_budget() -> None:
    attestation = _attestation()
    budget = timedelta(hours=1)

    assert attestation_is_fresh(attestation, budget, clock=lambda: _PROMOTED_AT)
    assert attestation_is_fresh(
        attestation,
        budget,
        clock=lambda: _PROMOTED_AT + budget,
    )


def test_freshness_rejects_one_microsecond_past_budget_and_future_evidence() -> None:
    attestation = _attestation()
    budget = timedelta(hours=1)

    assert not attestation_is_fresh(
        attestation,
        budget,
        clock=lambda: _PROMOTED_AT + budget + timedelta(microseconds=1),
    )
    assert not attestation_is_fresh(
        attestation,
        budget,
        clock=lambda: _PROMOTED_AT - timedelta(microseconds=1),
    )


def test_freshness_accepts_an_aware_non_utc_clock_and_normalizes_it() -> None:
    pacific = timezone(timedelta(hours=-7))
    local_boundary = (_PROMOTED_AT + timedelta(hours=1)).astimezone(pacific)

    assert attestation_is_fresh(
        _attestation(),
        timedelta(hours=1),
        clock=lambda: local_boundary,
    )


@pytest.mark.parametrize(
    "budget",
    [timedelta(0), timedelta(microseconds=-1), 1, True, None],
)
def test_freshness_rejects_nonpositive_or_wrong_type_budget(budget: object) -> None:
    with pytest.raises(PromotionAttestationError, match="positive timedelta"):
        attestation_is_fresh(
            _attestation(),
            budget,  # type: ignore[arg-type]
            clock=lambda: _PROMOTED_AT,
        )


def test_freshness_rejects_invalid_or_failed_clock() -> None:
    with pytest.raises(PromotionAttestationError, match="timezone-aware"):
        attestation_is_fresh(
            _attestation(),
            timedelta(hours=1),
            clock=lambda: datetime(2026, 7, 30, 20, 30, 2),
        )

    def failed_clock() -> datetime:
        raise RuntimeError("ambient secret must not escape")

    with pytest.raises(PromotionAttestationError, match="could not provide") as exc_info:
        attestation_is_fresh(
            _attestation(),
            timedelta(hours=1),
            clock=failed_clock,
        )
    assert "ambient secret" not in str(exc_info.value)


class _BrokenTimezone(tzinfo):
    def utcoffset(self, _value: datetime | None) -> timedelta | None:
        raise ValueError("timezone internals must not escape")

    def dst(self, _value: datetime | None) -> timedelta | None:
        return None

    def tzname(self, _value: datetime | None) -> str | None:
        return "broken"


def test_timestamp_validation_wraps_broken_timezone_without_echoing_details() -> None:
    broken = datetime(2026, 7, 30, 20, 15, 1, tzinfo=_BrokenTimezone())

    with pytest.raises(PromotionAttestationError, match="timezone-aware UTC") as exc_info:
        _evaluation(run_at=broken)
    assert "timezone internals" not in str(exc_info.value)

    with pytest.raises(PromotionAttestationError, match="timezone-aware") as exc_info:
        attestation_is_fresh(
            _attestation(),
            timedelta(hours=1),
            clock=lambda: broken,
        )
    assert "timezone internals" not in str(exc_info.value)


def test_public_api_rejects_wrong_object_types() -> None:
    with pytest.raises(PromotionAttestationError, match="RuntimeRelease"):
        release_identity_mismatches(None, _logical_release())  # type: ignore[arg-type]
    with pytest.raises(PromotionAttestationError, match="LogicalRelease"):
        release_identity_mismatches(_runtime_release(), None)  # type: ignore[arg-type]
    with pytest.raises(PromotionAttestationError, match="LogicalRelease"):
        _evaluation(evaluated_release={})
    with pytest.raises(PromotionAttestationError, match="RuntimeRelease"):
        PromotionAttestation(  # type: ignore[arg-type]
            runtime_release={},
            evaluation=_evaluation(),
            promoted_at=_PROMOTED_AT,
        )
    with pytest.raises(PromotionAttestationError, match="EvaluationRun"):
        PromotionAttestation(  # type: ignore[arg-type]
            runtime_release=_runtime_release(),
            evaluation={},
            promoted_at=_PROMOTED_AT,
        )
    with pytest.raises(PromotionAttestationError, match="PromotionAttestation"):
        attestation_bytes(None)  # type: ignore[arg-type]
    with pytest.raises(PromotionAttestationError, match="PromotionAttestation"):
        attestation_is_fresh(  # type: ignore[arg-type]
            None,
            timedelta(hours=1),
            clock=lambda: _PROMOTED_AT,
        )
    with pytest.raises(PromotionAttestationError, match="callable"):
        attestation_is_fresh(
            _attestation(),
            timedelta(hours=1),
            clock=None,  # type: ignore[arg-type]
        )


def test_serializer_rejects_nonfinite_value_even_if_object_is_force_corrupted() -> None:
    attestation = _attestation()
    object.__setattr__(attestation.evaluation, "run_id", float("nan"))

    with pytest.raises(PromotionAttestationError, match="canonical-JSON"):
        attestation_bytes(attestation)


def test_serializer_does_not_read_or_emit_ambient_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-history-signing-material"
    monkeypatch.setenv("FPA_HISTORY_HMAC_KEY", secret)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    serialized = attestation_bytes(_attestation())

    assert secret.encode() not in serialized
    assert b"secret" not in serialized.lower()
    assert b"api_key" not in serialized.lower()


def test_unknown_secret_field_is_rejected_without_echoing_its_value() -> None:
    secret = "never-echo-this-value"
    payload = _payload()
    _nested(payload, "evaluation")["api_key"] = secret

    with pytest.raises(PromotionAttestationError) as exc_info:
        parse_promotion_attestation(_json(payload))
    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"",
        b"{} trailing",
        "[]",
        1,
        None,
    ],
)
def test_parser_rejects_invalid_input(payload: object) -> None:
    with pytest.raises(PromotionAttestationError):
        parse_promotion_attestation(payload)  # type: ignore[arg-type]
