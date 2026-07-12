"""The harness self-test must actually catch every planted defect (and not
wrongly fail a clean answer). This test makes that a merge gate: if a check
stops being load-bearing, `run_selftest` reports a survivor and CI goes red."""

from evals.selftest import Outcome, _report, run_selftest


def test_every_planted_defect_is_caught():
    outcomes = run_selftest()
    assert outcomes, "self-test produced no scenarios"
    survivors = [o for o in outcomes if not o.caught]
    false_positives = [o for o in outcomes if not o.clean_passed]
    assert not survivors, f"defects the gate did not catch: {[o.name for o in survivors]}"
    assert not false_positives, f"clean answers wrongly failed: {[o.name for o in false_positives]}"
    assert all(o.ok for o in outcomes)


def test_report_returns_zero_when_all_ok(capsys):
    ok = [Outcome("scenario", "some_check", clean_passed=True, caught=True)]
    assert _report(ok) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "[OK  ]" in out


def test_report_returns_nonzero_on_a_survivor(capsys):
    survivor = [Outcome("leaky", "some_check", clean_passed=True, caught=False)]
    assert _report(survivor) == 1
    assert "DEFECT SURVIVED" in capsys.readouterr().out


def test_report_returns_nonzero_on_a_misbuilt_clean(capsys):
    misbuilt = [Outcome("bad-clean", "some_check", clean_passed=False, caught=True)]
    assert _report(misbuilt) == 1
    assert "mis-built" in capsys.readouterr().out
