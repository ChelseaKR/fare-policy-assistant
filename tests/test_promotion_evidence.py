"""Adversarial tests for exact local promotion-evidence verification."""

from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from assistant.promotion_evidence import (
    MAX_SUMMARY_BYTES,
    PromotionEvidenceError,
    verify_promotion_evidence,
)
from assistant.release_attestation import (
    EvaluationRun,
    LogicalRelease,
    RuntimeRelease,
    attestation_bytes,
    build_promotion_attestation,
)
from assistant.release_identity import build_release_identity
from evals.attestation import (
    ATTESTATION_SCHEMA,
    CONTEXT_SCHEMA,
    PROTOCOL_SCHEMA,
    SUITE_SCHEMA,
    canonical_digest,
)
from evals.attestation import (
    build_attestation as build_eval_attestation,
)

_SOURCE_REVISION = "a" * 40
_CONFIG_VERSION = "b" * 64
_CONTENT_VERSION = "c" * 64
_SNAPSHOT_VERSION = "d" * 64
_CORPUS_VERSION = "e" * 12
_RELEASE_VERSION = build_release_identity(
    _SOURCE_REVISION,
    _CONFIG_VERSION,
    content_version=_CONTENT_VERSION,
    snapshot_version=_SNAPSHOT_VERSION,
).release_version
_RUN_ID = "20260730T201501Z"
_RUN_AT = datetime(2026, 7, 30, 20, 15, 1, tzinfo=UTC)
_PROMOTED_AT = _RUN_AT + timedelta(minutes=5)
_ARTIFACT_SHA256 = base64.b64encode(bytes(range(32))).decode("ascii")
_CASE_MANIFEST = [
    {"case_id": "policy.basic", "case_semantics_version": "6" * 64},
    {"case_id": "safety.refusal", "case_semantics_version": "7" * 64},
    {"case_id": "safety.boundary", "case_semantics_version": "8" * 64},
]


@dataclass(frozen=True)
class _EvidenceFiles:
    summary_path: Path
    results_path: Path
    promotion_path: Path
    summary: dict[str, object]
    results: tuple[dict[str, object], ...]
    eval_attestation: dict[str, object]


def _logical_release(**changes: str) -> LogicalRelease:
    values = {
        "source_revision": _SOURCE_REVISION,
        "config_version": _CONFIG_VERSION,
        "content_version": _CONTENT_VERSION,
        "snapshot_version": _SNAPSHOT_VERSION,
        "release_version": _RELEASE_VERSION,
        "corpus_version": _CORPUS_VERSION,
    }
    values.update(changes)
    return LogicalRelease(**values)


def _runtime_release(logical: LogicalRelease | None = None) -> RuntimeRelease:
    release = logical or _logical_release()
    return RuntimeRelease(
        **{
            field: getattr(release, field)
            for field in (
                "source_revision",
                "config_version",
                "content_version",
                "snapshot_version",
                "release_version",
                "corpus_version",
            )
        },
        artifact_code_sha256=_ARTIFACT_SHA256,
        function_version="11",
    )


def _eval_attestation(
    *,
    promotion_changes: dict[str, object] | None = None,
) -> dict[str, object]:
    promotion: dict[str, object] = {
        "eligible": True,
        "live": True,
        "uncached": True,
        "judges_ran": True,
        "gates_passed": True,
        "reasons": [],
        "evaluated_at": "2026-07-30T20:15:01Z",
    }
    promotion.update(promotion_changes or {})
    return build_eval_attestation(
        subject={
            "source_state": "clean",
            "head_revision": _SOURCE_REVISION,
            "source_revision": _SOURCE_REVISION,
            "config_version": _CONFIG_VERSION,
            "content_version": _CONTENT_VERSION,
            "snapshot_version": _SNAPSHOT_VERSION,
            "release_version": _RELEASE_VERSION,
            "corpus_version": _CORPUS_VERSION,
            "descriptor_verified": True,
        },
        suite_version=canonical_digest(
            SUITE_SCHEMA,
            {"case_manifest": _CASE_MANIFEST},
        ),
        case_manifest=_CASE_MANIFEST,
        facts_version="2" * 64,
        gtfs_input_version="3" * 64,
        protocol={
            "mode": "full",
            "offline": False,
            "provider": "bedrock",
            "requested_models": {"answer": "answer-requested", "judge": "judge-requested"},
            "prompt_versions": {"answer": "4" * 64},
            "run_judges": True,
            "replicates": 1,
            "cache_enabled": False,
            "jobs": 4,
            "evaluator_version": "5" * 64,
        },
        promotion=promotion,
    )


