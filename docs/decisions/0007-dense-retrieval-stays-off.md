# ADR 0007: Dense retrieval stays off — the evidence refutes it

Date: 2026-06-16. Status: accepted. Resolves the open question in ADR 0001.

## Decision

Retrieval stays BM25-only by default. The dense hybrid path (`FPA_DENSE=1`,
sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2`, mixed with BM25
at a fixed 0.5 weight) remains implemented behind its flag, but off, because the
evidence shows it lowers retrieval quality — most on the Spanish questions it
was built to help.

## The evidence

ADR 0001 added dense retrieval on a hypothesis: Spanish questions over
English-only documents (three of four agencies) might need dense embeddings to
bridge the language gap, and the multilingual suite would decide. The honest
test is retrieval recall of the answer-bearing passage, with no model in the
loop: for every eval case that names a required fact, does the retrieved top-k
contain a chunk that states the fact? `evals/retrieval_ablation.py` measures
this for both modes.

| segment | n | BM25 | hybrid | delta |
|---|---|---|---|---|
| all | 84 | 97.6% | 94.0% | −3.6 |
| english | 67 | 97.0% | 95.5% | −1.5 |
| spanish | 17 | 100.0% | 88.2% | −11.8 |

Hybrid is worse everywhere, and the Spanish drop is large — the opposite of the
hypothesis. The reason is that BM25 here is not naive: `retrieve.py` expands a
Spanish query into its English fare vocabulary (the `_ES_EN_LEXICON`), so the
strong lexical signal already lands the right passage. Mixing in the
multilingual dense model at a fixed weight pulls semantically-near-but-wrong
chunks up the ranking and dilutes those exact matches.

## Consequences

- Default config keeps `use_dense=False`. No reranker either (ADR 0001 stands):
  retrieval is not the bottleneck — recall is ~98% and the open eval failures
  are generation and judge strictness, not missing passages.
- The dense code and the `dense` extra stay, gated by the flag, so the decision
  is reproducible (`uv run python -m evals.retrieval_ablation`) and a future
  corpus or a tuned weight can be re-tested. It is documented dead-by-default,
  not silently dormant.
- This is the harness doing its job a second time: as with the reverted prompt
  v5 in P0, the evidence killed a plausible-sounding change before it shipped.

## Caveat

The comparison tests the feature as built — this model, top-k 8, a 0.5 mix.
A different embedding model or weight could land differently, and on a larger or
less lexically-clean corpus dense would likely help. The decision is "off for
this corpus, on this evidence," not "dense retrieval is useless."
