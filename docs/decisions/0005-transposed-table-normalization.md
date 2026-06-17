# ADR 0005: Normalize transposed tables at ingest

Date: 2026-06-16. Status: accepted.

## Decision

At ingest, when two adjacent pipe-delimited rows are a *transposed* table — a
row of labels over an equal-width row of their values, aligned only by column
index — append explicit `label: value` lines to the section body. The original
rows are kept unchanged. The transform fires only on digit-free, equal-width
(≥3 column) row pairs, so fare tables (which carry figures, and whose header
and data rows differ in width) are untouched. Implemented as
`normalize_tables` in `src/assistant/ingest.py`.

## Why

The eval harness flagged `edge-025` ("Can I ride Yolobus with my UC Davis Aggie
Card?"). The source "Other Fare Media" table is stored transposed:

```
UC Davis Aggie Card | UC Davis Zip Pass | UC Davis Extension International Program ID | ...
Undergraduate or UCDE Global Study Only | with valid student ID | with valid expiration date | ...
```

The pass and its condition are linked only by sharing a column index. The
answer model mis-aligned them — it kept the Aggie Card's real condition but
also appended "valid expiration date" from a different column, which the
groundedness judge correctly caught as unsupported. The retrieved chunk
contained the right information; its *shape* was the failure. This is the
"fix retrieval/chunking when the evals show it is the bottleneck, and justify
it with eval deltas in an ADR" path from CLAUDE.md.

## Scope and blast radius

Deliberately additive and narrow. Across the whole corpus the transform fires
on exactly two chunks — the `Other Fare Media` table in `yolobus-fares` and
`yolobus-purchasing` — and appends the column-aligned pairs (e.g.
`UC Davis Aggie Card: Undergraduate or UCDE Global Study Only`). Because the
original rows are preserved, BM25 tokens are a superset of before, so retrieval
scores on every other case are unchanged. Unit tests pin both the intended
behavior and the two non-firing cases (adjacent fare data rows; a header +
data row of unequal width).

## Eval delta

Full live run, before → after: **96/103 → 97/103**. `edge_cases` 26/28 → 27/28;
every other suite unchanged; no case that passed before regressed (the
regression gate is clean). `edge-025` moved from fail (groundedness judge:
"valid expiration date … unsupported") to pass. The other six failures are
unrelated to table shape — `ground-024` is a model misread of a *well-formed*
table (BeeLine Woodland $3.00 stated as $2.00), `edge-002`/`ml-004` are
groundedness-judge strictness, `fresh-001` is a guard over-block, `refuse-018`
is a missing "as of" line on a partial answer — so they are left documented in
EVALS.md rather than papered over.

## Rejected alternatives

- **Replace the pipe rows instead of appending.** Would drop tokens and shift
  BM25 scores corpus-wide for a two-chunk benefit. Not worth the blast radius.
- **A general table-to-prose rewriter.** More power, far more risk: it would
  touch every fare table and could regress the many groundedness cases that
  pass today. The narrow transposed-only rule fixes the observed failure
  without that exposure.
- **A prompt instruction to "align table columns carefully."** Vibes-driven and
  unreliable; the defect is in the data shape, so the fix belongs at ingest.