def _restamp_eval_attestation(attestation: dict[str, object]) -> None:
    protocol = attestation["protocol"]
    subject = attestation["subject"]
    evidence = attestation["evidence"]
    promotion = attestation["promotion"]
    assert isinstance(protocol, dict)
    assert isinstance(subject, dict)
    assert isinstance(evidence, dict)
    assert isinstance(promotion, dict)
    protocol_source = {key: value for key, value in protocol.items() if key != "protocol_version"}
    protocol["protocol_version"] = canonical_digest(PROTOCOL_SCHEMA, protocol_source)
    context = {
        "subject": subject,
        "evidence": evidence,
        "protocol": protocol,
    }
    context_version = canonical_digest(CONTEXT_SCHEMA, context)
    attestation["context_version"] = context_version
    attestation["attestation_version"] = canonical_digest(
        ATTESTATION_SCHEMA,
        {
            "attestation_schema": attestation["attestation_schema"],
            **context,
            "promotion": promotion,
            "context_version": context_version,
        },
    )


def _base_results(eval_attestation: dict[str, object]) -> list[dict[str, object]]:
    context = eval_attestation["context_version"]
    return [
        {
            "case_id": "policy.basic",
            "suite": "policy",
            "passed": True,
            "question": "must never be returned",
            "answer": "must never be returned",
            "answer_model_served": "answer-served-v2",
            "answer_models_served": ["answer-served-v1", "answer-served-v2"],
            "judge_models_served": ["judge-served-v3"],
            "run_context_version": context,
            "case_semantics_version": "6" * 64,
        },
        {
            "case_id": "safety.refusal",
            "suite": "safety",
            "passed": True,
            "answer_model_served": None,
            "answer_models_served": [],
            "judge_models_served": ["judge-served-v3"],
            "run_context_version": context,
            "case_semantics_version": "7" * 64,
        },
        {
            "case_id": "safety.boundary",
            "suite": "safety",
            "passed": False,
            "answer_model_served": "answer-served-v2",
            "answer_models_served": ["answer-served-v2"],
            "judge_models_served": ["judge-served-v4"],
            "run_context_version": context,
            "case_semantics_version": "8" * 64,
        },
    ]


def _results_bytes(results: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in results
    )


def _scoreboard(
    results: list[dict[str, object]],
) -> tuple[dict[str, dict[str, int | float]], dict[str, int]]:
    suites: dict[str, dict[str, int | float]] = {}
    for record in results:
        suite = str(record["suite"])
        entry = suites.setdefault(suite, {"passed": 0, "total": 0, "pass_rate": 0.0})
        entry["total"] = int(entry["total"]) + 1
        entry["passed"] = int(entry["passed"]) + int(record["passed"] is True)
    for entry in suites.values():
        entry["pass_rate"] = round(100 * int(entry["passed"]) / int(entry["total"]), 1)
    total = {
        "passed": sum(int(entry["passed"]) for entry in suites.values()),
        "total": sum(int(entry["total"]) for entry in suites.values()),
    }
    return suites, total


def _summary(
    eval_attestation: dict[str, object],
    results: list[dict[str, object]],
    results_bytes: bytes,
) -> dict[str, object]:
    suites, total = _scoreboard(results)
    answer_values: set[str] = set()
    judge_values: set[str] = set()
    for record in results:
        raw_answer = record.get("answer_models_served", [])
        raw_judge = record.get("judge_models_served", [])
        assert isinstance(raw_answer, list)
        assert isinstance(raw_judge, list)
        answer_values.update(str(model) for model in raw_answer)
        judge_values.update(str(model) for model in raw_judge)
    answer_models = sorted(answer_values)
    judge_models = sorted(judge_values)
    return {
        "run_id": _RUN_ID,
        "run_at": "2026-07-30T20:15:01Z",
        "results_sha256": hashlib.sha256(results_bytes).hexdigest(),
        "mode": "full",
        "offline": False,
        "judges_ran": True,
        "promotion_requested": True,
        "gate_status": "passed",
        "attestation": eval_attestation,
        "corpus_version": _CORPUS_VERSION,
        "served_models": {"answer": answer_models, "judge": judge_models},
        "execution": {
            "jobs": 4,
            "cache": {"enabled": False, "answer_hits": 0, "judge_hits": 0},
            "only_failed": False,
            "since": None,
            "reused_cases": 0,
            "executed_cases": len(results),
        },
        "suites": suites,
        "total": total,
    }


