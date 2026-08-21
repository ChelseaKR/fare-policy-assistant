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
## b21d4e3b3c98 (2026-08-14 UTC)

Added SLO RTA (San Luis Obispo Regional Transit Authority): 14 agencies, 41
documents, 249 chunks. Four documents, all fetched 2026-08-14 UTC (checked
2026-08-13 Pacific): `slorta-fares` (the cash fare table, headed "New cash
fares as of April 6, 2026"), `slorta-discounts` (eligibility categories and
the RTA Discount Eligibility Card), `slorta-passes` (pass products, the VIP
Pass, ADA free fixed-route), and `slorta-contactless` (Tap2Ride, Token
Transit, and the full fare-capping table).

Central Coast, filling the gap between SBMTD and MST, and the only corpus
agency with an age-tiered senior fare: 65-79 pay half, 80 and over ride free
with a VIP Card. Unlike Santa Cruz METRO, whose cap amounts exist only inside
a PNG, RTA publishes its fare-capping amounts as HTML, so they are citable.

Two corpus-hygiene findings recorded with the addition:

- The fares page and the discounts page disagree about whether the child free
  fare (44 inches and under) applies on the South County routes; both are
  ingested as published, the manifest records the conflict, and eval case
  edge-100 pins the honest behavior (state the rule, surface the
  disagreement, decide nothing).
- The contactless page disagrees with itself about whether the disabled
  discount is available on Tap2Ride ("not available ... yet" in one section,
  "now available" in its FAQ). Eval case fresh-026 requires the cash path,
  which works under either reading.

This id was recomputed at merge time: the branch derived 21251137e67b against
FAX's 95794539d1d0; County Connection, San Joaquin RTD, AC Transit, and
WestCAT landed first, and `make ingest` over the union re-stamped this
heading, the same way 95794539d1d0 itself was recomputed.
## cbc07c922784 (2026-08-14 UTC)

Added VTA (Santa Clara Valley Transportation Authority): 15 agencies, 44
documents, 264 chunks. Three documents, all fetched 2026-08-14 UTC:
`vta-fares`, `vta-regional-transfers`, and `vta-rtc-card`.

The addition that documents its own limit: VTA's fare table renders its four
rider-category tables (Adult, Adult Express, Senior/Disabled, Youth) in a tab
widget whose labels are UI furniture the ingester drops, so the four price
blocks land category-blind and THE CATEGORY-TO-PRICE TABLE IS NOT FAITHFULLY
REPRESENTABLE in this pipeline — recorded at length in the manifest, and
pinned by eval case refuse-vta-001 (the honest partial for an amount-by-
category question) rather than flattened into labeled prices the source text
does not contain. What VTA does publish as prose — the Day Pass Accumulator
cap, 2-hour Clipper transfers with the express carve-out, the $2.50 express
surcharge and its exemptions, the discount ages and document alternatives,
the calendar-month pass window, the full Clipper START criteria, the
Paratransit ID exclusions — is in the corpus and cased.

One page fetched and excluded with its reason in the manifest:
/go/fares/clipper (card-purchase and TVM mechanics, the METRO
splash-pass-faqs precedent).

This id was recomputed at merge time: the branch derived 491751588366 against
the nine-agency 95794539d1d0; County Connection, San Joaquin RTD, AC Transit,
WestCAT, and SLO RTA landed first, and `make ingest` over the union
re-stamped this heading, as the 2026-08-12/13 additions did.
## 09f9c297deba (2026-08-14 UTC)

Added Napa Valley Vine Transit (VINE, operated by the Napa Valley
Transportation Authority): 16 agencies, 46 documents, 270 chunks. Two
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

This id was recomputed at merge time: the branch derived 7caf9b3638ec against
the nine-agency 95794539d1d0; County Connection, San Joaquin RTD, AC Transit,
WestCAT, SLO RTA, and VTA landed first, and `make ingest` over the union
re-stamped this heading, the way 95794539d1d0's was.
## cbfe81efcf3f (2026-08-14 UTC)

Added SamTrans (San Mateo County Transit District): 17 agencies, 49
documents, 279 chunks. Three documents, all fetched 2026-08-14 UTC:
`samtrans-fares`, `samtrans-fare-types`, and `samtrans-clipper`.

