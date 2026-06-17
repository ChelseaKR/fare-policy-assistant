from evals.calibration import _cohen_kappa, calibrate, load_labels


def test_labels_load_and_cover_both_judges():
    labels = load_labels()
    assert len(labels) >= 10
    assert {lab.judge for lab in labels} == {"groundedness", "helpfulness"}


def test_cohen_kappa_perfect_and_chance():
    assert _cohen_kappa([(True, True), (False, False)]) == 1.0
    # All-agree but one rater constant → kappa undefined-ish collapses to 0 or 1;
    # a mixed disagreement gives a value strictly below 1.
    assert _cohen_kappa([(True, True)] * 9 + [(False, True)]) < 1.0


def test_calibrate_matches_against_run_records():
    records = [
        {"case_id": "ground-001", "judges": [{"name": "groundedness", "passed": True}]},
        {"case_id": "ground-024", "judges": [{"name": "groundedness", "passed": False}]},
    ]
    labels = [lab for lab in load_labels() if lab.case_id in {"ground-001", "ground-024"}]
    out = calibrate(records, labels)
    assert out["n_matched"] == 2
    assert out["agreement"] == 1.0  # human agrees: 001 grounded, 024 contradicted


def test_validate_accepts_multiturn_and_rejects_too_short():
    import pytest

    from evals.runner import validate_cases
    ok = [{"cases": [{"id": "c1", "turns": ["a?", "b?"],
                      "expected_behavior": "answer", "rationale": "x"}]}]
    validate_cases(ok)  # no raise
    bad = [{"cases": [{"id": "c2", "turns": ["only one?"],
                       "expected_behavior": "answer", "rationale": "x"}]}]
    with pytest.raises(SystemExit):
        validate_cases(bad)
    missing = [{"cases": [{"id": "c3", "expected_behavior": "answer", "rationale": "x"}]}]
    with pytest.raises(SystemExit):
        validate_cases(missing)