def _summary_bytes(summary: dict[str, object]) -> bytes:
    return (json.dumps(summary, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _promotion_bytes(
    *,
    summary_bytes: bytes,
    results_bytes: bytes,
    eval_attestation: dict[str, object],
    logical_release: LogicalRelease | None = None,
    run_id: str = _RUN_ID,
    run_at: datetime = _RUN_AT,
    evaluation_attestation_version: str | None = None,
) -> bytes:
    logical = logical_release or _logical_release()
    evaluation = EvaluationRun(
        run_id=run_id,
        run_at=run_at,
        mode="full",
        offline=False,
        cache_enabled=False,
        judges_ran=True,
        evaluated_release=logical,
        results_sha256=hashlib.sha256(results_bytes).hexdigest(),
        summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
        evaluation_attestation_version=(
            evaluation_attestation_version or str(eval_attestation["attestation_version"])
        ),
        gate_status="passed",
    )
    promotion = build_promotion_attestation(
        _runtime_release(logical),
        evaluation,
        promoted_at=max(_PROMOTED_AT, run_at),
    )
    return attestation_bytes(promotion)


def _write_evidence(
    tmp_path: Path,
    *,
    summary_mutator: Callable[[dict[str, object]], None] | None = None,
    results_mutator: Callable[[list[dict[str, object]]], None] | None = None,
    promotion_changes: dict[str, object] | None = None,
    logical_release: LogicalRelease | None = None,
    promotion_run_id: str = _RUN_ID,
    promotion_run_at: datetime = _RUN_AT,
    promotion_eval_version: str | None = None,
    eval_mutator: Callable[[dict[str, object]], None] | None = None,
) -> _EvidenceFiles:
    eval_attestation = _eval_attestation(promotion_changes=promotion_changes)
    if eval_mutator is not None:
        eval_mutator(eval_attestation)
    results = _base_results(eval_attestation)
    if results_mutator is not None:
        results_mutator(results)
    exact_results = _results_bytes(results)
    summary = _summary(eval_attestation, results, exact_results)
    if summary_mutator is not None:
        summary_mutator(summary)
    exact_summary = _summary_bytes(summary)
    exact_promotion = _promotion_bytes(
        summary_bytes=exact_summary,
        results_bytes=exact_results,
        eval_attestation=eval_attestation,
        logical_release=logical_release,
        run_id=promotion_run_id,
        run_at=promotion_run_at,
        evaluation_attestation_version=promotion_eval_version,
    )
    summary_path = tmp_path / "summary.json"
    results_path = tmp_path / "results.jsonl"
    promotion_path = tmp_path / "promotion.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    summary_path.write_bytes(exact_summary)
    results_path.write_bytes(exact_results)
    promotion_path.write_bytes(exact_promotion)
    return _EvidenceFiles(
        summary_path=summary_path,
        results_path=results_path,
        promotion_path=promotion_path,
        summary=summary,
        results=tuple(results),
        eval_attestation=eval_attestation,
    )


def _resign(
    files: _EvidenceFiles,
    *,
    summary: dict[str, object] | None = None,
    results_bytes: bytes | None = None,
) -> _EvidenceFiles:
    selected_results = (
        results_bytes if results_bytes is not None else files.results_path.read_bytes()
    )
    selected_summary = copy.deepcopy(summary if summary is not None else files.summary)
    selected_summary["results_sha256"] = hashlib.sha256(selected_results).hexdigest()
    exact_summary = _summary_bytes(selected_summary)
    files.results_path.write_bytes(selected_results)
    files.summary_path.write_bytes(exact_summary)
    files.promotion_path.write_bytes(
        _promotion_bytes(
            summary_bytes=exact_summary,
            results_bytes=selected_results,
            eval_attestation=files.eval_attestation,
        )
    )
    return dataclasses.replace(files, summary=selected_summary)


def _verify(
    files: _EvidenceFiles,
    *,
    now: datetime = _RUN_AT + timedelta(days=1),
    budget: timedelta = timedelta(days=7),
):
    return verify_promotion_evidence(
        summary_path=files.summary_path,
        results_path=files.results_path,
        promotion_path=files.promotion_path,
        freshness_budget=budget,
        clock=lambda: now,
    )


def _assert_mismatch(files: _EvidenceFiles, expected: str) -> None:
    with pytest.raises(PromotionEvidenceError) as caught:
        _verify(files)
    assert expected in caught.value.mismatches


def test_verifies_exact_evidence_and_returns_only_sanitized_frozen_data(
    tmp_path: Path,
) -> None:
    files = _write_evidence(tmp_path)

    evidence = _verify(files)

    assert evidence.status == "verified"
    assert evidence.warnings == ()
    assert evidence.fresh is True
    assert evidence.total.as_dict() == {"passed": 2, "total": 3, "pass_rate": 66.7}
    assert [suite.as_dict() for suite in evidence.suites] == [
        {"name": "policy", "passed": 1, "total": 1, "pass_rate": 100.0},
        {"name": "safety", "passed": 1, "total": 2, "pass_rate": 50.0},
    ]
    assert evidence.served_models is not None
    assert evidence.served_models.answer == ("answer-served-v1", "answer-served-v2")
    assert evidence.served_models.judge == ("judge-served-v3", "judge-served-v4")
    assert evidence.cases[0].case_id == "policy.basic"
    assert evidence.run_context_version == files.eval_attestation["context_version"]
    assert evidence.summary_sha256 == hashlib.sha256(files.summary_path.read_bytes()).hexdigest()
    assert evidence.results_sha256 == hashlib.sha256(files.results_path.read_bytes()).hexdigest()
    serialized = json.dumps(evidence.as_dict())
    assert "must never be returned" not in serialized
    assert '"question"' not in serialized
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.status = "warning"  # type: ignore[misc]


def test_verifier_preserves_unicode_line_separators_inside_json_strings(
    tmp_path: Path,
) -> None:
    files = _write_evidence(
        tmp_path,
        results_mutator=lambda results: results[0].update(answer="first\u2028second\u2029third"),
    )

    evidence = _verify(files)

    assert evidence.total.total == 3
    assert evidence.cases[0].case_id == "policy.basic"


def test_exact_freshness_boundary_is_verified_and_stale_is_warning(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)

    boundary = _verify(files, now=_RUN_AT + timedelta(days=7))
    stale = _verify(files, now=_RUN_AT + timedelta(days=7, seconds=1))

    assert boundary.status == "verified"
    assert boundary.age_seconds == 7 * 24 * 60 * 60
    assert stale.status == "warning"
    assert stale.warnings == ("evaluation.stale",)
    assert stale.fresh is False


def test_future_evaluation_is_invalid_even_when_promotion_time_is_not_future(
    tmp_path: Path,
) -> None:
    files = _write_evidence(tmp_path)

    with pytest.raises(PromotionEvidenceError) as caught:
        _verify(files, now=_RUN_AT - timedelta(microseconds=1))

    assert caught.value.mismatches == ("evaluation.run_at.future",)


def test_future_promotion_time_is_invalid(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)

    with pytest.raises(PromotionEvidenceError) as caught:
        _verify(files, now=_RUN_AT + timedelta(minutes=1))

    assert caught.value.mismatches == ("promotion.promoted_at.future",)


@pytest.mark.parametrize("budget", [timedelta(0), timedelta(seconds=-1), "7 days"])
def test_requires_positive_timedelta_freshness_budget(
    tmp_path: Path,
    budget: object,
) -> None:
    files = _write_evidence(tmp_path)

    with pytest.raises(PromotionEvidenceError, match="positive timedelta"):
        _verify(files, budget=budget)  # type: ignore[arg-type]


def test_rejects_noncanonical_promotion_bytes(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)
    parsed = json.loads(files.promotion_path.read_bytes())
    files.promotion_path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")

    _assert_mismatch(files, "promotion.canonical")


def test_rejects_any_change_to_exact_summary_bytes(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)
    files.summary_path.write_bytes(files.summary_path.read_bytes() + b" ")

    _assert_mismatch(files, "summary.sha256")


def test_rejects_any_change_to_exact_result_bytes(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)
    files.results_path.write_bytes(files.results_path.read_bytes() + b"\n")

    _assert_mismatch(files, "results.sha256")


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("mode", "summary.mode"),
        ("offline", "summary.offline"),
        ("judges", "summary.judges_ran"),
        ("promotion", "summary.promotion_requested"),
        ("gate", "summary.gate_status"),
        ("cache", "summary.execution.cache.enabled"),
        ("reused", "summary.execution.reused_cases"),
        ("since", "summary.execution.since"),
        ("executed", "summary.execution.executed_cases"),
        ("replicates", "summary.replicates"),
    ],
)
def test_rejects_every_summary_promotion_contract_violation(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate(summary: dict[str, object]) -> None:
        execution = summary["execution"]
        assert isinstance(execution, dict)
        cache = execution["cache"]
        assert isinstance(cache, dict)
        if mutation == "mode":
            summary["mode"] = "smoke"
        elif mutation == "offline":
            summary["offline"] = True
        elif mutation == "judges":
            summary["judges_ran"] = False
        elif mutation == "promotion":
            summary["promotion_requested"] = False
        elif mutation == "gate":
            summary["gate_status"] = "pending"
        elif mutation == "cache":
            cache["enabled"] = True
        elif mutation == "reused":
            execution["reused_cases"] = 1
        elif mutation == "since":
            execution["since"] = "prior-run"
        elif mutation == "executed":
            execution["executed_cases"] = 2
        elif mutation == "replicates":
            summary["replicates"] = 2

    files = _write_evidence(tmp_path, summary_mutator=mutate)

    _assert_mismatch(files, mismatch)


def test_rejects_ineligible_nested_evaluation_attestation(tmp_path: Path) -> None:
    files = _write_evidence(
        tmp_path,
        promotion_changes={
            "eligible": False,
            "live": False,
            "reasons": ["not_live"],
        },
    )

    _assert_mismatch(files, "evaluation.promotion.eligible")


def test_rejects_runtime_release_disagreement(tmp_path: Path) -> None:
    alternate_config = "f" * 64
    alternate = _logical_release(
        config_version=alternate_config,
        release_version=build_release_identity(
            _SOURCE_REVISION,
            alternate_config,
            content_version=_CONTENT_VERSION,
            snapshot_version=_SNAPSHOT_VERSION,
        ).release_version,
    )
    files = _write_evidence(tmp_path, logical_release=alternate)

    _assert_mismatch(files, "evaluation.release")


@pytest.mark.parametrize(
    ("kwargs", "mismatch"),
    [
        ({"promotion_run_id": "different-run"}, "evaluation.run_id"),
        (
            {"promotion_run_at": _RUN_AT + timedelta(seconds=1)},
            "evaluation.run_at",
        ),
        (
            {"promotion_eval_version": "9" * 64},
            "evaluation.attestation_version",
        ),
    ],
)
def test_rejects_run_time_and_eval_attestation_disagreement(
    tmp_path: Path,
    kwargs: dict[str, object],
    mismatch: str,
) -> None:
    files = _write_evidence(tmp_path, **kwargs)  # type: ignore[arg-type]

    _assert_mismatch(files, mismatch)


def test_rejects_duplicate_and_unsafe_case_ids(tmp_path: Path) -> None:
    duplicate = _write_evidence(
        tmp_path / "duplicate",
        results_mutator=lambda results: results[1].update(case_id=results[0]["case_id"]),
    )
    _assert_mismatch(duplicate, "results.case_id.duplicate")

    unsafe = _write_evidence(
        tmp_path / "unsafe",
        results_mutator=lambda results: results[0].update(case_id="../escape"),
    )
    assert "case_id" in _expected_error(unsafe)


def _expected_error(files: _EvidenceFiles) -> str:
    with pytest.raises(PromotionEvidenceError) as caught:
        _verify(files)
    return " ".join(caught.value.mismatches)


@pytest.mark.parametrize("target", ["suite", "pass_rate", "total"])
def test_rejects_score_aggregation_disagreement(tmp_path: Path, target: str) -> None:
    def mutate(summary: dict[str, object]) -> None:
        if target in {"suite", "pass_rate"}:
            suites = summary["suites"]
            assert isinstance(suites, dict)
            safety = suites["safety"]
            assert isinstance(safety, dict)
            if target == "suite":
                safety["passed"] = 2
            else:
                safety["pass_rate"] = 100.0
        else:
            total = summary["total"]
            assert isinstance(total, dict)
            total["passed"] = 3

    files = _write_evidence(tmp_path, summary_mutator=mutate)

    expected = {
        "total": "summary.total",
        "suite": "summary.suites.safety",
        "pass_rate": "summary.suites.safety.pass_rate",
    }
    _assert_mismatch(files, expected[target])


def test_rejects_result_run_context_disagreement(tmp_path: Path) -> None:
    files = _write_evidence(
        tmp_path,
        results_mutator=lambda results: results[0].update(run_context_version="9" * 64),
    )

    _assert_mismatch(files, "results.line_1.run_context_version")


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("result_order", "results.line_1.case_id"),
        ("result_semantics", "results.line_1.case_semantics_version"),
        ("manifest_count", "evaluation.evidence.case_count"),
        ("suite_digest", "evaluation.evidence.suite_version"),
    ],
)
def test_reconciles_exact_results_with_attested_case_manifest(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate_results(results: list[dict[str, object]]) -> None:
        if mutation == "result_order":
            results[0], results[1] = results[1], results[0]
        elif mutation == "result_semantics":
            results[0]["case_semantics_version"] = "9" * 64

    def mutate_eval(attestation: dict[str, object]) -> None:
        evidence = attestation["evidence"]
        assert isinstance(evidence, dict)
        if mutation == "manifest_count":
            evidence["case_count"] = 2
        elif mutation == "suite_digest":
            evidence["suite_version"] = "9" * 64
        _restamp_eval_attestation(attestation)

    files = _write_evidence(
        tmp_path,
        results_mutator=mutate_results if mutation.startswith("result_") else None,
        eval_mutator=mutate_eval if mutation.startswith(("manifest_", "suite_")) else None,
    )

    _assert_mismatch(files, mismatch)


def test_rejects_summary_served_model_disagreement(tmp_path: Path) -> None:
    def mutate(summary: dict[str, object]) -> None:
        served = summary["served_models"]
        assert isinstance(served, dict)
        served["answer"] = ["claimed-but-never-served"]

    files = _write_evidence(tmp_path, summary_mutator=mutate)

    _assert_mismatch(files, "summary.served_models")


@pytest.mark.parametrize(
    ("result_field", "summary_field", "mismatch"),
    [
        ("run_context_version", None, "results.line_1.run_context_version"),
        ("case_semantics_version", None, "results.line_1.case_semantics_version"),
        ("answer_models_served", None, "results.line_1.answer_models_served"),
        ("judge_models_served", None, "results.line_1.judge_models_served"),
        (None, "served_models", "summary.served_models"),
    ],
)
def test_requires_complete_result_and_summary_provenance(
    tmp_path: Path,
    result_field: str | None,
    summary_field: str | None,
    mismatch: str,
) -> None:
    def mutate_results(results: list[dict[str, object]]) -> None:
        if result_field is not None:
            results[0].pop(result_field)

    def mutate_summary(summary: dict[str, object]) -> None:
        if summary_field is not None:
            summary.pop(summary_field)

    files = _write_evidence(
        tmp_path,
        results_mutator=mutate_results,
        summary_mutator=mutate_summary,
    )

    _assert_mismatch(files, mismatch)


def test_rejects_symlinked_input_and_oversized_file(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path / "base")
    symlink = tmp_path / "summary-link.json"
    symlink.symlink_to(files.summary_path)
    linked = dataclasses.replace(files, summary_path=symlink)
    _assert_mismatch(linked, "summary.file")

    oversized_path = tmp_path / "oversized-summary.json"
    oversized_path.write_bytes(b"x" * (MAX_SUMMARY_BYTES + 1))
    oversized = dataclasses.replace(files, summary_path=oversized_path)
    _assert_mismatch(oversized, "summary.size")


def test_detects_file_change_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _write_evidence(tmp_path)
    original_read = __import__("assistant.promotion_evidence", fromlist=["os"]).os.read
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, count)
        if not changed:
            changed = True
            files.summary_path.write_bytes(files.summary_path.read_bytes() + b" ")
        return chunk

    monkeypatch.setattr("assistant.promotion_evidence.os.read", racing_read)

    _assert_mismatch(files, "summary.changed")


