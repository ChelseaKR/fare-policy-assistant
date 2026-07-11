"""Retrieval-recall ablation: BM25-only vs hybrid (BM25 + dense).

ADR 0001 implemented dense retrieval but left it off, with the multilingual
suite to decide whether it earns a place — the hypothesis being that Spanish
questions over English-only documents (three of four agencies) need dense to
bridge the language gap. This measures that directly and cheaply, with no model
calls: for every eval case that names a required fact, does the retrieved top-k
contain a chunk that actually states the fact? That is retrieval recall of the
answer-bearing passage, isolated from the answer model and the judge.

    uv run python -m evals.retrieval_ablation

Prints recall for each mode, overall and split by language, so the dense
decision rests on evidence. See ADR 0007.
"""

from __future__ import annotations

import re

from assistant import config
from assistant.ingest import load_chunks
from assistant.retrieve import Retriever
from evals.runner import load_suites


def _fact_in_chunks(fact: str, chunks_text: list[str]) -> bool:
    pattern = fact[3:] if fact.startswith("re:") else re.escape(fact)
    joined = "\n".join(chunks_text)
    return bool(re.search(pattern, joined, re.I))


def _recall(retriever: Retriever, cases: list[dict]) -> tuple[int, int]:
    """How many cases have all their required facts present in the retrieved set."""
    hits = 0
    for case in cases:
        q = case.get("question") or case["turns"][-1]
        retrieved = [sc.chunk.text for sc in retriever.search(q)]
        if all(_fact_in_chunks(f, retrieved) for f in case["required_facts"]):
            hits += 1
    return hits, len(cases)


def main() -> None:
    chunks = load_chunks()
    cases = [
        c
        for s in load_suites()
        for c in s["cases"]
        if c.get("required_facts") and (c.get("question") or c.get("turns"))
    ]
    by_lang = {
        "en": [c for c in cases if c.get("language", "en") == "en"],
        "es": [c for c in cases if c.get("language") == "es"],
        "tl": [c for c in cases if c.get("language") == "tl"],
    }

    bm25 = Retriever(chunks, config.RetrievalConfig(use_dense=False))
    hybrid = Retriever(chunks, config.RetrievalConfig(use_dense=True))

    print(f"{'segment':<10} {'n':>4} {'BM25':>8} {'hybrid':>8} {'delta':>7}")
    for label, subset in [
        ("all", cases),
        ("english", by_lang["en"]),
        ("spanish", by_lang["es"]),
        # Stretch suite (evals/suites/stretch_tagalog.yaml, docs/ROADMAP.md
        # P3-3): no Tagalog source document exists, so this recall number is
        # entirely a test of the query-side lexicon in
        # assistant.retrieve._TL_EN_LEXICON, not of a translated page.
        ("tagalog", by_lang["tl"]),
    ]:
        if not subset:
            continue
        b_hits, n = _recall(bm25, subset)
        h_hits, _ = _recall(hybrid, subset)
        b_pct, h_pct = 100 * b_hits / n, 100 * h_hits / n
        print(f"{label:<10} {n:>4} {b_pct:>7.1f}% {h_pct:>7.1f}% {h_pct - b_pct:>+6.1f}")


if __name__ == "__main__":
    main()