The structures that earn the disk space: a merged discount row (Youth and
the senior/disabled/Medicare "Eligible Discount" class share one price
line), a two-children-age-4-or-younger-free-per-paying-adult rule, the
Youth Unlimited free-fare program for Socioeconomically Disadvantaged
students, a 19th-birthday cliff that cancels an active Youth Monthly Pass
mid-month, and fare waivers with two simultaneous conditions (uniform AND
unexpired military ID). And one named-but-unpublished structure, recorded
in the manifest rather than invented: SamTrans says open-payment fare
capping "is calculated separately from Monthly Pass usage" and publishes no
cap amounts, period, or rule anywhere in the fetched pages — the corpus
carries that one sentence, and refuse-samtrans-001 pins the honest partial
answer.

Two pages fetched and excluded with reasons in the manifest:
/fares/fare-structure (a pointer hub to the codified-tariff PDF, itself the
only dated fare-period statement SamTrans publishes — a PDF-ingest
candidate for a follow-up) and /rider-info/youth-riders (a 60-row
school-route table, the FAX Handy Ride lesson).

This id was recomputed at merge time: the branch derived af0feed6c742 against
the nine-agency 95794539d1d0; County Connection, San Joaquin RTD, AC Transit,
WestCAT, SLO RTA, VTA, and the Vine landed first, and `make ingest` over the
union re-stamped this heading, as the 2026-08-12/13 additions did.
## 3dd8b7bd757e (2026-08-14 UTC)

Added Marin Transit (Marin County Transit District): 18 agencies, 52
documents, 301 chunks. Three documents, all fetched 2026-08-14 UTC:
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

This id was recomputed at merge time as the last car of the ten-PR expansion
train: the branch derived 0a8bbe659de3 against the nine-agency 95794539d1d0;
County Connection, San Joaquin RTD, AC Transit, WestCAT, SLO RTA, VTA, the
Vine, and SamTrans landed first, and `make ingest` over the full
eighteen-agency union re-stamped this heading, as the 2026-08-12/13
additions did.

## 10deac978967 (2026-08-21)

Re-fetched all three Yolobus documents together: `yolobus-fares`,
`yolobus-purchasing`, `yolobus-reduced-fare-id`. No fare amount changed
(630 fare facts before and after, byte-identical `facts.jsonl`) and no
agency was added or removed; this is a same-agency coherence fix.

`yolobus-fares` was refreshed on its own by #114 (2026-08-12,
corpus_version 74b05330cb39), rolling the fare period to July 2026-June
2027 and updating the Yolobus Customer Service Center's hours to Mon-Fri
7:00 am-7:00 pm / Sat 9:00 am-3:00 pm. `yolobus-purchasing` and
`yolobus-reduced-fare-id` were not refreshed in that PR and kept the
retired Mon-Thu 9am-noon/1-4pm hours, the old Yolo Transportation District
street address, and the old office phone number — the exact
same-agency-contradicts-itself failure mode PR #117 (opened independently,
same day, and closed as superseded by #114 without merging) warned about:
a rider asking where to get a reduced-fare ID would be told hours the
office no longer keeps.

Live-refetched 2026-08-21 with the project's own tooling
(`assistant.ingest fetch yolobus-fares yolobus-purchasing
yolobus-reduced-fare-id`, then `assistant.ingest process`), from a
residential network. Re-processing reproduced byte-identical chunks for
every one of the other fifty non-Yolobus documents.

What changed on the two previously-unrefreshed pages, both now matching
`yolobus-fares`'s hours:

| | Before | After |
|---|---|---|
| YTD office address | 350 Industrial Way, Woodland | 352 Industrial Way, Woodland |
| YTD office phone | (530) 661-0816 | (530) 666-BUSS (2877) |
| Customer service hours | Mon-Thu 9:00 am-Noon, 1:00-4:00 pm | Mon-Fri 7:00 am-7:00 pm, Sat 9:00 am-3:00 pm |

`yolobus-fares` itself moved one more line since its 2026-08-12 fetch: the
unlimited-ride pass table dropped the "UC Davis Zip Pass" row entirely
(previously listed as accepted with a valid student ID). Yolobus's own
zippass page has said the app is discontinued since before that date, so
this is the site's self-contradiction resolving itself, not a new one.
`evals/suites/freshness.yaml::fresh-020` was written against that
contradiction and is flagged in place (not rewritten) as a case whose
tested premise may no longer hold — see the comment on the case.

Two eval cases updated to match, since their required facts were the
retired hours/address rather than the policy the case exists to test:
`edge_cases::edge-034` (rationale only; its required_facts already matched
coincidentally) and `edge_cases::edge-047` (required_facts: `350 Industrial
Way` to `352 Industrial Way`).
