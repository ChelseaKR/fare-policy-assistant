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
from evals.backend_comparison import BACKENDS, _evaluate_go_no_go, _run_backend

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

    def test_guard_trip_rate_is_zero_when_no_flags(self, retriever, chunks):
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
        assert result["guard_trip_rate"] == 0.0

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