def test_rejects_duplicate_summary_json_key_after_exact_hash_matches(
    tmp_path: Path,
) -> None:
    files = _write_evidence(tmp_path)
    summary_bytes = files.summary_path.read_bytes().replace(
        b'{\n  "run_id"',
        b'{\n  "mode": "full",\n  "run_id"',
        1,
    )
    files.summary_path.write_bytes(summary_bytes)
    files.promotion_path.write_bytes(
        _promotion_bytes(
            summary_bytes=summary_bytes,
            results_bytes=files.results_path.read_bytes(),
            eval_attestation=files.eval_attestation,
        )
    )

    _assert_mismatch(files, "summary.json")


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("schema", "evaluation.attestation.schema"),
        ("extra", "evaluation.attestation"),
        ("source_state", "evaluation.subject.source_state"),
        ("source_revision", "evaluation.subject.source_revision"),
        ("descriptor", "evaluation.subject.descriptor_verified"),
        ("config", "evaluation.subject.config_version"),
        ("corpus", "evaluation.subject.corpus_version"),
        ("release", "evaluation.subject.release_version"),
        ("evidence", "evaluation.evidence.facts_version"),
        ("protocol_digest", "evaluation.protocol.protocol_version"),
        ("protocol_mode", "evaluation.protocol.mode"),
        ("protocol_offline", "evaluation.protocol.offline"),
        ("protocol_judges", "evaluation.protocol.run_judges"),
        ("protocol_cache", "evaluation.protocol.cache_enabled"),
        ("protocol_replicates", "evaluation.protocol.replicates"),
        ("reasons", "evaluation.promotion.reasons"),
        ("context_digest", "evaluation.context_version"),
        ("attestation_digest", "evaluation.attestation_version"),
    ],
)
def test_rejects_malformed_or_self_inconsistent_eval_attestation(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate(attestation: dict[str, object]) -> None:
        subject = attestation["subject"]
        evidence = attestation["evidence"]
        protocol = attestation["protocol"]
        promotion = attestation["promotion"]
        assert isinstance(subject, dict)
        assert isinstance(evidence, dict)
        assert isinstance(protocol, dict)
        assert isinstance(promotion, dict)
        restamp = True
        if mutation == "schema":
            attestation["attestation_schema"] = "fare-assistant.eval-attestation.v2"
            restamp = False
        elif mutation == "extra":
            attestation["unexpected"] = True
            restamp = False
        elif mutation == "source_state":
            subject["source_state"] = "dirty"
        elif mutation == "source_revision":
            subject["head_revision"] = "f" * 40
        elif mutation == "descriptor":
            subject["descriptor_verified"] = False
        elif mutation == "config":
            subject["config_version"] = "invalid"
        elif mutation == "corpus":
            subject["corpus_version"] = "invalid"
        elif mutation == "release":
            subject["release_version"] = "9" * 64
        elif mutation == "evidence":
            evidence["facts_version"] = "invalid"
        elif mutation == "protocol_digest":
            protocol["protocol_version"] = "9" * 64
            restamp = False
        elif mutation == "protocol_mode":
            protocol["mode"] = "smoke"
        elif mutation == "protocol_offline":
            protocol["offline"] = True
        elif mutation == "protocol_judges":
            protocol["run_judges"] = False
        elif mutation == "protocol_cache":
            protocol["cache_enabled"] = True
        elif mutation == "protocol_replicates":
            protocol["replicates"] = 2
        elif mutation == "reasons":
            promotion["reasons"] = ["should-not-exist"]
        elif mutation == "context_digest":
            attestation["context_version"] = "9" * 64
            restamp = False
        elif mutation == "attestation_digest":
            attestation["attestation_version"] = "9" * 64
            restamp = False
        if restamp:
            _restamp_eval_attestation(attestation)

    files = _write_evidence(tmp_path, eval_mutator=mutate)

    _assert_mismatch(files, mismatch)


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("manifest_shape", "evaluation.evidence.case_manifest"),
        ("manifest_case_id", "evaluation.evidence.case_manifest.0.case_id"),
        ("manifest_duplicate", "evaluation.evidence.case_manifest.duplicate"),
        ("case_count_type", "evaluation.evidence.case_count"),
        ("case_count_limit", "evaluation.evidence.case_count"),
        ("empty_protocol", "evaluation.protocol"),
    ],
)
def test_rejects_additional_eval_attestation_boundaries(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate(attestation: dict[str, object]) -> None:
        evidence = attestation["evidence"]
        assert isinstance(evidence, dict)
        manifest = evidence["case_manifest"]
        assert isinstance(manifest, list)
        if mutation == "manifest_shape":
            evidence["case_manifest"] = "not-an-array"
        elif mutation == "manifest_case_id":
            first = manifest[0]
            assert isinstance(first, dict)
            first["case_id"] = "../unsafe"
        elif mutation == "manifest_duplicate":
            first = manifest[0]
            second = manifest[1]
            assert isinstance(first, dict)
            assert isinstance(second, dict)
            second["case_id"] = first["case_id"]
        elif mutation == "case_count_type":
            evidence["case_count"] = True
        elif mutation == "case_count_limit":
            evidence["case_count"] = 50_001
        elif mutation == "empty_protocol":
            attestation["protocol"] = {
                "protocol_version": canonical_digest(PROTOCOL_SCHEMA, {}),
            }
        _restamp_eval_attestation(attestation)

    files = _write_evidence(tmp_path, eval_mutator=mutate)

    _assert_mismatch(files, mismatch)


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("results_digest", "summary.results_sha256"),
        ("corpus_version", "summary.corpus_version"),
        ("run_at", "evaluation.run_at"),
        ("nonfinite", "summary.json"),
    ],
)
def test_rejects_additional_summary_boundaries(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate(summary: dict[str, object]) -> None:
        if mutation == "results_digest":
            summary["results_sha256"] = "9" * 64
        elif mutation == "corpus_version":
            summary["corpus_version"] = "f" * 12
        elif mutation == "run_at":
            summary["run_at"] = "2026-07-30T20:15:01+00:00"
        elif mutation == "nonfinite":
            summary["run_id"] = float("nan")

    files = _write_evidence(tmp_path, summary_mutator=mutate)

    _assert_mismatch(files, mismatch)


def test_rejects_invalid_utf8_summary_after_exact_bytes_are_attested(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)
    invalid_summary = b"\xff"
    files.summary_path.write_bytes(invalid_summary)
    files.promotion_path.write_bytes(
        _promotion_bytes(
            summary_bytes=invalid_summary,
            results_bytes=files.results_path.read_bytes(),
            eval_attestation=files.eval_attestation,
        )
    )

    _assert_mismatch(files, "summary.json")


@pytest.mark.parametrize(
    ("payload", "mismatch"),
    [
        (b"", "results.empty"),
        (b"\n", "results.empty_line"),
        (b'{"case_id":"case"}', "results.terminal_lf"),
        (b'{"case_id":"case"}\r\n', "results.line_endings"),
        (b'{"case_id":"case"}\n\n', "results.empty_line"),
        (b"\xff", "results.json"),
        (b"{not-json}\n", "results.line_1.json"),
        (b"[]\n", "results.line_1"),
        (b'{"suite":"policy","passed":true}\n', "results.line_1.case_id"),
        (
            b'{"case_id":"case","suite":"policy","passed":1}\n',
            "results.line_1.passed",
        ),
        (
            b'{"case_id":"case","suite":"bad\\nname","passed":true}\n',
            "results.line_1.suite",
        ),
    ],
)
def test_rejects_malformed_result_streams(
    tmp_path: Path,
    payload: bytes,
    mismatch: str,
) -> None:
    files = _resign(_write_evidence(tmp_path), results_bytes=payload)

    _assert_mismatch(files, mismatch)


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("too_few", "results.case_manifest"),
        ("too_many", "results.case_manifest"),
        ("model_shape", "results.line_1.answer_models_served"),
    ],
)
def test_rejects_additional_result_stream_boundaries(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    files = _write_evidence(tmp_path)
    records = [copy.deepcopy(record) for record in files.results]
    if mutation == "too_few":
        payload = _results_bytes(records[:-1])
    elif mutation == "too_many":
        payload = _results_bytes([*records, records[0]])
    else:
        records[0]["answer_models_served"] = "not-an-array"
        payload = _results_bytes(records)
    _resign(files, results_bytes=payload)

    _assert_mismatch(files, mismatch)


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    [
        ("partial_models", "results.line_1.judge_models_served"),
        ("unsorted_models", "results.line_1.answer_models_served"),
        ("final_model", "results.line_1.answer_model_served"),
        ("partial_case_provenance", "results.line_1.answer_models_served"),
        ("bad_semantics", "results.line_1.case_semantics_version"),
    ],
)
def test_rejects_incoherent_result_provenance(
    tmp_path: Path,
    mutation: str,
    mismatch: str,
) -> None:
    def mutate(results: list[dict[str, object]]) -> None:
        if mutation == "partial_models":
            results[0].pop("judge_models_served")
        elif mutation == "unsorted_models":
            results[0]["answer_models_served"] = ["z-model", "a-model"]
        elif mutation == "final_model":
            results[0]["answer_model_served"] = "not-in-the-set"
        elif mutation == "partial_case_provenance":
            results[0].pop("answer_model_served")
            results[0].pop("answer_models_served")
            results[0].pop("judge_models_served")
        elif mutation == "bad_semantics":
            results[0]["case_semantics_version"] = "invalid"

    files = _write_evidence(tmp_path, results_mutator=mutate)

    _assert_mismatch(files, mismatch)


