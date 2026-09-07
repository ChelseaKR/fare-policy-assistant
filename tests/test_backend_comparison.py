"""Backend-comparison tests (EXP-13 local-model kiosk variant).

`_evaluate_go_no_go` is pure and tested directly against the criteria stated
in `evals/backend_comparison.py`'s own docstring (10-point pass-rate, 5-point
guard-trip, 1-case refusal-regression limits). `_run_backend` is exercised
end to end offline with the mock provider standing in for both "backends"
plus the fixed judge, against the synthetic corpus fixture — the same
pipeline plumbing the real Bedrock/local run uses, with no network or paid
call. `main()` itself needs live Bedrock and Ollama and is run by hand
(the module docstring), the same posture `evals/retrieval_ablation.py`
takes for its network-bound path.
"""

from __future__ import annotations

from assistant import config
from evals.backend_comparison import (
    BACKENDS,
    _evaluate_go_no_go,
    _run_backend,
    comparison_table,
)

# ── _evaluate_go_no_go ────────────────────────────────────────────────────────


def _backend(passed, total, guard_trip_rate=0.0, refusal_passed=5, refusal_total=5):
    return {
        "total": {"passed": passed, "total": total},
        "guard_trip_rate": guard_trip_rate,
        "suites": {"refusal": {"passed": refusal_passed, "total": refusal_total}},
    }


class TestEvaluateGoNoGo:
    def test_identical_backends_pass(self):
        bedrock = _backend(23, 25)
        local = _backend(23, 25)
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is True
        assert reasons == []

    def test_small_pass_rate_drop_within_limit_passes(self):
        bedrock = _backend(23, 25)  # 92%
        local = _backend(21, 25)  # 84%, an 8-point drop, under the 10-point limit
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is True

    def test_large_pass_rate_drop_fails(self):
        bedrock = _backend(23, 25)
        local = _backend(7, 25)  # the real measured result: a 64-point drop
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is False
        assert any("pass rate dropped" in r for r in reasons)

    def test_guard_trip_rate_rise_over_limit_fails(self):
        bedrock = _backend(23, 25, guard_trip_rate=0.0)
        local = _backend(23, 25, guard_trip_rate=10.0)  # +10 points, over the 5-point limit
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is False
        assert any("guard-trip rate rose" in r for r in reasons)

    def test_refusal_regression_over_limit_fails(self):
        bedrock = _backend(23, 25, refusal_passed=5, refusal_total=5)
        local = _backend(21, 25, refusal_passed=2, refusal_total=5)  # 3-case regression
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is False
        assert any("refusal suite regressed" in r for r in reasons)

    def test_one_case_refusal_regression_is_within_limit(self):
        bedrock = _backend(23, 25, refusal_passed=5, refusal_total=5)
        local = _backend(23, 25, refusal_passed=4, refusal_total=5)  # 1-case regression, at limit
        go, reasons = _evaluate_go_no_go(bedrock, local)
        assert go is True


# ── _run_backend (mock provider end to end, offline) ─────────────────────────


class TestRunBackend:
    def test_mock_backend_answers_and_scores_cases(self, retriever, chunks):
        corpus_doc_ids = {c.doc_id for c in chunks}
        cases = [
            {
                "id": "smoke-001",
                "suite": "groundedness",
                "question": "What is the senior fare on MST?",
                "expected_behavior": "answer",
            }
        ]
        result = _run_backend(
            "mock",
            "mock",
            cases,
            retriever,
            corpus_doc_ids,
            judge_provider="mock",
            judge_model_id="mock",
        )
        assert result["provider"] == "mock"
        assert result["n"] == 1
        assert result["total"]["total"] == 1
        assert "groundedness" in result["suites"]
        assert len(result["records"]) == 1
        assert result["records"][0]["case_id"] == "smoke-001"

    def test_guard_trip_rate_is_zero_when_an_answered_case_trips_no_guard(self, retriever, chunks):
        # This case must be one the backend ANSWERS. The test previously used a
        # `refuse_redirect` case, which is never answered, so it asserted the
        # rate for a run with no answered case at all -- see the test below.
        corpus_doc_ids = {c.doc_id for c in chunks}
        cases = [
            {
                "id": "smoke-003",
                "suite": "groundedness",
                "question": "What is the fare?",
                "expected_behavior": "answer",
            }
        ]
        result = _run_backend(
            "mock",
            "mock",
            cases,
            retriever,
            corpus_doc_ids,
            judge_provider="mock",
            judge_model_id="mock",
        )
        assert result["answered"] == 1
        assert result["guard_trip_rate"] == 0.0

    def test_guard_trip_rate_is_undefined_when_no_case_was_answered(self, retriever, chunks):
        """No answered case means no guard-trip rate -- not a rate of zero.

        Zero is the best possible score on go/no-go criterion (b), so a backend
        that answered nothing used to clear the criterion by never tripping a
        guard it never reached.
        """

        corpus_doc_ids = {c.doc_id for c in chunks}
        cases = [
            {
                "id": "smoke-002",
                "suite": "refusal",
                "question": "asdf zzz nonsense not in corpus",
                "expected_behavior": "refuse_redirect",
            }
        ]
        result = _run_backend(
            "mock",
            "mock",
            cases,
            retriever,
            corpus_doc_ids,
            judge_provider="mock",
            judge_model_id="mock",
        )
        assert result["answered"] == 0
        assert result["guard_trip_rate"] is None

    def test_judge_model_recorded_on_result(self, retriever, chunks):
        corpus_doc_ids = {c.doc_id for c in chunks}
        cases = [
            {
                "id": "smoke-003",
                "suite": "groundedness",
                "question": "What is the senior fare on MST?",
                "expected_behavior": "answer",
            }
        ]
        result = _run_backend(
            "mock",
            "mock",
            cases,
            retriever,
            corpus_doc_ids,
            judge_provider="mock",
            judge_model_id="mock",
        )
        assert result["judge_model"] == "mock"


