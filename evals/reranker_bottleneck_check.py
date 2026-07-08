"""Reranker bottleneck check: would reordering retrieval even help?

ROADMAP.md P3-4 gates a reranker on evidence: add one only if evals show
retrieval — not generation or judge strictness — is the actual bottleneck.
This makes that judgment call measurable instead of impressionistic.

The generator is fed every chunk in a case's retrieved top-k as context
(`_format_passages(results)` in `answer.py`); a reranker's only effect in
this pipeline is to change the *order* of chunks the model already sees, not
which chunks it sees. So the question a reranker could possibly answer is:
among cases whose eval failed, was the fact-bearing chunk simply *missing*
from the retrieved top-k (a recall problem — a reranker over the same
candidate set cannot fix that), or was it present-but-buried (an ordering
problem a reranker could plausibly fix)?

This script takes the failing cases from the latest independent audit
(`docs/audits/eval-report.json`, produced by `make audit`) and, for each one
that names required_facts, re-runs the *current* default BM25 retriever to
check:

  - recall: is a chunk containing the fact anywhere in the retrieved top-k?
  - rank:   if so, at what position (1 = first chunk the model sees)?

If most failing cases already have the answer-bearing chunk retrieved (recall
hit), the failure happened downstream of retrieval — reordering would not
have changed what the model saw. Only recall misses are retrieval's fault,
and reranking cannot fix a recall miss (it operates on the same candidate
set retrieval already produced).

    uv run python -m evals.reranker_bottleneck_check [path/to/eval-report.json]

Exits 0 always; this is an analysis tool, not a gate. See ADR 0009 for the
conclusion drawn from its output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from assistant import config
from assistant.ingest import load_chunks
from assistant.retrieve import Retriever
from evals.retrieval_ablation import _fact_in_chunks
from evals.runner import load_suites

DEFAULT_REPORT = config.REPO_ROOT / "docs" / "audits" / "eval-report.json"


def _load_failing_ids(report_path: Path) -> dict[str, list[dict]]:
    """suite name -> list of {item_id, detail} for that suite's failing_examples."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for suite in data["suite_results"]:
        failing = suite.get("failing_examples") or []
        if failing:
            out[suite["suite_name"]] = failing
    return out


def _rank_of_fact(fact: str, retrieved_texts: list[str]) -> int | None:
    """1-indexed position of the first chunk containing `fact`, or None if absent."""
    for i, text in enumerate(retrieved_texts, start=1):
        if _fact_in_chunks(fact, [text]):
            return i
    return None


def main(argv: list[str] | None = None) -> None:
    report_path = Path(argv[0]) if argv else DEFAULT_REPORT
    if not report_path.exists():
        raise SystemExit(
            f"{report_path} not found. Run `make audit` (needs EVAL_HARNESS) to produce it, "
            "or pass an explicit path to a previously generated eval-report.json."
        )

    failing_by_suite = _load_failing_ids(report_path)
    cases_by_id = {c["id"]: c for s in load_suites() for c in s["cases"]}

    retriever = Retriever(load_chunks(), config.RetrievalConfig(use_dense=False))

    print(f"Source report: {report_path}")
    print(
        f"{'suite':<14} {'n_failing':>9} {'checkable':>9} {'recall_hit':>10} "
        f"{'rank1':>6} {'buried':>7} {'missing':>8}"
    )

    total_checkable = total_recall = total_rank1 = total_buried = total_missing = 0

    for suite_name, failing in sorted(failing_by_suite.items()):
        checkable = recall_hit = rank1 = buried = missing = 0
        for item in failing:
            case = cases_by_id.get(item["item_id"])
            if not case or not case.get("required_facts"):
                continue
            checkable += 1
            q = case.get("question") or case["turns"][-1]
            retrieved_texts = [sc.chunk.text for sc in retriever.search(q)]
            ranks = [_rank_of_fact(f, retrieved_texts) for f in case["required_facts"]]
            if all(r is not None for r in ranks):
                recall_hit += 1
                if all(r == 1 for r in ranks):
                    rank1 += 1
                else:
                    buried += 1
            else:
                missing += 1
        if checkable:
            print(
                f"{suite_name:<14} {len(failing):>9} {checkable:>9} {recall_hit:>10} "
                f"{rank1:>6} {buried:>7} {missing:>8}"
            )
        total_checkable += checkable
        total_recall += recall_hit
        total_rank1 += rank1
        total_buried += buried
        total_missing += missing

    print("-" * 70)
    print(
        f"{'TOTAL':<14} {'':>9} {total_checkable:>9} {total_recall:>10} "
        f"{total_rank1:>6} {total_buried:>7} {total_missing:>8}"
    )
    if total_checkable:
        recall_pct = 100 * total_recall / total_checkable
        rank1_pct = 100 * total_rank1 / total_checkable
        print(
            f"\n{recall_pct:.1f}% of checkable failing cases had every required fact "
            f"retrieved in the current top-k (recall hit); of those, {rank1_pct:.1f}% of "
            "all checkable cases already had it at rank 1. The generator receives every "
            "retrieved chunk as context regardless of order, so a rank-1 or buried-but-"
            "retrieved hit means the model already saw the fact and still failed — a "
            "reranker changes nothing there. Only the "
            f"{100 * total_missing / total_checkable:.1f}% recall-miss share is retrieval's "
            "to own, and a reranker cannot recover a chunk that was never in the candidate "
            "set it reorders."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
