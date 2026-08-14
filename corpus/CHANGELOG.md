# Corpus changelog

Each entry is a corpus version (the deterministic id from
`assistant.corpus.corpus_version`) and what changed since the previous snapshot.
A deployment can approve a version here and pin to it with
`FPA_PINNED_CORPUS_VERSION`; the `/version` endpoint reports whether the running
deploy matches.

Schema-2 source snapshots are tracked separately under `corpus/snapshots/` by
their full `snapshot_version`. They retain exact raw bytes and receipts and
distinguish semantic `content_version` from later source reverification. The
12-character entries below remain the compatibility history for deployed pins;
they are not retroactively relabeled as source-complete evidence.

This history starts with the current snapshot. There is only one snapshot so far,
so there is nothing to diff against yet. The weekly corpus-freshness automation
(`.github/workflows/corpus-freshness.yml`) is the intended writer of future
entries: on a re-fetch that changes any document, it can call
`assistant.corpus.diff_corpus` against the prior snapshot and append the added,
removed, and changed documents below.

## 0938fff0539a (2026-06-17)

Initial recorded version. Five agencies, 11 documents, 90 chunks: Monterey-Salinas
Transit (MST), Santa Barbara MTD (SBMTD), Yolobus, Sacramento Regional Transit
(SacRT), and Humboldt Transit Authority (HTA). Fetch dates per document are in
`corpus/manifest.yaml` and the per-snapshot `corpus/raw/<id>.meta.yaml` files.

## a68e77ff4673 (2026-08-12)

Sixth agency: Solano County Transit (SolTrans), Vallejo/Benicia plus the
SolanoExpress lines. Four documents, 20 chunks (110 total). Added because it is
the corpus's first Clipper participant — until now every agency's fare media was
agency-local, so the assistant had no grounding at all for "does my Clipper card
work here".

Added: `soltrans-fare-table`, `soltrans-clipper-card`, `soltrans-ways-to-pay`,
`soltrans-paperless-fares`. Fetched 2026-08-12 (receipts stamp 2026-08-13 UTC).

Two corpus-hygiene findings recorded with the addition:

- `soltrans-fare-table` is stamped "Effective Sunday, December 5, 2021" with no
  end date — current by the agency's own labeling, not lapsed — but it predates
  SolTrans' 2024-07-01 elimination of paper passes and still tells riders to use
  one. `soltrans-paperless-fares` is ingested as the dated correction, the same
  role `sbmtd-farechange` plays for SBMTD, and eval case fresh-016 pins the
  behavior.
- `soltrans.org/fares/ticket-office-location` was excluded: it is a stale
  near-duplicate of the Clipper Card page stating the Clipper START discount as
  20 percent where the live page and two SolTrans announcements say 50 percent.

## 95794539d1d0 (2026-08-13 UTC)

Added Fresno Area Express (FAX), operated by the City of Fresno: 9 agencies, 23
documents, 152 chunks. Three documents, all fetched 2026-08-13 UTC:
`fax-fares` (the Fares & Passes page), `fax-reduced-fare-program`, and
`fax-reduced-fare-program-es` — the last two the corpus's first PDFs, and the
second a genuine Spanish translation rather than an untranslated shell.

FAX is the first Central Valley agency here and the first whose reduced fare is
a suspension rather than a discount: qualifying riders pay $0.00 on fixed route
"while subsidy funding is available", with no published end date. The base fare
is $1.00 and paratransit stays at $1.25 for the same rider, so this corpus
version is also the first where "what is the fare" has no single right answer.

This id was recomputed after merging the Yolobus 2026-2027 refresh and the Elk
Grove (e-tran), Santa Cruz METRO (SCMTD), and SolTrans additions, so it covers
all five changes: FAX's twelve chunks are the only ones it adds on top of the
140 those four left behind. Any further corpus change landing before this does
needs another `make ingest` and another re-stamp of this heading.

## 0a8bbe659de3 (2026-08-14 UTC)

Added Marin Transit (Marin County Transit District): 10 agencies, 26
documents, 174 chunks. Three documents, all fetched 2026-08-14 UTC:
`marin-fares`, `marin-clipper` (the /clipper URL, which 301s to
/future-fares-clipper), and `marin-31day-transition` (the dated paper-pass
retirement page, stamped 3/5/2026).

The structures that earn the disk space: frequent-rider maximums ("never
pay more than $5 per day or $40 per month", Clipper only) whose scope
differs from the same-priced 31-day pass — only Marin Transit trips count
toward the maximum, while the pass also covers Golden Gate Transit locally
in Marin — plus a two-tier transfer rule that changes with the payment
method, and a PCA/attendant discount riders over-read as free.

Two corpus-hygiene findings recorded with the addition:
- A day pass with no price: the live fares page still says day passes "can
  be purchased through the farebox on the bus" and no fetched page
  publishes any day-pass product or price. refuse-marin-001 pins the honest
  partial answer.
- Paper 31-day passes retired on schedule (not sold after 2026-03-31,
  honored "through early summer 2026") with the transition page still
  framing the wind-down in the future tense; fresh-marin-001 pins the
  cutoff-not-purchase-pointer behavior, the fresh-016 pattern.
- Excluded: /youth-pass, whose unique bulk is a participating-schools table
  naming individual school coordinators with phone numbers and emails —
  retrieval pollution (the FAX Handy Ride lesson) and personal contact
  details a fare corpus has no need to republish.

This id was computed on a branch based on the nine-agency corpus
(95794539d1d0). Three sibling agency branches were in flight at the same
time; whichever merges after this one re-runs `make ingest` and re-stamps
its own heading, as the 2026-08-12/13 additions did.
