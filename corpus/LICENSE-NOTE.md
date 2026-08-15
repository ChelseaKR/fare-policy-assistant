# About the text in this directory

Short version: none of the fare-policy text in this directory belongs to this
project, and this project cannot give you permission to reuse it.

## What is here

Thirty-nine web pages and two PDF documents published by fourteen California
transit agencies: nine fetched
on 2026-06-12, one refetched on 2026-08-10 after its site moved (hta-fares),
thirteen on 2026-08-13 (the yolobus-fares refetch for the new fare period, the
two Elk Grove Transit Services, three Santa Cruz METRO, and four SolTrans pages
added that day, and three FAX documents — one web page plus the corpus's first
two PDFs), and the County Connection, San Joaquin RTD, AC Transit, WestCAT,
and SLO RTA pages on 2026-08-14 UTC (checked 2026-08-13 Pacific). Every
document's exact fetch date is in `manifest.yaml` and in its
`corpus/raw/*.meta.yaml`. Plus everything derived from them:

| Path | What it holds |
|---|---|
| `raw/` | the fetched pages, byte for byte, as the agency served them |
| `processed/*.md` | the same pages, cleaned to markdown |
| `processed/chunks.jsonl` | the same text, split into retrieval chunks |
| `snapshots/**` | dated snapshots: raw bytes, chunks, and fetch receipts |
| `versions/*/chunks.jsonl` | chunk sets retained from earlier corpus versions |

`manifest.yaml` lists every document with its agency, source URL, and the fetch
date. The same text also reaches `evals/govchat/golden.jsonl` (as retrieved
passages), `evals/calibration/judge_label_packet_2026-07-11.md`, and short
excerpts inside the failure traces in `EVALS.md` and `docs/eval-report.*`.

## Whose it is

Each agency's. Monterey-Salinas Transit, Santa Barbara MTD, Yolo County
Transportation District (Yolobus), Sacramento Regional Transit District,
Humboldt Transit Authority, Santa Cruz Metropolitan Transit District, Solano
County Transit (SolTrans), the City of Fresno (Fresno Area Express), County
Connection (the Central Contra Costa Transit Authority), the San Joaquin
Regional Transit District (San Joaquin RTD), AC Transit (the Alameda-Contra
Costa Transit District), WestCAT (the Western Contra Costa Transit
Authority), and the San Luis Obispo Regional Transit Authority each
publish these documents on their own sites; the two
Elk Grove Transit Services (e-tran) pages are published by SacRT on sacrt.com,
where its fares have been published since the 2021 annexation.
Seventeen of the forty-one snapshots carry an explicit "all rights reserved" line in
the page footer (the three Santa Cruz METRO pages read "©2026 SC Metro, All
Rights Reserved.", both e-tran pages carry SacRT's "© 2026 Sacramento
Regional Transit District. All rights reserved.", the San Joaquin RTD fares
page reads "© 2026 San Joaquin Regional Transit District | All Rights
Reserved" — its DFC page's snapshot carries no copyright line at all, a
correction to the "both pages" wording this file briefly used — and the four
SLO RTA pages read "© 1989–2026 San Luis Obispo Regional Transit Authority.
All Rights Reserved." — and RTA is the one agency here whose linked Terms of
Use go beyond the default reservation, granting personal, non-commercial
copying and prohibiting other reproduction without written permission; see
the table below); the four MST pages, the
four SolTrans pages, and the FAX fares page carry a bare copyright notice
("©2026 Monterey-Salinas Transit", "© 2024 SolTrans", "© 2026 | City of
Fresno"), which reserves the same rights by default; and the two FAX PDFs carry
no rights statement at all.
Publishing something on a public agency website makes it publicly readable. It
does not place it in the public domain, and California's Public Records Act
governs disclosure, not copyright.

The project's own work in this repository (code, prompts, eval suites, docs, and
the reports generated from them) is Apache-2.0 licensed. The agency text is not,
and
`NOTICE` carves it out of that grant explicitly.

## Why it is here at all

So the evaluation can be re-run. This repository's product is an eval harness
whose scores only mean something if you can see the exact text the assistant was
answering from on the day it was scored. Fare pages change; a score computed
against a page that no longer exists is unfalsifiable. The snapshots are dated,
hashed, and committed for that reason, and for no other. They are not a
redistribution service, a mirror, or a dataset offered for reuse.

## What you may and may not assume

You may assume:

- these files are an accurate copy of what the agency served on the fetch date
  recorded in `manifest.yaml` and in each `raw/<id>.meta.yaml` receipt;
- you can read them, and reason about them, to understand or audit how this
  project's assistant and eval harness behave.

Do not assume:

