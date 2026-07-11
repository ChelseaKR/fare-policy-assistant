# ADR 0011: GTFS(-Fares) cross-validation channel

Date: 2026-07-08. Status: accepted. Implements EXP-06 in
`docs/ideation/03-expansions.md`.

## Decision

`src/assistant/gtfs.py` ingests each agency's machine-readable GTFS static
feed (where one is confirmed to exist, see Survey below) as a second,
structured evidence source, and cross-checks the fare amounts it publishes
against the prose corpus for the same agency. Disagreements are written to
`corpus/processed/gtfs_cross_check.json` as `feed_agrees: yes | no | no_feed`
per fare row. `make gtfs-fetch` snapshots the fare-relevant files from each
configured feed into `corpus/raw/gtfs/<agency>/`; `make gtfs-check` runs the
comparison.

The feed is a **tripwire, never a source of truth**. The published prose
remains the citable policy — this channel never overrides an answer, never
substitutes the feed price, and is not wired into the answer pipeline at all.
It produces a report for a human (or, per the EXP-06 shape note, the FIX-09
freshness-PR body) to look at.

## Why

California (Cal-ITP) actively standardizes GTFS publication, and the corpus
already overlaps the two live Cal-ITP Benefits agencies (MST, SBMTD). Prose
pages and feeds drift at different speeds; a detected disagreement ("web page
says $2.50, feed says $3.00") is precisely the wrong-fare liability scenario
this project's safety case cares about (see EXP-06's rationale in the
ideation doc), caught mechanically instead of by luck.

## Survey (live-fetched 2026-07-08)

The EXP-06 ideation entry flagged as an open question "which of the five
agencies publish usable Fares data was not verified in this pass." This pass
checked, against the real agencies:

| Agency  | Feed found | Fares schema | Notes |
|---------|-----------|--------------|-------|
| MST     | yes | GTFS-Fares v1 (`fare_attributes.txt` + `fare_rules.txt`) | `https://www.mst.org/google/google_transit.zip` |
| SBMTD   | yes | GTFS-Fares v2 (`fare_products.txt` + `fare_leg_rules.txt` + `rider_categories.txt`) | `https://www.sbmtd.gov/google_transit/feed.zip` |
| Yolobus | not found | — | no GTFS zip resolved at any conventionally-guessed URL |
| SacRT   | not found | — | `sacrt.com/gtfs/google_transit.zip` redirects to an HTML page, not a feed |
| HTA     | not found | — | not checked further once the pattern above didn't hold |

Only MST and SBMTD are in `corpus/manifest.yaml`'s `gtfs_feeds:` list as a
result. This is deliberate, not an oversight: pointing an unconfirmed agency
at a guessed URL risks silently fetching nothing, a 404, or (worse) another
agency's feed under a similar path. The design tolerates this — an agency
with no configured feed gets a `no_feed` record from `cross_check`, not a
missing or crashing check. Finding a real feed for Yolobus, SacRT, or HTA
(most likely via a GTFS aggregator that needs an API key this pass didn't
have — Transitland, Mobility Database) is a follow-up, not a blocker.

Both schemas needed to be handled because they were both found live in the
wild on the very first two agencies checked: GTFS-Fares v1 ("classic",
`fare_attributes.txt`) is what MST still publishes; v2 (`fare_products.txt`)
is what SBMTD has migrated to. `gtfs.py` parses both.

## What the first real run found

Running `make gtfs-fetch && make gtfs-check` against the live MST and SBMTD
feeds and the committed corpus on 2026-07-08 found:

- MST's `$2.00` regular fare and SBMTD's `$2.50` standard / `$1.25` reduced
  fares all agree with the prose (both `Regular Fixed Route` and `Standard
  One-way Cash Fare` cost exactly what the feed says).
- **A real, live disagreement, on the first run:** SBMTD's feed publishes
  fares for the Downtown-Waterfront Shuttle (`$0.50` standard / `$0.25`
  reduced) that do not appear anywhere in the corpus's prose pages —
  `sbmtd-fares-passes.md` mentions the shuttle exists (to note UCSB/SBCC
  cards and MTD transfers aren't valid on it) but never states its fare. This
  is a genuine coverage gap the mechanism caught mechanically: an assistant
  question about the shuttle's fare today would have nothing to cite, and the
  cross-check surfaced that without a human noticing it first. It's a
  coverage gap rather than a stale-price drift, but it's the same failure
  mode EXP-06 targets (a program whose real-world fare the assistant cannot
  ground) and it is not fabricated — it's what this pass's live run actually
  found. Filed as a corpus-completeness gap, not fixed here (out of scope for
  this PR; see docs/ROADMAP.md).

## Known false-positive class (handled)

A `$0.00` feed fare (a free program, a child fare) is essentially never
spelled `$0.00` in agency prose — it's spelled "free" or "no charge". Without
a guard, every free fare in every feed would flag as a disagreement on every
agency, which is exactly the noisy-alarm pattern that gets a feature like
this disabled rather than trusted. `cross_check` treats a zero-amount feed
fare as agreeing if the agency's prose corpus mentions "free" anywhere. This
is coarser than a per-program match (see Limitations) but eliminates the
single largest source of false alarms observed in the first real run (SBMTD
alone had five zero/near-zero fares that all cleared once this guard was
added; MST's `Free` fare attribute is genuinely free everywhere in its
prose).

## Limitations

- **No per-program matching (depends on EXP-01).** EXP-06's ideation entry
  describes cross-checking feed fares against EXP-01's typed
  `facts.jsonl` fact rows, scoped per agency/program/rider-class. EXP-01 (the
  structured fare-fact layer) is not implemented in this codebase yet,  so
  `cross_check` compares against every dollar amount mentioned anywhere in an
  agency's prose corpus instead. A feed amount that matches *some* prose
  figure for that agency reads as agreeing even if it's actually the wrong
  program's price coincidentally matching (e.g., a monthly pass price that
  happens to equal a different program's single-ride price). This is real
  but rare in a 5-agency corpus; tightening it is the natural next step once
  EXP-01 lands, and `cross_check`'s docstring flags the swap point.
- **Coverage is 2 of 5 agencies.** See Survey above.
- **Never gates or auto-fixes anything.** By design — see Decision. A
  disagreement is a report line for a human to look at, not an automated
  correction.

## Consequences

- `corpus/manifest.yaml` gains a `gtfs_feeds:` list, additive and optional;
  an agency absent from it is unaffected by this feature.
- `corpus/raw/gtfs/<agency>/` snapshots only fare-relevant GTFS members
  (`fare_attributes.txt`, `fare_products.txt`, etc., plus `agency.txt`), not
  the full feed — a GTFS zip also carries multi-megabyte `stop_times.txt` /
  `shapes.txt` this project has no use for, and committing only the fare
  slice keeps snapshots small and diffable, matching `corpus/raw`'s existing
  "committed snapshot" convention.
- Nothing in `answer.py`, `retrieve.py`, or the eval suites changes. This is
  an additive, offline check.
