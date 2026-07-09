"""Retrieval-recall ablation helpers.

The recall logic — does the retrieved top-k contain a chunk that states each
required fact — is what the dense-vs-BM25 decision rests on, so it is tested
directly against the offline BM25 retriever. The `main` entry point builds a
dense (sentence-transformers) retriever that downloads a model, so it is a
heavy, network-bound analysis script run by hand, not in the unit suite.
"""

from __future__ import annotations

from evals.retrieval_ablation import _fact_in_chunks, _recall


class TestFactInChunks:
    def test_literal_fact_found(self):
        assert _fact_in_chunks("$2.00", ["The fare is $2.00.", "other"])

    def test_regex_fact_found(self):
        assert _fact_in_chunks(r"re:\$\s?1\.00", ["seniors pay $1.00"])

    def test_fact_absent(self):
        assert not _fact_in_chunks("$9.99", ["The fare is $2.00."])


class TestRecall:
    def test_counts_cases_with_all_facts_retrieved(self, retriever):
        cases = [
            {"question": "Do youth ride free on Yolobus?", "required_facts": ["free"]},
            {
                "question": "Do youth ride free on Yolobus?",
                "required_facts": ["this fact is nowhere in the corpus xyzzy"],
            },
        ]
        hits, total = _recall(retriever, cases)
        assert total == 2
        assert hits == 1  # first case's fact is retrievable, second's is not

    def test_multiturn_case_uses_last_turn_as_query(self, retriever):
        cases = [{"turns": ["hi", "Do youth ride free on Yolobus?"], "required_facts": ["free"]}]
        hits, total = _recall(retriever, cases)
        assert (hits, total) == (1, 1)