- that the text is current. It is a snapshot with a date on it. Fares change and
  several of these programs have changed before;
- that this project grants you any right to copy, republish, redistribute, or
  train on the agency text. It holds no such right and grants none. The
  `license` field in `evals/govchat/golden.jsonl` says so on every row;
- that "public record" means "public domain", or that a robots.txt that allowed
  a polite fetch also permits republication. `manifest.yaml` records the
  robots/Content-Signal review separately from each agency's terms of use for
  exactly that reason;
- that this project speaks for any of these agencies. It is an independent
  portfolio project, not affiliated with, sponsored by, or endorsed by any of
  them.

If you want to reuse an agency's fare text, ask that agency.

## Go to the source

The agency's own site is authoritative for its fare policy, and is the only
place to check whether what is snapshotted here still holds:

| Agency | Fare information | Terms of use / site policies |
|---|---|---|
| Monterey-Salinas Transit (MST) | <https://mst.org/fares/> | no terms-of-use page located (checked 2026-08-12); site publishes a privacy policy only: <https://mst.org/privacy-policy/> |
| Santa Barbara MTD (SBMTD) | <https://sbmtd.gov/fares-passes/> | <https://sbmtd.gov/disclaimer/#terms_of_use> |
| Yolobus (Yolo County Transportation District) | <https://yolobus.com/fares/> | no terms-of-use page located (checked 2026-08-12); site publishes a privacy policy only: <https://yolobus.com/privacy-policy/> |
| Sacramento Regional Transit (SacRT) | <https://www.sacrt.com/fares/> | no website terms-of-use page located (checked 2026-08-12); <https://www.sacrt.com/terms-conditions/> exists but is purchase-order terms for vendors |
| Humboldt Transit Authority (HTA) | <https://hta.org/fares/> | no terms-of-use page located (checked 2026-08-12) |
| Elk Grove Transit Services (e-tran) | <https://www.sacrt.com/elk-grove-transit-fares/> | published on sacrt.com, so the SacRT row above governs; no separate terms page (checked 2026-08-12) |
| Santa Cruz METRO (SCMTD) | <https://scmetro.org/rider-info/fares-passes/> | no content-reuse terms located (checked 2026-08-12); site publishes a Privacy & Use Policy covering visitor data and vendor terms only: <https://scmetro.org/organization/privacy-use/> |
| Solano County Transit (SolTrans) | <https://www.soltrans.org/fares/fare-table> | no terms-of-use page located (checked 2026-08-12); the site's own sitemap enumerates one legal page only: <https://www.soltrans.org/about/policies/privacy-policy> |
| Fresno Area Express (FAX), City of Fresno | <https://www.fresno.gov/transportation/fares-passes/> | <https://www.fresno.gov/internet-policy/> ("Internet Policy", linked from the site footer; it has a section headed "Copy Restrictions") |
| County Connection (CCCTA) | <https://countyconnection.com/fares/fare-types-prices/> | no terms-of-use page located (checked 2026-08-13); the site's page sitemap lists no terms or legal page, and the footer carries no copyright line |
| San Joaquin RTD (SJRTD) | <https://sanjoaquinrtd.com/fares/> | no terms-of-use page located (checked 2026-08-13); the footer's "Legal & Policies" link resolves to a visitor-data privacy policy only: <https://sanjoaquinrtd.com/privacy-policy/> |
| AC Transit | <https://www.actransit.org/fares> | <https://www.actransit.org/disclaimer> (site disclaimer, recorded per document in `manifest.yaml`) |
| WestCAT (Western Contra Costa Transit Authority) | <https://www.westcat.org/home/FaresAll> | no terms-of-use page located (checked 2026-08-13); the site's Privacy Policy (`/Home/PrivacyPolicy`) covers visitor data only, and no footer carries a copyright line |
| San Luis Obispo RTA (SLORTA) | <https://www.slorta.org/fares/> | <https://www.slorta.org/terms-use/> (footer-linked; its Copyright Notice grants personal, non-commercial copying and requires written permission for other reproduction — read it there, and see the SLORTA note in `manifest.yaml` for how this project weighed it) |

Where the table says "none located", that is the finding, not a gap someone
forgot to fill: ten of these fourteen agencies publish no website terms of use
we could find (counting e-tran, whose publisher SacRT's row governs), and the
absence of stated terms is not permission. (An earlier revision said "five of
these nine"; the count had drifted — the base rows already showed seven
without terms — and the County Connection, AC Transit, and WestCAT rows were
added at the train merge, having been recorded per document in
`manifest.yaml` when those agencies landed.) Where a terms
page does exist, this table points at it and stops. Read each
agency's own page rather than this summary of where to find it, and do not rely
on anyone else's characterization of it, including this one.
