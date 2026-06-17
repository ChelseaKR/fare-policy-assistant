from evals.runner import suite_regressed


def _s(passed, total):
    return {"passed": passed, "total": total, "pass_rate": round(100 * passed / total, 1)}


def test_single_case_drop_on_small_suite_is_tolerated():
    # conversation suite, 6 cases: 5/6 -> 4/6 is 16.7 points but only one case.
    assert not suite_regressed(_s(5, 6), _s(4, 6))


def test_two_case_drop_is_a_regression():
    assert suite_regressed(_s(6, 6), _s(4, 6))


def test_large_suite_small_percent_drop_not_flagged():
    # 33-case suite losing one case is ~3 points but a single case.
    assert not suite_regressed(_s(33, 33), _s(32, 33))


def test_real_multi_case_regression_caught_on_large_suite():
    # Two cases on a larger suite clears both the points and the case floor.
    assert suite_regressed(_s(29, 29), _s(27, 29))


def test_improvement_never_flagged():
    assert not suite_regressed(_s(4, 6), _s(6, 6))