def test_rejects_summary_suite_names_and_served_model_shape_mismatch(tmp_path: Path) -> None:
    def mutate_suite(summary: dict[str, object]) -> None:
        suites = summary["suites"]
        assert isinstance(suites, dict)
        suites["unexpected"] = suites.pop("policy")

    suite_files = _write_evidence(tmp_path / "suite", summary_mutator=mutate_suite)
    _assert_mismatch(suite_files, "summary.suites")

    def mutate_models(summary: dict[str, object]) -> None:
        served = summary["served_models"]
        assert isinstance(served, dict)
        served["extra"] = []

    model_files = _write_evidence(tmp_path / "models", summary_mutator=mutate_models)
    _assert_mismatch(model_files, "summary.served_models")


def test_rejects_invalid_promotion_and_summary_shapes(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path / "promotion")
    files.promotion_path.write_bytes(b"{}")
    _assert_mismatch(files, "promotion.attestation")

    non_object = _write_evidence(tmp_path / "summary")
    summary_bytes = b"[]\n"
    non_object.summary_path.write_bytes(summary_bytes)
    non_object.promotion_path.write_bytes(
        _promotion_bytes(
            summary_bytes=summary_bytes,
            results_bytes=non_object.results_path.read_bytes(),
            eval_attestation=non_object.eval_attestation,
        )
    )
    _assert_mismatch(non_object, "summary.json")


