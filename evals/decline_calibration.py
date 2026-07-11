"""Decline-threshold calibration (FIX-07 / ADR 0013).

The old `RetrievalConfig.min_confidence` was an absolute BM25 score, and BM25
scores are not calibrated against anything — they drift every time the corpus
grows (a new agency changes IDF for every existing chunk), so the constant
silently re-tuned itself with each corpus change. `assistant.retrieve
.Retriever.confident()` now decides on two normalized, corpus-size-independent
signals instead (`assistant.retrieve.ConfidenceSignals`):

  - z_score: the top result's score against the full-corpus score
    distribution for the same query.
  - term_coverage: the fraction of (lexicon-expanded) query terms literally
    present in the top chunk.

This script calibrates `decline_z_threshold` and `decline_coverage_floor`
against a labeled should-answer/should-decline question set built from the
eval suites:

  - should-answer: every case whose `expected_behavior` is "answer" or
    "partial" — the corpus genuinely supports these, so the decline rule must
    never trigger on them (an unsupported *decline* here is a completeness
    regression; an unsupported *answer* elsewhere is the critical failure the
    hard rules forbid, so answer-set coverage is a hard constraint, not a
    thing to trade off).
  - should-decline: cases tagged `retrieval_signal: decline` in the suite
    YAML — the refusal suite's out-of-corpus/off-topic cases (the ideation's
    seed set, extended with a few more agencies and off-topic questions).

    uv run python -m evals.decline_calibration

Prints a sweep table (like ADR 0007's ablation) and the threshold pair that
maximizes should-decline recall subject to 100% should-answer coverage. Only
BM25 signals are exercised (no model calls, no dense retrieval) — this is
retrieval-only, same spirit as retrieval_ablation.py. Re-run after every
corpus change (wired into the FIX-09 freshness loop) since the labeled set
and the corpus can drift apart.
"""

from __future__ import annotations

from assistant.config import RetrievalConfig
from assistant.ingest import load_chunks
from assistant.retrieve import Retriever
from evals.runner import load_suites

Z_GRID = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
COVERAGE_GRID = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4]


def _question(case: dict) -> str | None:
    """The text actually handed to `Retriever.search()` for this case,
    matching `answer._retrieval_query()`: a multi-turn case's last turn is
    prepended with the turn before it, since that is what the pipeline
    retrieves on (a bare follow-up like "does it cover my spouse too?" is
    lexically weak on its own by design; only the history-carrying query is a
    fair signal)."""
    if case.get("question"):
        return case["question"]
    turns = case.get("turns")
    if not turns:
        return None
    if len(turns) == 1:
        return turns[0]
    return f"{turns[-2]} {turns[-1]}"


def labeled_cases() -> tuple[list[str], list[str]]:
    """(should_answer questions, should_decline questions) from the suites."""
    should_answer: list[str] = []
    should_decline: list[str] = []
    for suite in load_suites():
        for case in suite["cases"]:
            q = _question(case)
            if not q:
                continue
            if case.get("retrieval_signal") == "decline":
                should_decline.append(q)
            elif case.get("expected_behavior") in ("answer", "partial"):
                should_answer.append(q)
    return should_answer, should_decline


def _declines(retriever: Retriever, question: str, z: float, coverage: float) -> bool:
    results = retriever.search(question)
    if not results:
        return True
    sig = retriever.confidence_signals(question, results)
    return sig.z_score < z or sig.term_coverage < coverage


def sweep(
    retriever: Retriever, should_answer: list[str], should_decline: list[str]
) -> list[tuple[float, float, float, float]]:
    """Rows of (z, coverage, answer_set_coverage, decline_recall)."""
    rows = []
    for z in Z_GRID:
        for coverage in COVERAGE_GRID:
            wrongly_declined = sum(_declines(retriever, q, z, coverage) for q in should_answer)
            answer_coverage = 1 - wrongly_declined / len(should_answer) if should_answer else 1.0
            correctly_declined = sum(_declines(retriever, q, z, coverage) for q in should_decline)
            decline_recall = correctly_declined / len(should_decline) if should_decline else 0.0
            rows.append((z, coverage, answer_coverage, decline_recall))
    return rows


def main() -> None:
    chunks = load_chunks()
    retriever = Retriever(chunks, RetrievalConfig(use_dense=False))
    should_answer, should_decline = labeled_cases()
    print(f"should-answer set: {len(should_answer)}   should-decline set: {len(should_decline)}\n")
    print(f"{'z>=':>6} {'coverage>=':>11} {'answer kept':>12} {'decline recall':>15}")
    rows = sweep(retriever, should_answer, should_decline)
    for z, coverage, answer_coverage, decline_recall in rows:
        print(
            f"{z:>6.2f} {coverage:>11.2f} {answer_coverage * 100:>11.1f}% "
            f"{decline_recall * 100:>14.1f}%"
        )

    # Full should-answer coverage is a hard constraint (an unsupported decline
    # on a question the corpus can genuinely answer is a completeness
    # regression the harness should never introduce); among the thresholds
    # that clear it, pick the one with the best decline recall, and the
    # tightest (highest) thresholds on ties, since a tighter rule declines
    # more confidently as the corpus grows.
    candidates = [r for r in rows if r[2] == 1.0]
    if not candidates:
        print("\nno threshold pair keeps 100% should-answer coverage; widen the grid")
        return
    best_recall = max(r[3] for r in candidates)
    best = max((r for r in candidates if r[3] == best_recall), key=lambda r: (r[0], r[1]))
    z, coverage, answer_coverage, decline_recall = best
    print(
        f"\nrecommended: decline_z_threshold={z}, decline_coverage_floor={coverage} "
        f"({answer_coverage * 100:.0f}% should-answer coverage, "
        f"{decline_recall * 100:.1f}% should-decline recall)"
    )


if __name__ == "__main__":
    main()
