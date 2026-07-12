# 0017 — GTFS-Fares as the source of truth for fare numbers

Date: 2026-07-12. Status: accepted (increment 1 landed). Follows ADR 0016.

## Context

ADR 0016 established, with data, that the worst failure class — a real fare read
from the wrong row of a prose table (ground-024's $2-vs-$3 Woodland fare,
conv-forged-002's "senior rides FREE" when it is $1.25) — cannot be caught by a
prose heuristic without a 15:1+ false-positive rate, and is therefore owned by
the LLM judge. That is a detection stance. This ADR is the *prevention* stance:
change where the numbers come from so the misread mostly cannot happen.

Every pilot agency already publishes a machine-readable GTFS-Fares feed, and the
repo already snapshots and cross-checks it (ADR 0011, `corpus/raw/gtfs/`,
`assistant.gtfs`). In that feed a fare *amount* is bound to a typed rider
category — SBMTD publishes `standard` $2.50, `reduced` $1.25 (Seniors/Disabled/
Medicare), `free` $0.00 (children under 45 inches) — not a table cell a model
must parse. That is exactly the structure whose absence makes prose misreads
possible.

## Decision

Treat the GTFS-Fares feed as the **source of truth for fare numbers**, and have
the model compose answers from typed facts rather than read amounts out of
prose. Roll it out in increments so each is separately verifiable:

1. **(this ADR, landed)** `assistant.fare_table` — a typed `StructuredFare`
   view over the feed (amount bound to a resolved `RiderCategory`), a rider-
   category lookup, and `render_fare_card(agency)`, the authoritative fare block
   a later increment injects into the answer prompt. Falls back to empty for an
   agency with no feed, so nothing regresses where there is no structured
   source. `make fares AGENCY=SBMTD`.
2. **(next)** Inject `render_fare_card` into the answer prompt as authoritative,
   labeled fares for agencies with a feed, so the model states a number it did
   not have to parse. Validate on a live run: the table-misread cases
   (ground-024, conv-forged-002) should improve, and nothing else should
   regress. Gate on the regression threshold as usual.
3. **(next)** A structured consistency check: because the answer is composed
   from labeled fares, a price bound to a named category can be verified against
   the feed deterministically — the row-binding ADR 0016 could not do from
   prose, now tractable because the source is typed. This targets the residual
   the judge owns today.

## Consequences

- Coverage is partial and honest: v2 feeds (SBMTD) carry rider categories; v1
  feeds (MST) carry amounts without categories; some agencies have no feed at
  all. The fare card is empty where there is no feed, and the assistant keeps
  its cited-prose behavior there. Every injected number will be attributable to
  the feed, dated like the rest of the corpus.
- This is the architectural answer to the question ADR 0016 left open, and the
  first piece of the "structured fare computation" expansion — the same typed
  store is what a future trip-aware fare answer ("A to B as a senior") would
  query.
