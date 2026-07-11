"""evals/check_report_regression.py: the committed-EVALS.md regression gate.

See docs/audits/eval-regression-2026-06-30.md for why this exists: a live run's
exit code is not enough to stop a regressed report from being committed, so
this re-checks the *committed* scoreboard (embedded in EVALS.md's provenance
comment by evals/report.py) against the *committed* baseline.
"""

from __future__ import annotations

from evals import provenance
from evals.check_report_regression import check


def _evals_md(suites: dict) -> str:
    return "not important\n" + provenance.render_evals_md_block(
        {"run_id": "2026-01-01T00:00:00+00:00", "corpus_version": "abc", "suites": suites}
    )


def test_clean_report_has_no_regressions():
    baseline = {"suites": {"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}}}
    evals_md = _evals_md({"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}})
    assert check(evals_md, baseline) == []


def test_regressed_committed_report_is_flagged():
    # This is exactly the real, current repo state: multilingual dropped from
    # 20/21 at baseline to 18/21 in the committed EVALS.md.
    baseline = {"suites": {"multilingual": {"passed": 20, "total": 21, "pass_rate": 95.2}}}
    evals_md = _evals_md({"multilingual": {"passed": 18, "total": 21, "pass_rate": 85.7}})
    regressions = check(evals_md, baseline)
    assert len(regressions) == 1
    assert "multilingual" in regressions[0]
    assert "20/21" in regressions[0]
    assert "18/21" in regressions[0]


def test_single_case_drop_on_small_suite_not_flagged():
    # Same tolerance as check_regression / suite_regressed: a single-case move
    # on a small suite is judge noise, not a regression.
    baseline = {"suites": {"conversation": {"passed": 5, "total": 6, "pass_rate": 83.3}}}
    evals_md = _evals_md({"conversation": {"passed": 4, "total": 6, "pass_rate": 66.7}})
    assert check(evals_md, baseline) == []


def test_improvement_never_flagged():
    baseline = {"suites": {"refusal": {"passed": 18, "total": 19, "pass_rate": 94.7}}}
    evals_md = _evals_md({"refusal": {"passed": 19, "total": 19, "pass_rate": 100.0}})
    assert check(evals_md, baseline) == []


def test_missing_provenance_block_is_flagged_not_crashed():
    baseline = {"suites": {"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}}}
    regressions = check("no provenance comment here", baseline)
    assert len(regressions) == 1
    assert "no embedded suites provenance" in regressions[0]


def test_suite_absent_from_committed_report_is_not_a_crash():
    # A suite renamed or dropped is out of scope for this check (it is a
    # provenance/structure question, not a regression this function judges).
    baseline = {"suites": {"conversation": {"passed": 6, "total": 6, "pass_rate": 100.0}}}
    evals_md = _evals_md({"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}})
    assert check(evals_md, baseline) == []
