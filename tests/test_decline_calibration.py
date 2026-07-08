"""Decline-threshold calibration helpers (FIX-07 / ADR 0009).

The labeling and decision logic is what the calibrated threshold rests on, so
it is tested directly, offline, against the small fixture corpus — the same
split as test_retrieval_ablation.py: unit-test the helpers, run `main`'s full
sweep against the real corpus by hand.
"""

from __future__ import annotations

from evals.decline_calibration import _declines, _question, labeled_cases, sweep


class TestQuestion:
    def test_single_turn_question(self):
        assert _question({"question": "How much is the fare?"}) == "How much is the fare?"

    def test_single_turn_list(self):
        assert _question({"turns": ["Just one turn"]}) == "Just one turn"

    def test_multiturn_prepends_prior_turn(self):
        # Matches answer._retrieval_query: the follow-up inherits the turn
        # before it, since that is what the pipeline actually retrieves on.
        case = {"turns": ["What proof do I need for MST?", "Does it cover my spouse too?"]}
        assert _question(case) == "What proof do I need for MST? Does it cover my spouse too?"

    def test_no_question_or_turns(self):
        assert _question({"expected_behavior": "answer"}) is None


class TestLabeledCases:
    def test_splits_by_retrieval_signal_and_expected_behavior(self, monkeypatch):
        suites = [
            {
                "cases": [
                    {"question": "Do youth ride free on Yolobus?", "expected_behavior": "answer"},
                    {"question": "Is this partial?", "expected_behavior": "partial"},
                    {
                        "question": "How much is BART?",
                        "expected_behavior": "refuse_redirect",
                        "retrieval_signal": "decline",
                    },
                    {
                        # A guard-catch refusal (PII/injection/etc.), not
                        # tagged for retrieval — must land in neither set.
                        "question": "My SSN is 123-45-6789",
                        "expected_behavior": "refuse_redirect",
                    },
                ]
            }
        ]
        monkeypatch.setattr("evals.decline_calibration.load_suites", lambda: suites)
        should_answer, should_decline = labeled_cases()
        assert should_answer == ["Do youth ride free on Yolobus?", "Is this partial?"]
        assert should_decline == ["How much is BART?"]


class TestDeclinesAndSweep:
    def test_declines_true_when_no_results(self, retriever):
        assert _declines(retriever, "asdkjfh qwoieru zzzxyq", z=100.0, coverage=100.0) is True

    def test_declines_false_for_confident_relevant_result(self, retriever):
        q = "Do youth ride free on Yolobus?"
        assert _declines(retriever, q, z=-100.0, coverage=0.0) is False

    def test_sweep_reports_full_coverage_at_permissive_thresholds(self, retriever):
        should_answer = ["Do youth ride free on Yolobus?"]
        should_decline = ["weather forecast astronomy parliament"]
        rows = sweep(retriever, should_answer, should_decline)
        permissive = next(r for r in rows if r[0] == 0.0 and r[1] == 0.0)
        z, coverage, answer_coverage, decline_recall = permissive
        assert answer_coverage == 1.0
