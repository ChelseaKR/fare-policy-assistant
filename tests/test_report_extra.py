"""Report-generator branches beyond the basic scoreboard (test_report.py).

Covers the cost line, Spanish-parity table, judge-calibration section,
multi-turn failure rendering, the guard-blocked raw text, the no-failures case,
and the end-to-end file generation (EVALS.md + HTML), all from hand-built run
data written to a temp directory so the committed report is never touched.
"""

from __future__ import annotations

import json

import pytest

from assistant import config
from evals import report
from evals.calibration import answer_hash

SUMMARY_WITH_COST = {
    "run_at": "2026-06-12T01:00:00+00:00",
    "mode": "full",
    "offline": False,
    "judges_ran": True,
    "answer_model": "claude-haiku-4-5",
    "judge_model": "claude-sonnet-4-6",
    "prompt_versions": {"system": "v1 2026-06-11"},
    "duration_seconds": 2.0,
    "cost": {
        "answer_model": {"est_usd": 0.0012},
        "judge_model": {"est_usd": 0.0008},
        "total_tokens": 5000,
        "total_est_usd": 0.0020,
    },
    "suites": {
        "multilingual": {"passed": 1, "total": 2, "pass_rate": 50.0},
        "groundedness": {"passed": 1, "total": 1, "pass_rate": 100.0},
    },
    "total": {"passed": 2, "total": 3},
}


def _rec(**kw):
    base = {
        "case_id": "x",
        "suite": "groundedness",
        "mirror_of": None,
        "passed": True,
        "question": "q?",
        "rationale": "r",
        "answer": "a [doc:mst-fares]",
        "kind": "answered",
        "passages": [],
        "checks": [],
        "judges": [],
        "raw_model_answer": "",
        "turns": None,
    }
    base.update(kw)
    return base


def test_cost_line_rendered_when_present():
    md = report.generate_markdown(SUMMARY_WITH_COST, [_rec()])
    assert "Cost (estimated): $0.0020" in md
    assert "5,000 tokens" in md


def test_cost_line_absent_is_labeled():
    summary = {**SUMMARY_WITH_COST}
    summary.pop("cost")
    assert "- Cost: not recorded for this run" in report._cost_line(summary)


def test_spanish_parity_table_pairs_mirror():
    records = [
        _rec(case_id="ml-001", suite="multilingual", mirror_of="ground-001", passed=True),
        _rec(case_id="ground-001", suite="groundedness", passed=False),
    ]
    md = report.generate_markdown(SUMMARY_WITH_COST, records)
    assert "## Spanish parity" in md
    assert "| ml-001 | ✓ | ground-001 | ✗ |" in md


def test_stretch_language_parity_table_pairs_mirror():
    records = [
        _rec(case_id="tl-001", suite="stretch_tagalog", mirror_of="ground-001", passed=False),
        _rec(case_id="ground-001", suite="groundedness", passed=True),
    ]
    md = report.generate_markdown(SUMMARY_WITH_COST, records)
    assert "## Stretch-language parity (Tagalog)" in md
    assert "| tl-001 | ✗ | ground-001 | ✓ |" in md


def test_stretch_language_parity_absent_when_no_stretch_cases():
    md = report.generate_markdown(SUMMARY_WITH_COST, [_rec()])
    assert "## Stretch-language parity" not in md


def test_calibration_section_present_on_live_run():
    # A judge verdict that matches a committed human label drives the calibration
    # block; load_labels() reads the real evals/calibration/judge_labels.jsonl.
    # Each label is bound (answer_sha256) to the exact answer it graded, so the
    # fixture record must carry that same answer text or calibrate() correctly
    # reports it as stale rather than scoring it (see evals/calibration.py).
    from evals.calibration import load_labels

    labeled = next(lab for lab in load_labels() if lab.judge == "groundedness")
    answer = (
        "A single ride on an MST bus costs **$2.00** if you pay cash for a regular "
        "fixed-route fare [doc:mst-fares]. If you qualify for a discount fare, a "
        "single ride is **$1.00** [doc:mst-fares].\n\nBased on policies published as "
        "of 2026-06-12, I'd recommend confirming current fares with MST before your "
        "trip, as fares can change."
    )
    assert answer_hash(answer) == labeled.answer_sha256, (
        "fixture answer text drifted from the committed label's bound answer; "
        "update it to match evals/calibration/judge_labels.jsonl's ground-001 row"
    )
    records = [
        _rec(
            case_id=labeled.case_id,
            answer=answer,
            judges=[{"name": "groundedness", "passed": labeled.human_passed}],
        )
    ]
    md = report.generate_markdown(SUMMARY_WITH_COST, records)
    assert "## Judge calibration" in md
    assert "Raw agreement" in md


def test_calibration_section_skipped_when_judges_did_not_run():
    summary = {**SUMMARY_WITH_COST, "judges_ran": False}
    assert report._calibration_section(summary, [_rec()]) is None


def test_multiturn_failure_renders_conversation_and_blocked_text():
    records = [
        _rec(
            case_id="conv-001",
            suite="groundedness",
            passed=False,
            turns=["First question?", "Follow-up?"],
            raw_model_answer="Yes, you qualify — blocked by the guard.",
            checks=[{"name": "citation_present_and_resolvable", "passed": False, "detail": "none"}],
        )
    ]
    md = report.generate_markdown(SUMMARY_WITH_COST, records)
    assert "**Conversation:**" in md
    assert "1. First question?" in md
    assert "Model text the guard blocked" in md
    assert "you qualify" in md  # shown in the trace, never to the rider


def test_no_failures_message():
    summary = {
        **SUMMARY_WITH_COST,
        "suites": {"groundedness": {"passed": 1, "total": 1, "pass_rate": 100.0}},
    }
    md = report.generate_markdown(summary, [_rec(passed=True)])
    assert "No failures in this run." in md


# ── file generation ──────────────────────────────────────────────────────────


def _write_run(run_dir, summary, records):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary))
    (run_dir / "results.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    return run_dir


def test_generate_writes_markdown_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    run_dir = _write_run(tmp_path / "run1", SUMMARY_WITH_COST, [_rec()])
    report.generate(run_dir)
    md = (tmp_path / "EVALS.md").read_text()
    html = (tmp_path / "docs" / "eval-report.html").read_text()
    assert "# Evaluation Report" in md
    assert "<!doctype html>" in html and "Evaluation Report" in html


def test_latest_run_dir_picks_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVAL_RUNS_DIR", tmp_path)
    _write_run(tmp_path / "20260101T000000Z", SUMMARY_WITH_COST, [_rec()])
    _write_run(tmp_path / "20260202T000000Z", SUMMARY_WITH_COST, [_rec()])
    assert report.latest_run_dir().name == "20260202T000000Z"


def test_latest_run_dir_errors_with_no_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVAL_RUNS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        report.latest_run_dir()
