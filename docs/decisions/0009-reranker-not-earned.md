# ADR 0009: No reranker — retrieval is not the failing eval's bottleneck

Date: 2026-07-08. Status: accepted. Resolves ROADMAP.md P3-4.

## Decision

No reranker is added. ADR 0001's "no reranker" stands, and this ADR replaces
the prose claim in ROADMAP.md P3-4 ("today retrieval is not the bottleneck")
with a measured one, plus the trigger that would reopen the question.

## The evidence

The P3-4 item's own bar for adding a reranker is: evals show *retrieval* —
not generation or judge strictness — is the actual bottleneck. That is
checkable directly. `evals/reranker_bottleneck_check.py` takes every failing
item from the latest independent audit (`docs/audits/eval-report.json`,
`make audit`, real assistant answers scored by the external deterministic-
lexical judge) and, for each one that names required facts, re-runs the
current default BM25 retriever to ask two questions:

1. **Recall** — is a chunk containing the fact anywhere in the retrieved
   top-k?
2. **Rank** — if so, at what position?

Rank matters here because `answer.py` feeds the *entire* retrieved top-k to
the generator (`_format_passages(results)`), not just the top match. A
reranker in this pipeline can only reorder chunks the model already receives
as context — it cannot make the model see a chunk that was outside the
candidate set retrieval produced. So a failing case where the fact was
retrieved (at any rank) is evidence against retrieval as the cause, and only
a genuine recall miss is retrieval's fault.

Run against the 2026-07-08 audit (33 failing items across accuracy,
groundedness, multilingual, refusal; 32 named checkable required facts):

| | count | share of checkable |
|---|---|---|
| checkable failing cases | 32 | — |
| recall hit (fact somewhere in top-k) | 31 | 96.9% |
| — of which already at rank 1 | 25 | 78.1% |
| — of which retrieved but buried | 6 | 18.8% |
| recall miss (fact not in top-k at all) | 1 | 3.1% |

96.9% of failing cases already had the answer-bearing passage in the context
the model saw, most of it (78.1%) already first in line. Those failures
happened after retrieval: in generation (the model had the fact and still
produced an unsupported or contradicted claim) or in judging (the
deterministic-lexical judge's negation-mismatch and claim-entailment checks,
which the `groundedness` suite's 0.04 score — far below `accuracy`'s 0.92 on
largely the same corpus — points at directly). That matches ADR 0007's
recall measurement (~98% BM25 recall on the full suite) and confirms it holds
specifically on the cases that are failing today, not just on average.

A reranker cannot fix either failure mode. It reorders a candidate set the
generator already receives in full; it cannot correct a claim the model
generated wrong, and it cannot loosen a judge's negation check.

## Consequences

- ROADMAP.md P3-4 is updated to cite this ADR and the script instead of an
  unmeasured claim.
- `evals/reranker_bottleneck_check.py` is the reusable check: re-run it
  against any future audit report before reconsidering a reranker.
- **Trigger to revisit:** if a future audit's recall-miss share rises
  materially above single digits — i.e., failing cases where the required
  fact is genuinely absent from the retrieved top-k — that is retrieval
  actually being the bottleneck, and this ADR should be superseded with the
  new deltas. Until then, effort belongs on generation grounding and judge
  calibration (P0/P1), not on reordering passages the model already has.

## Caveat

This measures recall/rank, not answer quality directly, and required_facts
are literal-or-regex substrings, not semantic checks — a chunk can contain
the string and still be the wrong passage in context, or the true
answer-bearing chunk could in principle be misidentified by the substring
match on some case. The check is deliberately the same cheap, model-free
method ADR 0007 used, chosen so this decision rests on the same kind of
evidence as the dense-retrieval one, not a fresh methodology per ADR.

## Amendment — 2026-07-11 re-confirmation (fresh evidence)

The 2026-07-11 remediation added a real retrieval case worth re-checking the
decision against, and a fresh run to re-measure it on.

**A genuine recall miss appeared — and a reranker was still the wrong fix.**
`sens-010a` ("does my 3-year-old ride free?") failed because the passage
carrying "Children under 45 inches tall" ranked #37, outside the top-k. That is
exactly a recall miss: the fact-bearing chunk was never in the candidate set. It
was fixed by *improving recall* — a child/youth free-fare "close the loop"
companion that pulls the provision passage in (`src/assistant/retrieve.py`) —
not by reordering, because reordering cannot recover a chunk retrieval never
returned. The one case that looked like a retrieval problem confirmed the ADR's
central point rather than undermining it.

**On the promoted 192/201 run, retrieval is the bottleneck for none of the
remaining failures.** Re-running the recall/rank check over that run's own
failing cases (not the older audit): of the 7 checkable failures, **7/7 had the
required fact retrieved in the top-k**, most at rank 0 — the first chunk the
model saw. `ground-024` is the sharpest example: the model received the $3.00
Woodland chunk at rank 0 and still answered $2.00. That is a generation error
the model made while looking straight at the right passage; a reranker changes
nothing.

Decision unchanged: **no reranker.** The reopening trigger from the original ADR
stands — a run where a material share of failures are recall misses of a chunk
that a reranker could have surfaced from *within* the candidate set. This run is
the opposite of that.
