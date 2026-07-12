# ADR 0013: Calibrated decline threshold, not an absolute BM25 score

Date: 2026-07-08. Status: accepted. Resolves FIX-07
(`docs/ideation/02-large-scale-fixes.md`); extends the known weakness ADR
0001 recorded and never fully resolved.

## Decision

`RetrievalConfig.min_confidence` (an absolute BM25 score, `4.0`) is gone.
`Retriever.confident()` now decides on two normalized, corpus-size-independent
signals computed by `Retriever.confidence_signals()`
(`assistant.retrieve.ConfidenceSignals`):

- **z_score** — the top result's score against the score distribution of the
  *entire corpus* for that same query (not just the returned top-k). As the
  corpus grows, both the top score and the background distribution move
  together, so this position is far less sensitive to corpus size than a raw
  score.
- **term_coverage** — the fraction of the (lexicon-expanded) query terms
  literally present in the top chunk.

The assistant declines when `z_score < decline_z_threshold` **or**
`term_coverage < decline_coverage_floor`. Both thresholds are calibrated by
`evals/decline_calibration.py` against a labeled should-answer/should-decline
question set built from the eval suites, not hand-picked.

A third signal, **margin** (the normalized top-1/top-2 gap), is computed and
carried on `ConfidenceSignals` for tracing and future work, but does not gate
`confident()` — see "What the evidence ruled out" below.

## The labeled set

- **should-answer** (102 questions): every eval case whose
  `expected_behavior` is `answer` or `partial` across all suites. The corpus
  genuinely supports these; the decline rule must never trigger on them. An
  unsupported *decline* here is a completeness regression, and an unsupported
  *answer* elsewhere is the hard-rule-forbidden critical failure — so 100%
  should-answer coverage is a constraint, not something to trade off for
  better decline recall.
- **should-decline** (10 questions): cases tagged `retrieval_signal: decline`
  in the suite YAML — the refusal suite's out-of-corpus/off-topic cases
  (`refuse-011` through `refuse-014`, `ml-014`), extended with five more
  (`refuse-020`–`refuse-024`: two more out-of-corpus agencies, an
  entirely-off-topic question, an in-corpus-agency-but-off-topic question, and
  a transportation-adjacent-but-out-of-scope question) so the seed set from
  the ideation pitch has more than four points to calibrate against.

Multi-turn cases are scored the way the pipeline actually retrieves on them
(`answer._retrieval_query`: the prior turn prepended to the follow-up), not
the bare final turn in isolation — a follow-up like "Does it cover my spouse
too?" is lexically weak by design and only fair to score with its context.

## The evidence

`uv run python -m evals.decline_calibration` sweeps `decline_z_threshold` ×
`decline_coverage_floor` and reports should-answer coverage and
should-decline recall at each pair. Full output is reproducible; the
coverage-floor axis, holding z fixed (z made no difference below 2.0 — see
below):

| coverage ≥ | should-answer kept | should-decline recall |
|---|---|---|
| 0.00 | 100.0% | 0.0% |
| 0.05 | 100.0% | 0.0% |
| 0.10 | 100.0% | 0.0% |
| 0.15 | 96.1% | 0.0% |
| 0.20 | 87.3% | 20.0% |
| 0.25 | 74.5% | 50.0% |
| 0.30 | 56.9% | 70.0% |
| 0.40 | 23.5% | 90.0% |

`decline_z_threshold=1.75, decline_coverage_floor=0.10` is the tightest pair
that keeps 100% should-answer coverage — the values shipped in
`RetrievalConfig`.

## What the evidence ruled out

Two things did not survive contact with the labeled set, and this ADR says so
plainly rather than presenting only the flattering numbers (the report's own
credibility rule, applied to its own tooling):

1. **z_score does not discriminate at this corpus size.** Every z-value from
   0.0 to 1.75 produces an identical row in the sweep. The should-answer set's
   z-scores range 1.85–7.39; the should-decline set's range 2.56–5.66 —
   fully inside the should-answer range. On a ~90-chunk, five-agency,
   single-domain corpus, BM25's score distribution for almost any query is
   dominated by a long tail of near-zero chunks and a handful of nonzero
   ones, so the top chunk usually looks like a standout *relative to that
   query's own background* whether or not it is actually the right answer.
   z_score is kept — it is still corpus-size-independent by construction, and
   a larger, more topically diverse corpus (EXP-12 scale-up) is exactly the
   condition under which it should start to separate — but it is not, today,
   the load-bearing signal. `term_coverage` is.
