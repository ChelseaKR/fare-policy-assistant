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

## 60033300dc49 (2026-08-14 UTC)

Added County Connection (CCCTA, Central Contra Costa Transit Authority): 10
agencies, 28 documents, 177 chunks. Five documents, all fetched 2026-08-14 UTC:
`cccta-fare-types-prices`, `cccta-clipper-card`, `cccta-transfers`,
`cccta-rtc-discount-card`, and `cccta-link-fares` (LINK is the paratransit
service). The agency's own "Fares & Passes" landing page was excluded: its
entire rendered body is "Coming soon" and has been since its 2018-05-22
modification stamp.

County Connection is the corpus's second Clipper agency, which is the reason it
was chosen: SolTrans' Clipper page asserts a transfer arrangement with "County
Connection" that the manifest could previously only fence off as a third-party
claim. County Connection's own Transfers and Clipper pages corroborate the
arrangement (free transfers from SolTrans, Clipper only, at shared stops) while
publishing a different window scope — 2 hours for its own bus-to-bus transfer,
no published window for inter-operator transfers — against SolTrans' "good for
60 minutes".

One ingest hazard is recorded in the manifest rather than fixed silently: the
pipeline's per-section line dedupe drops fare rows that price identically, so
the cash table's Youth row ($2.50, identical to Adult) is missing from
`cccta-fare-types-prices#0` and the Youth heading sits directly above the
Senior/Disabled $1.25 row. The page's prose rule ("The youth fare discount is
not available when paying with cash") survives ingest and eval case edge-069
pins it.

Written on a branch that adds only CCCTA. If another corpus change merges
first, this id needs another `make ingest` and a re-stamp of this heading, the
way 95794539d1d0's was.
## d113659adda8 (2026-08-14 UTC)

Added San Joaquin RTD (San Joaquin Regional Transit District): 11 agencies, 30
documents, 188 chunks. Two documents, both fetched 2026-08-14 UTC (checked
2026-08-13 Pacific): `sjrtd-fares` (the Fares page: fixed route, Commuter,
Paratransit, and Van Go! tables plus where-to-buy) and `sjrtd-dfc` (the
Discount Fare Card eligibility and application page under Access San Joaquin).

The corpus's second Central Valley agency, and the first whose senior discount
age depends on the rider's city of residence: the DFC page qualifies seniors at
62+ in Manteca or Lathrop, 65+ in Tracy or Escalon, and 60+ in Stockton, Lodi,
Ripon, and other San Joaquin County cities, while the fares page's discount row
says only "ages 60 and over". Neither page publishes an effective-date range;
currency was corroborated against RTD's Commuter service page (modified
2026-02-27, fetched for verification only), which still sells the Sacramento
and Dublin BART service the fares page prices.

This id was recomputed at merge time: the branch derived 47bc4ec2e419 against
FAX's 95794539d1d0, County Connection's 60033300dc49 (#126) landed first, and
`make ingest` over the union re-stamped this heading — the same treatment
95794539d1d0 itself got. Further sibling agency merges will re-stamp their own
headings, not this one.
## 5db0d6a4bfbd (2026-08-14 UTC)

Added AC Transit (Alameda-Contra Costa Transit District): 12 agencies, 33
documents, 214 chunks. Three documents, all fetched 2026-08-14 UTC:
`actransit-fares` (Fares, stamped "Effective July 1, 2026"),
`actransit-fares-es` (a genuine Spanish translation — the corpus's second real
Spanish fares page, after MST's), and `actransit-discounts`.

AC Transit is the corpus's first accumulator-capped fare structure: Day,
Weekly, and Monthly passes are "fare maximums" applied automatically when
pay-per-ride spending reaches the pass price within the calendar period, and
both the rule and the amounts are published as HTML text, so the capping
structure is representable and cased (edge-actransit-001/002,
ml-actransit-002) rather than flattened.

Two corpus-hygiene findings recorded with the addition:
- `actransit.org/transfers` was excluded: undated, it states the
  Next-Generation Clipper interagency credit as "up to $2.85" and local
  transfers as "one free" where the dated fares page says "up to $3" and
  "unlimited" — the SolTrans ticket-office-location reasoning, applied to the
  page that lost the date contest. edge-actransit-003 forbids the $2.85.
- The fares page's Adult and Discounted tables arrive without their tab-widget
  labels bound to them (label order is the only binding); recorded in the
  manifest as a representational hazard for any discounted-figure case.

This id was recomputed at merge time: the branch derived 02c3bb15a506 against
the nine-agency 95794539d1d0, County Connection (60033300dc49) and San
Joaquin RTD (d113659adda8) landed first, and `make ingest` over the union
re-stamped this heading, as the 2026-08-12/13 additions did.
## 8b119819d9c8 (2026-08-14 UTC)

Added WestCAT (Western Contra Costa Transit Authority): 13 agencies, 37
documents, 227 chunks. Four documents, all fetched 2026-08-14 UTC:
`westcat-fares-all` (one table covering local/express, LYNX Transbay, ADA
paratransit, and Senior Dial-a-Ride), `westcat-transfers`, `westcat-clipper`,
and `westcat-buying`.

WestCAT publishes no robots.txt at all — both hosts 404 — which RFC 9309 reads
as no restrictions; the manifest's 10-second crawl delay was honored anyway.

Two findings travel with this version. First, WestCAT's Transfers page still
honors paper transfers "from County Connection and Tri Delta Transit at shared
stops in Martinez", while County Connection's own pages describe its transfers
as Clipper-only — one agency's current page describing another's apparently
retired product (xagency-013). Second, WestCAT publishes a 120-minute window
for inter-agency Clipper transfers where SolTrans' page says 60 minutes;
xagency-014 requires an answer to attribute each number rather than blend
them.

This id was recomputed at merge time: the branch derived 69aab4ac6576 against
the nine-agency 95794539d1d0; County Connection, San Joaquin RTD, and AC
Transit landed first, and `make ingest` over the union re-stamped this
heading, the way 95794539d1d0's was.
