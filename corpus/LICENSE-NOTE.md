# About the text in this directory

Short version: none of the fare-policy text in this directory belongs to this
project, and this project cannot give you permission to reuse it.

## What is here

Twelve web pages and two PDF documents published by six California transit
agencies, fetched once each (nine on 2026-06-12, one refetched on 2026-08-10
after its site moved, one refetched on 2026-08-13 UTC for a new fare period,
and three added on 2026-08-13 UTC), plus everything derived from them:

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
Humboldt Transit Authority, and the City of Fresno (Fresno Area Express) each
publish these documents on their own sites. Seven of the fourteen snapshots
carry an explicit "all rights reserved" line in the page footer; the four MST
pages and the FAX fares page carry a bare copyright notice ("©2026
Monterey-Salinas Transit", "© 2026 | City of Fresno"), which reserves the same
rights by default, and the two FAX PDFs carry no rights statement at all.
Publishing something on a public agency website makes it publicly readable. It
does not place it in the public domain, and California's Public Records Act
governs disclosure, not copyright.

The project's own work in this repository (code, prompts, eval suites, docs, and
the reports generated from them) is MIT licensed. The agency text is not, and
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
| Fresno Area Express (FAX), City of Fresno | <https://www.fresno.gov/transportation/fares-passes/> | <https://www.fresno.gov/internet-policy/> ("Internet Policy", linked from the site footer; it has a section headed "Copy Restrictions") |

Where the table says "none located", that is the finding, not a gap someone
forgot to fill: three of these six agencies publish no website terms of use we
could find, and the absence of stated terms is not permission. Where a terms
page does exist, this table points at it and stops. Read the agency's own page;
do not rely on anyone else's characterization of it, including this one.
