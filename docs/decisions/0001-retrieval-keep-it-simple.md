# ADR 0001: BM25-first retrieval, dense optional, no reranker

Date: 2026-06-12. Status: accepted.

## Decision

Retrieval is BM25 (rank_bm25) over section-level chunks with an agency filter,
top-6. Dense retrieval (sentence-transformers,
paraphrase-multilingual-MiniLM-L12-v2, local inference, zero marginal cost) is
implemented behind `FPA_DENSE=1` and the `dense` extra, mixed with BM25 at a
fixed weight. No reranker, no agentic retrieval loops.

## Why

The corpus is ~120 chunks. At this size BM25 retrieves the right section for
agency-named questions almost every time, runs offline, and keeps the demo
free. Dense retrieval exists for one concrete reason: Spanish questions
against English-only documents (three of four agencies), where lexical overlap
fails. The multilingual suite measures whether it is needed; it gets enabled
when the eval deltas justify it, and that change will cite the failing cases.

## Known weakness, found by the baseline run

BM25 absolute scores are not calibrated, so the `min_confidence` threshold
cannot reliably distinguish out-of-corpus questions ("senior fare on LA
Metro", top score 8.9) from in-corpus ones (a legitimate Yolobus question,
8.06). Low-confidence refusal therefore does not rest on the threshold alone;
the system prompt instructs refusal when passages do not answer the question,
and the missing-citation output guard converts an ungrounded answer into a
refusal. The refusal suite measures the combined behavior. If live runs show
gaps, the next candidates are dense-score gating or a query-coverage check,
and the eval deltas will decide.

## Live-run findings (2026-06-12) and the dense-retrieval trigger

The first live runs added two ranking findings:

1. Fare-table chunks are number-dense with few word tokens, so BM25 ranks
   them below prose even when they hold the asked-for price (cases
   ground-001, ground-014, ml-002). Mitigations so far: top_k raised 6→8 and
   a 1.2× same-language boost so translated documents don't crowd originals.
2. Spanish questions about English-only agencies retrieve the one bilingual
   page (SBMTD's fare-change notice) instead of the page with the answer,
   because its Spanish prose out-matches a term-translation lexicon (case
   ml-011, partially ml-002).

The Spanish→English lexicon in `retrieve.py` is the cheap mitigation. If
multilingual cases keep failing on ranking after lexicon tuning, the next
step is the already-implemented dense path (`pip install .[dense]`,
`FPA_DENSE=1`, multilingual MiniLM): run the full suite with and without it
and let the deltas on ml-002/ml-011 justify making it the default.