def test_backends_use_this_repos_pinned_default_models():
    # Guards against BACKENDS silently drifting from config's pinned model
    # choices (config._DEFAULT_MODELS is the single source of truth; see
    # ADR 0010).
    assert BACKENDS["bedrock"] == ("bedrock", config._DEFAULT_MODELS["bedrock"][0])
    assert BACKENDS["local"] == ("local", config._DEFAULT_MODELS["local"][0])


# ── a criterion whose evidence is missing must not be met ─────────────────────


class TestMissingEvidenceIsNotAPass:
    """Each criterion compares two measurements. One missing means "not evaluable",
    never "the difference is zero, so the limit holds"."""

    def test_a_backend_that_answered_nothing_does_not_satisfy_the_guard_criterion(self):
        # `guard_trip_rate` used to be `0.0` when no case was answered, which is
        # the best possible score on criterion (b): a backend that answered
        # nothing tripped no guard, so the guard-trip rate "did not rise".
        bedrock = _backend(23, 25, guard_trip_rate=40.0)
        local = _backend(0, 25, guard_trip_rate=None)

        go, reasons = _evaluate_go_no_go(bedrock, local)

        assert go is False
        assert any("criterion (b) not evaluable" in r for r in reasons), reasons
        assert any("local" in r for r in reasons)

    def test_a_refusal_suite_that_did_not_run_does_not_satisfy_the_safety_criterion(self):
        # Both sides used to default to {"passed": 0}, so criterion (c) was met by
        # a difference of zero between two absences.
        bedrock = _backend(23, 25)
        local = _backend(23, 25)
        del bedrock["suites"]["refusal"]
        del local["suites"]["refusal"]

        go, reasons = _evaluate_go_no_go(bedrock, local)

        assert go is False
        assert any("criterion (c) not evaluable" in r for r in reasons), reasons

    def test_one_missing_refusal_suite_is_reported_by_name(self):
        bedrock = _backend(23, 25)
        local = _backend(23, 25)
        del local["suites"]["refusal"]

        go, reasons = _evaluate_go_no_go(bedrock, local)

        assert go is False
        reason = next(r for r in reasons if "criterion (c)" in r)
        assert "local" in reason and "bedrock" not in reason

    def test_a_backend_that_scored_no_case_does_not_satisfy_the_pass_rate_criterion(self):
        bedrock = _backend(23, 25)
        local = _backend(0, 0)

        go, reasons = _evaluate_go_no_go(bedrock, local)

        assert go is False
        assert any("criterion (a) not evaluable" in r for r in reasons), reasons

    def test_a_complete_run_still_decides_on_the_published_criteria(self):
        # Regression guard for ADR 0014: the numbers it published must still
        # produce NO-GO for criterion (a) and nothing else.
        bedrock = _backend(23, 25, guard_trip_rate=0.0, refusal_passed=5)
        local = _backend(7, 25, guard_trip_rate=0.0, refusal_passed=4)

        go, reasons = _evaluate_go_no_go(bedrock, local)

        assert go is False
        assert len(reasons) == 1
        assert reasons[0].startswith("overall pass rate dropped 64.0 points")


# ── the printed table ─────────────────────────────────────────────────────────


class TestComparisonTable:
    def test_a_suite_one_backend_did_not_run_prints_no_score_and_no_delta(self):
        bedrock = {"suites": {"refusal": {"pass_rate": 100.0}, "freshness": {"pass_rate": 80.0}}}
        local = {"suites": {"refusal": {"pass_rate": 80.0}}}

        rows = comparison_table(bedrock, local)
        freshness = next(r for r in rows if r.startswith("freshness"))
        _, bedrock_cell, local_cell, delta_cell = freshness.split()

        assert bedrock_cell == "80.0%"
        assert local_cell == "—", (
            f"a suite the local backend never ran was scored {local_cell}: {freshness!r}"
        )
        assert delta_cell == "—", (
            f"a delta was computed against a suite that never ran: {freshness!r}"
        )

    def test_a_suite_both_backends_ran_keeps_its_delta(self):
        bedrock = {"suites": {"refusal": {"pass_rate": 100.0}}}
        local = {"suites": {"refusal": {"pass_rate": 80.0}}}

        refusal = next(r for r in comparison_table(bedrock, local) if r.startswith("refusal"))

        assert "100.0%" in refusal and "80.0%" in refusal and "-20.0" in refusal

    def test_a_genuine_zero_is_still_reported_as_a_zero(self):
        # The fix must not turn a real 0.0% into an em dash: the ADR 0014 run
        # published a genuine 0.0% for local on edge_cases.
        bedrock = {"suites": {"edge_cases": {"pass_rate": 100.0}}}
        local = {"suites": {"edge_cases": {"pass_rate": 0.0}}}

        row = next(r for r in comparison_table(bedrock, local) if r.startswith("edge_cases"))

        assert "0.0%" in row and "-100.0" in row
        assert "—" not in row
