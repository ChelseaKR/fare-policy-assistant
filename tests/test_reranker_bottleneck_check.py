"""Reranker bottleneck check: rank-of-fact helper and report parsing.

Runs the offline BM25 retriever only (no network, no model calls) — same
scope as test_retrieval_ablation.py, whose fixtures and helpers this reuses.
"""

from __future__ import annotations

import json

from evals.reranker_bottleneck_check import _load_failing_ids, _rank_of_fact


class TestRankOfFact:
    def test_fact_at_first_position(self):
        assert _rank_of_fact("$2.00", ["fare is $2.00", "other text"]) == 1

    def test_fact_buried(self):
        assert _rank_of_fact("$2.00", ["other text", "fare is $2.00"]) == 2

    def test_fact_absent(self):
        assert _rank_of_fact("$9.99", ["fare is $2.00", "other text"]) is None

    def test_regex_fact(self):
        assert _rank_of_fact(r"re:\$\s?1\.00", ["nope", "seniors pay $1.00"]) == 2


class TestLoadFailingIds:
    def test_only_suites_with_failures_are_returned(self, tmp_path):
        report = {
            "suite_results": [
                {
                    "suite_name": "groundedness",
                    "failing_examples": [{"item_id": "edge-001", "detail": "x"}],
                },
                {"suite_name": "adversarial", "failing_examples": []},
            ]
        }
        path = tmp_path / "eval-report.json"
        path.write_text(json.dumps(report), encoding="utf-8")

        failing = _load_failing_ids(path)

        assert list(failing) == ["groundedness"]
        assert failing["groundedness"] == [{"item_id": "edge-001", "detail": "x"}]