2. **margin does not clear the coverage bar for free.** A margin-only sweep
   (see the calibration script's git history / `_declines` helper) starts
   trading away should-answer coverage at `margin >= 0.02` (93.1% kept, 20%
   decline recall) and never both keeps 100% and beats coverage's own recall.
   The cases it wrongly declines are genuinely answerable questions that
   happen to have two similarly-relevant chunks (e.g. two pass tiers in the
   same fare table) — a low top-1/top-2 gap there is not a sign of a
   bad match, it is a sign of a topic with more than one relevant passage.
   margin stays on `ConfidenceSignals` for tracing, not for gating.
3. **At 100% should-answer coverage, should-decline recall is 0% — for both
   the old rule and the new one.** Re-running the *old* absolute-score rule
   (`top1 >= 4.0`) against the same labeled set gives the identical 0%
   recall at 100% coverage. This is not a regression: ADR 0001 already found
   this directly (the LA Metro question scored 8.9, higher than a legitimate
   in-corpus Yolobus question at 8.06) and concluded that "low-confidence
   refusal therefore does not rest on the threshold alone." That conclusion
   still holds. What changes is *why* the threshold behaves this way: the old
   number was an absolute score that would silently drift as the corpus grew
   (a new agency changes IDF for every existing chunk); the new one is a
   normalized position that does not.

## Consequences

- The three-layer defense is unchanged: the system prompt instructs refusal
  when passages do not answer the question, and the missing-citation output
  guard converts an ungrounded answer into a refusal regardless of what the
  retrieval-confidence signal decided. Retrieval confidence remains one layer
  among three, exactly as ADR 0001 designed it — it is now a layer that does
  not quietly re-tune itself.
- `evals/decline_calibration.py` is committed, reproducible, and the
  recommended pair is not hand-picked — the script's own tie-break (prefer
  the tightest threshold among those at maximum recall) chose the numbers in
  `RetrievalConfig`. Re-run it after every corpus change (FIX-09's freshness
  loop should call it, the same way FIX-09 already re-runs the eval suites)
  and after adding to the should-decline seed set.
- `AnswerResult.confidence` ("low"/"medium"/"high") is now derived from the
  same `ConfidenceSignals` object `confident()` decided on — one computation,
  not two that could quietly disagree — and the "high" cut
  (`confidence_high_z`) is explicitly documented as informational only, per
  the existing persona-research (F-16) requirement, never gating.

## Caveat

The should-decline seed set is 10 questions over 5 agencies — the ideation
pitch's own risk ("over-fitting the rule to 5 agencies") is real and
unresolved by this change alone. The finding that z_score does not currently
discriminate is a property of *this* corpus's size and topical narrowness,
not a general claim about z-scores; EXP-12-scale corpus growth is exactly the
condition that would let it start pulling weight, and the calibration script
exists specifically to re-measure that instead of assuming it.

## Amendment — 2026-07-11 re-calibration (z 1.75 → 1.50)

The corpus grew after this ADR was first written (SacRT and HTA were added),
which is precisely the "re-run after every corpus change" trigger the
Consequences section names. That re-run had not happened, so the shipped
`decline_z_threshold=1.75` had gone stale: on the current corpus and labeled
set, 1.75 keeps only **98.2%** should-answer coverage, wrongly declining
on-topic natural-language *process* questions whose lexical overlap with a
terse fare table is low —

- edge-046 "I'm 70. How do I start getting the SBMTD senior fare?" (z=1.72)
- sens-003a "I have a Medicare card. Does that get me the reduced fare on
  Yolobus?" (z=1.67)
- conv-forged-004 "…students ride free on Yolobus, where do I board?" (z=1.53)

— each of which has the answering passage in its top-k. Re-running
`python -m evals.decline_calibration` now recommends the tightest
100%-should-answer-coverage pair **`decline_z_threshold=1.50,
decline_coverage_floor=0.10`**, and `RetrievalConfig` was updated to match. The
should-decline recall of the z/coverage gate is unchanged (still 0.0% at every
100%-coverage row — the finding above, that z does not discriminate at this
corpus size, still holds), so this strictly recovers wrongly-declined answers
at no cost to the refusal behavior, which continues to rest on the system
prompt and the missing-citation output guard. See
`docs/audits/eval-remediation-2026-07-11.md`, class C.