def test_rejects_missing_directory_and_non_path_inputs(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)
    missing = dataclasses.replace(files, summary_path=tmp_path / "missing.json")
    _assert_mismatch(missing, "summary.file")

    directory = tmp_path / "directory"
    directory.mkdir()
    directory_input = dataclasses.replace(files, summary_path=directory)
    _assert_mismatch(directory_input, "summary.file")

    with pytest.raises(PromotionEvidenceError) as caught:
        verify_promotion_evidence(
            summary_path="summary.json",  # type: ignore[arg-type]
            results_path=files.results_path,
            promotion_path=files.promotion_path,
            freshness_budget=timedelta(days=7),
            clock=lambda: _RUN_AT,
        )
    assert caught.value.mismatches == ("summary.file",)


@pytest.mark.parametrize(
    "clock",
    [
        None,
        lambda: None,
        lambda: datetime(2026, 7, 30),
    ],
)
def test_rejects_invalid_clocks(tmp_path: Path, clock: object) -> None:
    files = _write_evidence(tmp_path)

    with pytest.raises(PromotionEvidenceError) as caught:
        verify_promotion_evidence(
            summary_path=files.summary_path,
            results_path=files.results_path,
            promotion_path=files.promotion_path,
            freshness_budget=timedelta(days=7),
            clock=clock,  # type: ignore[arg-type]
        )

    assert caught.value.mismatches == ("clock",)


def test_wraps_clock_failure_as_invalid_evidence(tmp_path: Path) -> None:
    files = _write_evidence(tmp_path)

    def broken_clock() -> datetime:
        raise RuntimeError("clock backend failed")

    with pytest.raises(PromotionEvidenceError) as caught:
        verify_promotion_evidence(
            summary_path=files.summary_path,
            results_path=files.results_path,
            promotion_path=files.promotion_path,
            freshness_budget=timedelta(days=7),
            clock=broken_clock,
        )

    assert caught.value.mismatches == ("clock",)
