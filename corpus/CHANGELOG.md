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

## 7caf9b3638ec (2026-08-14 UTC)

Added Napa Valley Vine Transit (VINE, operated by the Napa Valley
Transportation Authority): 10 agencies, 25 documents, 158 chunks. Two
documents, both fetched 2026-08-14 UTC: `vine-fares` (the whole fixed-route
policy on one page, including the age-80+ complimentary Lifetime Pass) and
`vine-go` (VineGo paratransit zone fares).

Two exclusions carry the findings. The FAQ page contradicts the fares page on
the transfer window — "ONE hour" against the fares page's "90 minutes" — so it
is excluded the way SolTrans' 20-percent duplicate was, and edge-083 is the
tripwire on the ingested number. The Summer Youth Pass news page sells a $20
seasonal pass "valid from June 1 to August 31" with no year printed anywhere;
committed, it would go stale undetectably, so the corpus stays silent and
fresh-023 pins decline-and-redirect (the fresh-008 pattern).

Golden Gate Transit was checked for this batch and is recorded in the manifest
header as a NO-GO of a new kind: fetchable (permissive robots, no WAF, all
pages 200) but not ingestible, because the site wraps every page body in a
single ASP.NET form element and this pipeline's cleaner strips form tags
wholesale, so every page cleans to zero sections.

Written on a branch that adds only VINE. If another corpus change merges
first, this id needs another `make ingest` and a re-stamp of this heading, the
way 95794539d1d0's was.
