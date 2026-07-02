"""Counterfactual sensitivity suite (EXP-02).

The sensitivity suite is written as `pairs:` of minimal-pair `variants:`; the
runner flattens each variant into an ordinary case carrying a `pair_id`, scores
every variant with the same deterministic checks, then re-groups the results
into a pair-level verdict. A pair is only "distinguished" if every variant
passed — that is what proves the answer changed (or held) across the boundary
rather than one boilerplate answer satisfying one side.

These tests exercise loading/flattening, the pair-verdict logic, the summary
plumbing, and the report line, all offline (no model calls, no cost).
"""

from __future__ import annotations

import json

import pytest

from assistant import config
from evals import report, runner


@pytest.fixture
def tmp_runs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(config, "EVAL_RUNS_DIR", runs)
    return runs


# ── loading / flattening ─────────────────────────────────────────────────────


def test_sensitivity_suite_loads_and_flattens_variants_into_cases():
    (suite,) = runner.load_suites(only="sensitivity")
    assert suite["suite"] == "sensitivity"
    assert suite["pairs"], "the suite is authored as pairs"
    # Every pair contributes its variants to a flat `cases` list.
    expected_cases = sum(len(p["variants"]) for p in suite["pairs"])
    assert len(suite["cases"]) == expected_cases
    for case in suite["cases"]:
        assert case["suite"] == "sensitivity"
        assert case["pair_id"], "each flattened variant carries its parent pair id"
        assert case["boundary"], "each variant inherits the pair's boundary description"
        assert "question" in case and "expected_behavior" in case


def test_sensitivity_suite_has_about_fifteen_pairs_with_unique_variant_ids():
    (suite,) = runner.load_suites(only="sensitivity")
    assert 13 <= len(suite["pairs"]) <= 18, "~15 boundary pairs"
    ids = [c["id"] for c in suite["cases"]]
    assert len(ids) == len(set(ids)), "variant ids are unique"
    # Variant ids nest under their pair id (sens-001 → sens-001a / sens-001b).
    for case in suite["cases"]:
        assert case["id"].startswith(case["pair_id"])


def test_committed_sensitivity_suite_validates():
    runner.validate_cases(runner.load_suites(only="sensitivity"))
    # And it does not break whole-corpus validation either.
    runner.validate_cases(runner.load_suites())


# ── pair-validation guards ───────────────────────────────────────────────────


def test_validate_rejects_pair_with_a_single_variant():
    bad = [{
        "pairs": [{
            "id": "sens-x", "boundary": "b",
            "variants": [{"id": "sens-xa", "question": "q?",
                          "expected_behavior": "answer", "rationale": "r"}],
        }],
    }]
    with pytest.raises(SystemExit, match="at least two variants"):
        runner.validate_cases(bad)


def test_validate_rejects_pair_missing_boundary():
    bad = [{
        "pairs": [{
            "id": "sens-y",
            "variants": [
                {"id": "sens-ya", "question": "q?", "expected_behavior": "answer",
                 "rationale": "r"},
                {"id": "sens-yb", "question": "q?", "expected_behavior": "answer",
                 "rationale": "r"},
            ],
        }],
    }]
    with pytest.raises(SystemExit, match="missing `boundary`"):
        runner.validate_cases(bad)


# ── pair-verdict logic ───────────────────────────────────────────────────────


def test_pair_passes_only_when_every_variant_passes():
    records = [
        {"pair_id": "sens-001", "passed": True},
        {"pair_id": "sens-001", "passed": True},
    ]
    assert runner.pair_verdicts(records) == {"sens-001": True}


def test_mixed_pass_fail_pair_reports_failed():
    records = [
        {"pair_id": "sens-001", "passed": True},
        {"pair_id": "sens-001", "passed": False},  # one side held, the other didn't
    ]
    assert runner.pair_verdicts(records) == {"sens-001": False}


def test_pair_verdicts_groups_multiple_pairs_and_ignores_unpaired_records():
    records = [
        {"pair_id": "sens-001", "passed": True},
        {"pair_id": "sens-001", "passed": True},
        {"pair_id": "sens-002", "passed": True},
        {"pair_id": "sens-002", "passed": False},
        {"pair_id": None, "passed": False},        # ordinary (non-pair) case
        {"suite": "refusal", "passed": True},       # no pair_id key at all
    ]
    verdicts = runner.pair_verdicts(records)
    assert verdicts == {"sens-001": True, "sens-002": False}
    assert sum(verdicts.values()) == 1


# ── end-to-end offline run records pair stats ────────────────────────────────


def test_offline_sensitivity_run_writes_pairs_passed_and_total(tmp_runs):
    run_dir = runner.run(offline=True, suite="sensitivity")
    summary = json.loads((run_dir / "summary.json").read_text())
    sens = summary["suites"]["sensitivity"]
    assert "pairs_passed" in sens and "pairs_total" in sens
    # One pair per authored boundary; total variants exceed the pair count.
    (suite,) = runner.load_suites(only="sensitivity")
    assert sens["pairs_total"] == len(suite["pairs"])
    assert sens["total"] == sum(len(p["variants"]) for p in suite["pairs"])
    assert 0 <= sens["pairs_passed"] <= sens["pairs_total"]
    # Every trace in the run carries its pair id.
    records = [json.loads(x) for x in (run_dir / "results.jsonl").read_text().splitlines()]
    assert records and all(r["pair_id"] for r in records)


# ── report line ──────────────────────────────────────────────────────────────


def test_report_renders_boundary_pairs_line():
    summary = {
        "run_at": "2026-07-02T00:00:00+00:00", "mode": "full", "offline": True,
        "judges_ran": False, "answer_model": "mock", "judge_model": "mock",
        "prompt_versions": {"system": "v1"}, "duration_seconds": 1.0,
        "suites": {"sensitivity": {"passed": 24, "total": 30, "pass_rate": 80.0,
                                   "pairs_passed": 12, "pairs_total": 15}},
        "total": {"passed": 24, "total": 30},
    }
    md = report.generate_markdown(summary, [])
    assert "12/15 boundary pairs correctly distinguished" in md


def test_report_omits_sensitivity_line_when_no_pair_stats():
    summary = {
        "run_at": "2026-07-02T00:00:00+00:00", "mode": "full", "offline": True,
        "judges_ran": False, "answer_model": "mock", "judge_model": "mock",
        "prompt_versions": {"system": "v1"}, "duration_seconds": 1.0,
        "suites": {"refusal": {"passed": 1, "total": 1, "pass_rate": 100.0}},
        "total": {"passed": 1, "total": 1},
    }
    md = report.generate_markdown(summary, [])
    assert "boundary pairs correctly distinguished" not in md
