# 0016 — Deterministic fare-row binding: investigated, not earned

## Status

Rejected as a gating check. The table-misread class stays judge-owned, with the
deterministic layer limited to what it can do precisely. This ADR records the
investigation and the measurement so the decision is not relitigated without new
evidence.

## Context

The scariest eval failures are *wrong fares from a misread table row*: an answer
gives Woodland's BeeLine fare as $2.00 when the table says $3.00 (ground-024),
or calls the SBMTD senior one-way fare FREE when it is $1.25 (conv-forged-002).
The existing `fare_facts_consistent` check (ADR context: `evals/checks.py`)
verifies that a price claimed in an answer exists *somewhere* in the cited doc's
`FareFact` table. It does not verify the price matches the **row** — the
rider-class or place — the answer is talking about. The proposal was to close
that gap deterministically, to "kill the table-misread class."

## What was tried, and what it measured

The `FareFact` table does bind a price to its row (`program="Woodland",
rider_class="Regular", price=3.0`). So the plan was: for each price claimed in
an answer, find the fact whose row label co-occurs near the price, and flag a
mismatch when that row's price differs. Two variants were run against the real
promoted run's 201 cached answers (offline, deterministic — no model calls):

| Variant | Cases flagged | True catches | False positives |
|---|---|---|---|
| Proximity to any row label | 16 | 1 (ground-024) | 15 |
| Restricted to place-fare tables | 26 | 1 (ground-024) | 25 |

Every false positive is a **correct** answer. The cause is intrinsic, not a
tuning bug:

- Answers naturally **enumerate several fares in one breath** ("single ride
  $1.25, transfer $0.25, day pass $3.50"), so any fixed proximity window around
  a price also contains the labels of *neighboring* rows, whose prices
  legitimately differ.
- Label tokens are **noisy as row keys**: the place-restricted variant treated
  "yolobus" (which appears in row labels like "Between Yolobus (Express)"),
  "week", and "month" as place rows, so almost any priced answer mentioning the
  agency tripped it.

Associating a price with its intended row in free prose is the exact
natural-language task an LLM judge does well and a proximity heuristic does
badly. Pushing the heuristic toward precision (more exclusions, tighter windows)
traded one false-positive family for another without approaching a usable rate.

## Decision

Do **not** add a gating fare-row-binding check. A gate with a 15:1 (or worse)
false-positive ratio would fail correct answers — the precise anti-pattern the
2026-07-11 remediation removed when it made `forbidden_content` negation-aware.
The harness's credibility rests on not doing that.

The table-misread class is covered by a division of labor, stated honestly:

- **Price absent from the table entirely** (a fabricated number): caught
  deterministically by `fare_facts_consistent`, and proven caught by the
  defect-injection self-test (`evals/selftest.py`, PR "harness self-test").
- **A real table price attributed to the wrong row** (ground-024,
  conv-forged-002): owned by the **groundedness judge**, which caught both in
  the live runs. This is why the judge is not redundant with the deterministic
  gate — it does the association a heuristic cannot.

## Consequences

- The rider-safety needle for table misreads is **not** moved by a new
  deterministic gate; it stays on the judge plus the price-existence check. Any
  claim that this class is "solved deterministically" would be false, and this
  ADR exists to prevent that claim.
- Revisit only with a materially different method — for example a structured,
  column-aware extraction that emits an answer-side (row, price) assertion the
  answer model must fill from the table, rather than post-hoc association over
  prose. That is a larger change than a check and is out of scope here.
- The measurement script lived in the investigation only; it is not committed as
  a tool, because a 25:1-false-positive advisory is not a useful artifact.
