"""Promotion-attestation builder boundary and failure-mode tests."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from assistant.release_attestation import (
    PROMOTION_ATTESTATION_SCHEMA,
    attestation_bytes,
    attestation_digest,
    parse_promotion_attestation,
)
from assistant.release_identity import build_release_identity
from evals.attestation import SUITE_SCHEMA, canonical_digest
from evals.attestation import build_attestation as build_eval_attestation
from scripts import build_promotion_attestation as builder

SOURCE = "a" * 40
CONFIG = "b" * 64
CONTENT = "c" * 64
SNAPSHOT = "d" * 64
RELEASE = build_release_identity(
    SOURCE,
    CONFIG,
    content_version=CONTENT,
    snapshot_version=SNAPSHOT,
).release_version
CORPUS = "e" * 12
ARTIFACT = base64.b64encode(bytes(range(32))).decode("ascii")
RUN_ID = "20260730T223000Z"
RUN_AT = "2026-07-30T22:30:00Z"
PROMOTED_AT = datetime(2026, 7, 30, 22, 35, tzinfo=UTC)
CASE_SEMANTICS = "6" * 64
CASE_MANIFEST = [
    {
        "case_id": "case-1",
        "case_semantics_version": CASE_SEMANTICS,
    }
]


def _runtime(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source_revision": SOURCE,
        "config_version": CONFIG,
        "content_version": CONTENT,
        "snapshot_version": SNAPSHOT,
        "release_version": RELEASE,
        "corpus_version": CORPUS,
        "artifact_code_sha256": ARTIFACT,
        "function_version": "11",
    }
    value.update(changes)
    return value


def _eval_attestation(
    case_manifest_value: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    manifest = case_manifest_value or CASE_MANIFEST
    return build_eval_attestation(
        subject={
            "source_state": "clean",
            "head_revision": SOURCE,
            "source_revision": SOURCE,
            "config_version": CONFIG,
            "content_version": CONTENT,
            "snapshot_version": SNAPSHOT,
            "release_version": RELEASE,
            "corpus_version": CORPUS,
            "descriptor_verified": True,
        },
        suite_version=canonical_digest(
            SUITE_SCHEMA,
            {"case_manifest": manifest},
        ),
        case_manifest=manifest,
        facts_version="2" * 64,
        gtfs_input_version="3" * 64,
        protocol={
            "mode": "full",
            "offline": False,
            "replicates": 1,
            "run_judges": True,
            "cache_enabled": False,
            "evaluator_version": "4" * 64,
        },
        promotion={
            "eligible": True,
            "live": True,
            "uncached": True,
            "judges_ran": True,
            "gates_passed": True,
            "reasons": [],
            "evaluated_at": RUN_AT,
        },
    )


def _result_record(
    *,
    case_id: str = "case-1",
    suite: str = "core",
    passed: bool = True,
    semantics: str = CASE_SEMANTICS,
    context_version: str | None = None,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "passed": passed,
        "suite": suite,
        "run_context_version": context_version or str(_eval_attestation()["context_version"]),
        "case_semantics_version": semantics,
        "answer_model_served": "answer-served",
        "answer_models_served": ["answer-served"],
        "judge_models_served": ["judge-served"],
    }


def _jsonl(records: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
        for record in records
    )


RESULTS = _jsonl([_result_record()])


def _summary(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": RUN_ID,
        "run_at": RUN_AT,
        "results_sha256": hashlib.sha256(RESULTS).hexdigest(),
        "mode": "full",
        "offline": False,
        "judges_ran": True,
        "promotion_requested": True,
        "gate_status": "passed",
        "execution": {
            "cache": {"enabled": False, "answer_hits": 0, "judge_hits": 0},
            "only_failed": False,
            "since": None,
            "reused_cases": 0,
            "executed_cases": 1,
        },
        "replicates": 1,
        "suites": {"core": {"passed": 1, "total": 1, "pass_rate": 100.0}},
        "total": {"passed": 1, "total": 1},
        "served_models": {
            "answer": ["answer-served"],
            "judge": ["judge-served"],
        },
        "attestation": _eval_attestation(),
    }
    value.update(changes)
    return value


def _summary_bytes(summary: dict[str, object] | None = None) -> bytes:
    return (
        json.dumps(
            summary or _summary(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _evidence_for_rows(
    rows: list[tuple[str, str, bool]],
    *,
    actual_case_ids: list[str] | None = None,
) -> tuple[dict[str, object], bytes]:
    manifest = [
        {
            "case_id": case_id,
            "case_semantics_version": str(index + 6) * 64,
        }
        for index, (case_id, _suite, _passed) in enumerate(rows)
    ]
    attestation = _eval_attestation(manifest)
    context_version = str(attestation["context_version"])
    records = [
        _result_record(
            case_id=(actual_case_ids[index] if actual_case_ids is not None else expected_case_id),
            suite=suite,
            passed=passed,
            semantics=str(index + 6) * 64,
            context_version=context_version,
        )
        for index, (expected_case_id, suite, passed) in enumerate(rows)
    ]
    results = _jsonl(records)
    suites: dict[str, dict[str, int | float]] = {}
    for _case_id, suite, passed in rows:
        score = suites.setdefault(
            suite,
            {"passed": 0, "total": 0, "pass_rate": 0.0},
        )
        score["total"] = int(score["total"]) + 1
        score["passed"] = int(score["passed"]) + int(passed)
    for score in suites.values():
        score["pass_rate"] = round(
            100 * int(score["passed"]) / int(score["total"]),
            1,
        )
    summary = _summary(
        results_sha256=hashlib.sha256(results).hexdigest(),
        attestation=attestation,
        suites=suites,
        total={
            "passed": sum(int(score["passed"]) for score in suites.values()),
            "total": len(rows),
        },
    )
    execution = summary["execution"]
    assert isinstance(execution, dict)
    execution["executed_cases"] = len(rows)
    return summary, results


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime.json"
    summary = tmp_path / "summary.json"
    results = tmp_path / "results.jsonl"
    runtime.write_text(json.dumps(_runtime()), encoding="utf-8")
    summary.write_bytes(_summary_bytes())
    results.write_bytes(RESULTS)
    return runtime, summary, results


def _rehash_eval_attestation(summary: dict[str, object]) -> None:
    attestation = summary["attestation"]
    assert isinstance(attestation, dict)
    protocol = attestation["protocol"]
    assert isinstance(protocol, dict)
    raw_protocol = {key: value for key, value in protocol.items() if key != "protocol_version"}
    evidence = attestation["evidence"]
    subject = attestation["subject"]
    promotion = attestation["promotion"]
    assert isinstance(evidence, dict)
    assert isinstance(subject, dict)
    assert isinstance(promotion, dict)
    summary["attestation"] = build_eval_attestation(
        subject=subject,
        suite_version=evidence["suite_version"],
        case_manifest=evidence["case_manifest"],
        facts_version=evidence["facts_version"],
        gtfs_input_version=evidence["gtfs_input_version"],
        protocol=raw_protocol,
        promotion=promotion,
    )


def test_pure_builder_binds_exact_runtime_eval_and_results() -> None:
    attestation = builder.build_attestation(
        _runtime(),
        _summary_bytes(),
        RESULTS,
        promoted_at=PROMOTED_AT,
    )
    payload = json.loads(attestation_bytes(attestation))

    assert payload["schema"] == PROMOTION_ATTESTATION_SCHEMA
    assert payload["runtime_release"] == _runtime()
    assert payload["evaluation"] == {
        "run_id": RUN_ID,
        "run_at": "2026-07-30T22:30:00Z",
        "mode": "full",
        "offline": False,
        "cache_enabled": False,
        "judges_ran": True,
        "evaluated_release": {
            field: _runtime()[field]
            for field in (
                "source_revision",
                "config_version",
                "content_version",
                "snapshot_version",
                "release_version",
                "corpus_version",
            )
        },
        "results_sha256": hashlib.sha256(RESULTS).hexdigest(),
        "summary_sha256": hashlib.sha256(_summary_bytes()).hexdigest(),
        "evaluation_attestation_version": _eval_attestation()["attestation_version"],
        "gate_status": "passed",
    }
    assert payload["promoted_at"] == "2026-07-30T22:35:00Z"


def test_builder_preserves_unicode_line_separators_inside_json_strings() -> None:
    record = _result_record()
    record["answer"] = "first\u2028second\u2029third"
    results = _jsonl([record])
    summary = _summary(results_sha256=hashlib.sha256(results).hexdigest())

    attestation = builder.build_attestation(
        _runtime(),
        _summary_bytes(summary),
        results,
        promoted_at=PROMOTED_AT,
    )

    assert attestation.evaluation.results_sha256 == hashlib.sha256(results).hexdigest()


def test_summary_receipt_binds_exact_bytes_not_only_parsed_content() -> None:
    compact = _summary_bytes()
    indented = (
        json.dumps(_summary(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()

    compact_attestation = builder.build_attestation(
        _runtime(),
        compact,
        RESULTS,
        promoted_at=PROMOTED_AT,
    )
    indented_attestation = builder.build_attestation(
        _runtime(),
        indented,
        RESULTS,
        promoted_at=PROMOTED_AT,
    )

    assert compact_attestation.evaluation.summary_sha256 == hashlib.sha256(compact).hexdigest()
    assert indented_attestation.evaluation.summary_sha256 == hashlib.sha256(indented).hexdigest()
    assert (
        compact_attestation.evaluation.evaluation_attestation_version
        == indented_attestation.evaluation.evaluation_attestation_version
    )
    assert attestation_digest(compact_attestation) != attestation_digest(indented_attestation)


def test_cli_writes_canonical_0644_output_and_only_safe_receipt_to_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    output = tmp_path / "published" / "promotion.json"
    secret = "sk-ant-never-print-this"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    code = builder.main(
        [
            "--runtime",
            str(runtime),
            "--summary",
            str(summary),
            "--results",
            str(results),
            "--output",
            str(output),
            "--promoted-at",
            "2026-07-30T22:35:00Z",
        ]
    )

    captured = capsys.readouterr()
    written = parse_promotion_attestation(output.read_bytes())
    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "attestation_sha256": attestation_digest(written),
        "output_path": str(output),
    }
    assert secret not in captured.out
    assert output.stat().st_mode & 0o777 == 0o644
    assert output.read_bytes() == attestation_bytes(written)


def test_script_entrypoint_resolves_repo_local_packages_from_any_cwd(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(builder.__file__).resolve()), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--runtime" in result.stdout
    assert "--summary" in result.stdout


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("mode",), "smoke", "summary.mode"),
        (("offline",), True, "summary.offline"),
        (("judges_ran",), False, "summary.judges_ran"),
        (("promotion_requested",), False, "promotion_requested"),
        (("gate_status",), "failed", "summary.gate_status"),
        (("execution", "cache", "enabled"), True, "cache.enabled"),
        (("execution", "reused_cases"), 1, "reused_cases"),
        (("execution", "only_failed"), True, "only_failed"),
        (("execution", "since"), "prior-run", "since"),
        (("execution", "executed_cases"), 0, "executed_cases"),
        (("replicates",), 2, "replicates"),
    ],
)
def test_rejects_every_nonfresh_or_partial_summary_flag(
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    summary = _summary()
    current: dict[str, object] = summary
    for part in path[:-1]:
        child = current[part]
        assert isinstance(child, dict)
        current = child
    current[path[-1]] = value

    with pytest.raises(builder.PromotionAttestationBuildError, match=message):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )


@pytest.mark.parametrize(
    "field",
    [
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
    ],
)
def test_rejects_every_runtime_to_evaluated_release_mismatch(field: str) -> None:
    width = 12 if field == "corpus_version" else (40 if field == "source_revision" else 64)
    runtime = _runtime(**{field: "f" * width})

    with pytest.raises(builder.PromotionAttestationBuildError, match=field):
        builder.build_attestation(runtime, _summary_bytes(), RESULTS, promoted_at=PROMOTED_AT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_state", "dirty"),
        ("descriptor_verified", False),
    ],
)
def test_rejects_unverified_or_dirty_subject(field: str, value: object) -> None:
    summary = _summary()
    attestation = summary["attestation"]
    assert isinstance(attestation, dict)
    subject = attestation["subject"]
    assert isinstance(subject, dict)
    subject[field] = value
    if field == "source_state":
        subject["source_revision"] = None
        subject["release_version"] = None
        subject["descriptor_verified"] = False
    promotion = attestation["promotion"]
    assert isinstance(promotion, dict)
    for flag in ("eligible", "live", "uncached", "judges_ran", "gates_passed"):
        promotion[flag] = False
    promotion["reasons"] = [field]
    _rehash_eval_attestation(summary)

    with pytest.raises(builder.PromotionAttestationBuildError, match=field):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )


@pytest.mark.parametrize(
    "field",
    ["eligible", "live", "uncached", "judges_ran", "gates_passed"],
)
def test_rejects_every_false_promotion_flag(field: str) -> None:
    summary = _summary()
    attestation = summary["attestation"]
    assert isinstance(attestation, dict)
    promotion = attestation["promotion"]
    assert isinstance(promotion, dict)
    promotion[field] = False
    promotion["eligible"] = False
    promotion["reasons"] = [field]
    _rehash_eval_attestation(summary)

    with pytest.raises(builder.PromotionAttestationBuildError, match=field):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )


def test_rejects_non_one_protocol_replicates() -> None:
    summary = _summary()
    attestation = summary["attestation"]
    assert isinstance(attestation, dict)
    protocol = attestation["protocol"]
    assert isinstance(protocol, dict)
    protocol["replicates"] = 2
    _rehash_eval_attestation(summary)

    with pytest.raises(builder.PromotionAttestationBuildError, match="protocol.replicates"):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )


def test_rejects_results_digest_tamper_and_ambiguous_results_json() -> None:
    summary = _summary()
    summary["results_sha256"] = "0" * 64
    with pytest.raises(builder.PromotionAttestationBuildError, match="exact results bytes"):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )

    duplicate = b'{"case_id":"one","case_id":"two","passed":true}\n'
    summary["results_sha256"] = hashlib.sha256(duplicate).hexdigest()
    with pytest.raises(builder.PromotionAttestationBuildError, match="duplicate"):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), duplicate, promoted_at=PROMOTED_AT
        )


@pytest.mark.parametrize(
    ("results", "message"),
    [
        (b"", "at least one"),
        (RESULTS.removesuffix(b"\n"), "end with one ASCII LF"),
        (RESULTS.replace(b"\n", b"\r\n"), "ASCII LF line endings"),
        (RESULTS + b"\n", "non-empty line"),
        (b"[]\n", "JSON object"),
        (b'{"passed":true,"suite":"core"}\n', "case_id.*required"),
        (b'{"case_id":"","passed":true,"suite":"core"}\n', "case_id"),
        (b'{"case_id":" case-1","passed":true,"suite":"core"}\n', "case_id"),
        (b'{"case_id":"case id","passed":true,"suite":"core"}\n', "safe identifier"),
        (
            ('{"case_id":"' + ("x" * 129) + '","passed":true,"suite":"core"}\n').encode(),
            "safe identifier",
        ),
        (b'{"case_id":"case-1","suite":"core"}\n', "passed.*required"),
        (b'{"case_id":"case-1","passed":1,"suite":"core"}\n', "passed must be a boolean"),
        (b'{"case_id":"case-1","passed":true}\n', "suite.*required"),
        (b'{"case_id":"case-1","passed":true,"suite":""}\n', "suite"),
        (b'{"case_id":"case-1","passed":true,"suite":" core"}\n', "suite"),
    ],
)
def test_rejects_malformed_result_records(results: bytes, message: str) -> None:
    summary = _summary()
    summary["results_sha256"] = hashlib.sha256(results).hexdigest()

    with pytest.raises(builder.PromotionAttestationBuildError, match=message):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            results,
            promoted_at=PROMOTED_AT,
        )


@pytest.mark.parametrize(
    "field",
    [
        "run_context_version",
        "case_semantics_version",
        "answer_models_served",
        "judge_models_served",
    ],
)
def test_requires_complete_per_result_provenance(field: str) -> None:
    record = _result_record()
    record.pop(field)
    results = _jsonl([record])
    summary = _summary(results_sha256=hashlib.sha256(results).hexdigest())

    with pytest.raises(builder.PromotionAttestationBuildError, match=field):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            results,
            promoted_at=PROMOTED_AT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("order", "case_id.*ordered case_manifest"),
        ("semantics", "case_semantics_version.*ordered case_manifest"),
        ("context", "run_context_version.*context_version"),
    ],
)
def test_reconciles_results_with_exact_attested_manifest_and_context(
    mutation: str,
    message: str,
) -> None:
    summary, results = _evidence_for_rows(
        [
            ("first-case", "core", True),
            ("second-case", "edge", False),
        ]
    )
    records = [json.loads(line) for line in results.splitlines()]
    if mutation == "order":
        records[0], records[1] = records[1], records[0]
    elif mutation == "semantics":
        records[0]["case_semantics_version"] = "9" * 64
    else:
        records[0]["run_context_version"] = "9" * 64
    changed_results = _jsonl(records)
    summary["results_sha256"] = hashlib.sha256(changed_results).hexdigest()

    with pytest.raises(builder.PromotionAttestationBuildError, match=message):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            changed_results,
            promoted_at=PROMOTED_AT,
        )


@pytest.mark.parametrize("mutation", ["missing", "answer_union", "judge_union"])
def test_reconciles_required_summary_served_model_unions(mutation: str) -> None:
    summary = _summary()
    if mutation == "missing":
        summary.pop("served_models")
    else:
        served = summary["served_models"]
        assert isinstance(served, dict)
        served["answer" if mutation == "answer_union" else "judge"] = ["unobserved-model"]

    with pytest.raises(builder.PromotionAttestationBuildError, match="served_models"):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            RESULTS,
            promoted_at=PROMOTED_AT,
        )


def test_rejects_duplicate_case_ids_across_suites() -> None:
    summary, results = _evidence_for_rows(
        [
            ("same-case", "core", True),
            ("other-case", "edge", False),
        ],
        actual_case_ids=["same-case", "same-case"],
    )

    with pytest.raises(builder.PromotionAttestationBuildError, match="duplicate case_id"):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            results,
            promoted_at=PROMOTED_AT,
        )


def test_accepts_exact_multi_suite_scoreboard_recomputed_from_results() -> None:
    summary, results = _evidence_for_rows(
        [
            ("core-1", "core", True),
            ("edge-1", "edge", False),
            ("core-2", "core", True),
        ]
    )

    attestation = builder.build_attestation(
        _runtime(),
        _summary_bytes(summary),
        results,
        promoted_at=PROMOTED_AT,
    )

    assert attestation.evaluation.results_sha256 == hashlib.sha256(results).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("overall_total", "record count"),
        ("executed_cases", "record count"),
        ("overall_passed", "total.passed"),
        ("suite_total", "core counts"),
        ("suite_passed", "core counts"),
        ("suite_pass_rate", "core.pass_rate"),
        ("missing_suite", "exactly match"),
        ("extra_suite", "exactly match"),
    ],
)
def test_rejects_every_results_to_summary_accounting_mismatch(
    mutation: str,
    message: str,
) -> None:
    summary = _summary()
    execution = summary["execution"]
    total = summary["total"]
    suites = summary["suites"]
    assert isinstance(execution, dict)
    assert isinstance(total, dict)
    assert isinstance(suites, dict)
    core = suites["core"]
    assert isinstance(core, dict)

    if mutation == "overall_total":
        total["total"] = 2
    elif mutation == "executed_cases":
        execution["executed_cases"] = 2
    elif mutation == "overall_passed":
        total["passed"] = 0
    elif mutation == "suite_total":
        core["total"] = 2
    elif mutation == "suite_passed":
        core["passed"] = 0
    elif mutation == "suite_pass_rate":
        core["pass_rate"] = 99.9
    elif mutation == "missing_suite":
        suites.pop("core")
    else:
        suites["extra"] = {"passed": 0, "total": 1, "pass_rate": 0.0}

    with pytest.raises(builder.PromotionAttestationBuildError, match=message):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(summary),
            RESULTS,
            promoted_at=PROMOTED_AT,
        )


def test_rejects_eval_attestation_digest_tamper() -> None:
    summary = _summary()
    attestation = summary["attestation"]
    assert isinstance(attestation, dict)
    attestation["attestation_version"] = "0" * 64

    with pytest.raises(builder.PromotionAttestationBuildError, match="digest"):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )


@pytest.mark.parametrize("which", ["runtime", "summary"])
def test_json_inputs_reject_duplicate_keys(
    tmp_path: Path,
    which: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    selected = runtime if which == "runtime" else summary
    raw = selected.read_text(encoding="utf-8")
    if which == "runtime":
        raw = raw.replace(
            '"function_version": "11"',
            '"function_version": "11", "function_version": "12"',
        )
    else:
        raw = raw.replace('"mode":"full"', '"mode":"full","mode":"smoke"', 1)
    selected.write_text(raw, encoding="utf-8")

    code = builder.main(
        [
            "--runtime",
            str(runtime),
            "--summary",
            str(summary),
            "--results",
            str(results),
            "--output",
            str(tmp_path / "promotion.json"),
        ]
    )

    assert code == 2
    assert "duplicate" in capsys.readouterr().err


@pytest.mark.parametrize("selected_name", ["runtime", "summary", "results"])
def test_rejects_every_symlinked_input(tmp_path: Path, selected_name: str) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    paths = {"runtime": runtime, "summary": summary, "results": results}
    selected = paths[selected_name]
    target = selected.with_suffix(selected.suffix + ".target")
    selected.replace(target)
    selected.symlink_to(target)

    with pytest.raises(builder.PromotionAttestationBuildError, match="non-symlink"):
        builder.build_attestation_file(
            runtime_path=runtime,
            summary_path=summary,
            results_path=results,
            output_path=tmp_path / "promotion.json",
            promoted_at=PROMOTED_AT,
        )


def test_refuses_output_symlink_without_touching_target(tmp_path: Path) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    target = tmp_path / "owned.json"
    target.write_bytes(b"keep me")
    output = tmp_path / "promotion.json"
    output.symlink_to(target)

    with pytest.raises(builder.PromotionAttestationBuildError, match="output symlink"):
        builder.build_attestation_file(
            runtime_path=runtime,
            summary_path=summary,
            results_path=results,
            output_path=output,
            promoted_at=PROMOTED_AT,
        )
    assert target.read_bytes() == b"keep me"


def test_atomic_replace_failure_preserves_old_output_and_cleans_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    output = tmp_path / "promotion.json"
    old = b'{"old":"attestation"}\n'
    output.write_bytes(old)
    entries_before = sorted(path.name for path in tmp_path.iterdir())

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(builder.os, "replace", fail_replace)
    with pytest.raises(builder.PromotionAttestationBuildError, match="could not be written"):
        builder.build_attestation_file(
            runtime_path=runtime,
            summary_path=summary,
            results_path=results,
            output_path=output,
            promoted_at=PROMOTED_AT,
        )
    assert output.read_bytes() == old
    assert sorted(path.name for path in tmp_path.iterdir()) == entries_before


def test_timestamp_validation_and_default_utc_clock(tmp_path: Path) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    output = tmp_path / "promotion.json"
    now = datetime(2026, 7, 30, 22, 40, 1, 123456, tzinfo=UTC)
    attestation = builder.build_attestation_file(
        runtime_path=runtime,
        summary_path=summary,
        results_path=results,
        output_path=output,
        clock=lambda: now,
    )
    assert attestation.promoted_at == now
    assert json.loads(output.read_bytes())["promoted_at"] == "2026-07-30T22:40:01.123456Z"

    for invalid in (
        "2026-07-30T22:40:00",
        "2026-07-30T15:40:00-07:00",
        "2026-07-30 22:40:00Z",
        "tomorrow",
    ):
        with pytest.raises(builder.PromotionAttestationBuildError, match="RFC3339 UTC"):
            builder.parse_promoted_at(invalid)


def test_rejects_future_explicit_promotion_time_against_injected_clock(
    tmp_path: Path,
) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    observed_at = PROMOTED_AT - timedelta(seconds=1)

    with pytest.raises(builder.PromotionAttestationBuildError, match="observed current time"):
        builder.build_attestation_file(
            runtime_path=runtime,
            summary_path=summary,
            results_path=results,
            output_path=tmp_path / "promotion.json",
            promoted_at=PROMOTED_AT,
            clock=lambda: observed_at,
        )
    assert not (tmp_path / "promotion.json").exists()

    with pytest.raises(builder.PromotionAttestationBuildError, match="observed current time"):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(),
            RESULTS,
            promoted_at=PROMOTED_AT,
            observed_at=observed_at,
        )


def test_public_builder_rejects_unambiguously_future_promotion_time() -> None:
    with pytest.raises(builder.PromotionAttestationBuildError, match="observed current time"):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(),
            RESULTS,
            promoted_at=datetime(2999, 1, 1, tzinfo=UTC),
        )


def test_rejects_run_timestamp_disagreement_and_promotion_before_evaluation() -> None:
    summary = _summary(run_at="2026-07-30T22:31:00Z")
    with pytest.raises(builder.PromotionAttestationBuildError, match="must equal"):
        builder.build_attestation(
            _runtime(), _summary_bytes(summary), RESULTS, promoted_at=PROMOTED_AT
        )

    with pytest.raises(builder.PromotionAttestationBuildError, match="promotion attestation"):
        builder.build_attestation(
            _runtime(),
            _summary_bytes(),
            RESULTS,
            promoted_at=datetime(2026, 7, 30, 22, 29, 59, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra"],
)
def test_runtime_object_has_an_exact_field_set(mutation: str) -> None:
    runtime = _runtime()
    if mutation == "missing":
        runtime.pop("function_version")
    else:
        runtime["environment"] = {"SECRET": "must-not-be-accepted"}

    with pytest.raises(builder.PromotionAttestationBuildError, match="invalid field set"):
        builder.build_attestation(runtime, _summary_bytes(), RESULTS, promoted_at=PROMOTED_AT)


def test_pure_builder_does_not_mutate_inputs() -> None:
    runtime = _runtime()
    summary = _summary()
    runtime_before = json.dumps(runtime, sort_keys=True)
    summary_before = json.dumps(summary, sort_keys=True)

    attestation = builder.build_attestation(
        runtime,
        _summary_bytes(summary),
        RESULTS,
        promoted_at=PROMOTED_AT,
    )

    assert dataclasses.is_dataclass(attestation)
    assert json.dumps(runtime, sort_keys=True) == runtime_before
    assert json.dumps(summary, sort_keys=True) == summary_before


def test_default_clock_rejects_naive_values(tmp_path: Path) -> None:
    runtime, summary, results = _write_inputs(tmp_path)
    with pytest.raises(builder.PromotionAttestationBuildError, match="timezone-aware"):
        builder.build_attestation_file(
            runtime_path=runtime,
            summary_path=summary,
            results_path=results,
            output_path=tmp_path / "promotion.json",
            clock=lambda: datetime(2026, 7, 30, 22, 40),
        )


def test_promoted_at_accepts_fractional_z_and_normalizes_to_utc() -> None:
    parsed = builder.parse_promoted_at("2026-07-30T22:40:01.120000Z")
    assert parsed == datetime(2026, 7, 30, 22, 40, 1, 120000, tzinfo=UTC)


def test_promotion_age_can_be_exactly_zero() -> None:
    run_at = datetime.fromisoformat(RUN_AT.replace("Z", "+00:00"))
    attestation = builder.build_attestation(
        _runtime(),
        _summary_bytes(),
        RESULTS,
        promoted_at=run_at,
    )
    assert attestation.promoted_at - attestation.evaluation.run_at == timedelta(0)
