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


def test_every_check_the_grader_can_emit_has_a_planted_defect():
    """The self-test's own denominator.

    Until 2026-08-05 it planted defects against 8 of the 13 checks
    `evals/checks.py` emits. The five with no planted defect included
    `language_match` — the whole reason the multilingual suite is a language
    test and not a second English suite — and `refused` / `redirect_present`,
    which are the entirety of the refusal suite's deterministic scoring. Those
    two suites score 22/22 and 34/34. A check that has never failed and was
    never shown *able* to fail is indistinguishable from one that cannot, which
    is exactly how the parity gate sat saturated for a month.

    The check names are read out of the grader's source rather than listed here,
    so adding a check without a scenario for it fails this test instead of
    quietly widening the gap again.
    """
    import re

    from assistant import config
    from evals.selftest import _scenarios

    source = (config.REPO_ROOT / "evals" / "checks.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'CheckResult\(\s*"([a-z_]+)"', source))
    assert len(emitted) >= 13, f"expected the grader to emit 13+ checks, found {sorted(emitted)}"

    covered = {s.check for s in _scenarios()}
    missing = sorted(emitted - covered)
    assert not missing, (
        "checks the harness self-test never plants a defect against: "
        f"{missing}. A gate nothing has proven can fail is not evidence."
    )
