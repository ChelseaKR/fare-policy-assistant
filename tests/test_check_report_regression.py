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


def test_suite_absent_from_committed_report_is_flagged():
    """This used to return [] on the reasoning that a vanished suite was
    `evals/provenance.py`'s problem. It never was: provenance compares prompt
    and corpus versions and has never looked at suite composition, so a report
    regenerated from a `--suite` subset passed every gate."""
    baseline = {"suites": {"conversation": {"passed": 6, "total": 6, "pass_rate": 100.0}}}
    evals_md = _evals_md({"refusal": {"passed": 10, "total": 10, "pass_rate": 100.0}})
    (finding,) = check(evals_md, baseline)
    assert "absent from the committed EVALS.md" in finding


def test_deleting_the_failing_cases_is_flagged_even_though_the_rate_rises():
    """The oldest way to turn a board green. `suite_regressed` needs both a
    pass-rate drop and a pass-count drop, so removing a suite's two failures
    takes it from 46/48 to 46/46 and trips neither."""
    baseline = {"suites": {"edge_cases": {"passed": 46, "total": 48, "pass_rate": 95.8}}}
    evals_md = _evals_md({"edge_cases": {"passed": 46, "total": 46, "pass_rate": 100.0}})
    (finding,) = check(evals_md, baseline)
    assert "2 case(s) removed" in finding


def test_a_suite_that_grows_is_not_flagged():
    baseline = {"suites": {"edge_cases": {"passed": 46, "total": 48, "pass_rate": 95.8}}}
    evals_md = _evals_md({"edge_cases": {"passed": 49, "total": 52, "pass_rate": 94.2}})
    assert check(evals_md, baseline) == []


def test_the_committed_report_holds_the_shrinkage_and_presence_checks():
    """The real repo state, not a fixture: every baseline suite is present in
    EVALS.md at no fewer cases than the baseline records."""
    import json

    from evals.check_report_regression import BASELINE_PATH, EVALS_MD_PATH

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert check(EVALS_MD_PATH.read_text(encoding="utf-8"), baseline) == []


# ── the gate must not pass on an absent input ────────────────────────────────
#
# `main()` returned 0 when `evals/baseline.json` was missing, which turned
# "someone deleted the baseline" into a green build. The skip has one legitimate
# use — this module ships in `template/MANIFEST.yaml`, and a freshly extracted
# repository has no baseline yet — so the fix distinguishes the two cases rather
# than removing the skip: a tree that has never been evaluated has no committed
# scoreboard either.


def _run_main(monkeypatch, tmp_path, *, evals_md: str | None) -> int:
    from evals import check_report_regression as gate

    monkeypatch.setattr(gate, "BASELINE_PATH", tmp_path / "absent-baseline.json")
    md_path = tmp_path / "EVALS.md"
    if evals_md is not None:
        md_path.write_text(evals_md, encoding="utf-8")
    monkeypatch.setattr(gate, "EVALS_MD_PATH", md_path)
    return gate.main()


def test_a_missing_baseline_with_a_committed_scoreboard_fails(monkeypatch, tmp_path, capsys):
    evals_md = "# report\n" + provenance.render_evals_md_block(
        {"suites": {"refusal": {"passed": 30, "total": 30, "pass_rate": 100.0}}}
    )
    assert _run_main(monkeypatch, tmp_path, evals_md=evals_md) == 1
    assert "gone missing rather than never existing" in capsys.readouterr().err


def test_a_missing_baseline_in_a_never_evaluated_tree_still_skips(monkeypatch, tmp_path, capsys):
    # The fresh-template case, which must stay green or `make template` produces
    # a repository that cannot pass its own gate on day one.
    assert _run_main(monkeypatch, tmp_path, evals_md=None) == 0
    assert "has not been evaluated yet" in capsys.readouterr().err


def test_a_missing_baseline_with_an_evals_md_that_carries_no_scoreboard_skips(
    monkeypatch, tmp_path
):
    assert _run_main(monkeypatch, tmp_path, evals_md="# report\nno provenance block\n") == 0


def test_the_committed_baseline_is_present(monkeypatch):
    """Belt and braces: in *this* repository the file must exist, so its
    deletion is a red build even if the branch above were ever weakened."""
    from evals import check_report_regression as gate

    assert gate.BASELINE_PATH.exists(), "evals/baseline.json is the gate's input"
