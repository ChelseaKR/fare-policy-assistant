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

The weekly corpus-freshness automation
(`.github/workflows/corpus-freshness.yml`) is the intended writer of these
entries: on a re-fetch that changes any document it calls
`assistant.corpus.diff_corpus` against the prior snapshot and appends the added,
removed, and changed documents below. That workflow has failed every run since
2026-07-13 (its runners get 403s from MST and the refresh is all-or-nothing), so
entries after the initial one have been written by hand from the same tool,
`tools/corpus_refresh_report.py`, and one version is missing from the chain
entirely. Each entry says which agencies it actually touched.

## 500e2b8c6090 (2026-08-13)

Changed:
- yolobus-fares
- yolobus-purchasing
- yolobus-reduced-fare-id

Yolobus-only refresh: the three Yolobus documents were re-fetched, no other
agency. `yolobus-fares` had been contained since the deploy default was set
(`FPA_DISABLED_DOC_IDS=yolobus-fares`) because its committed fare period,
"All below fares are effective July 1, 2025 – June 30, 2026", had ended. The
replacement page publishes "All below fares are effective July 1, 2026 –
June 30, 2027", so the snapshot is inside its stated period again.

No fare amount moved. Every dollar figure on the three pages is identical to
the 2026-06-12 snapshot, single-ride, monthly, transfer, BeeLine and ADA
paratransit alike, and `corpus/processed/facts.jsonl` is byte-identical.
What changed besides the dates: the youth-free line is now worded "Youth ages
18 and under ride free!" rather than "Youth ages 0-18 ride free!" (same rule);
the Yolo Transportation District customer service center is now 352 Industrial
Way, phone (530) 666-BUSS (2877), open Mon-Fri 7:00 am - 7:00 pm and Sat
9:00 am - 3:00 pm, replacing 350 Industrial Way, (530) 661-0816, Mon-Thu
9am-12pm and 1pm-4pm; and Unitrans joins Transit Connect "in 2026" rather than
"in summer 2026". Chunk count 90 to 89: Yolobus demoted the Transit Connect
heading, so that paragraph now folds into the Connect Card section.

Containment was deliberately NOT lifted in this change. `yolobus-fares` stays
in `FPA_DISABLED_DOC_IDS`; removing it needs an evaluation run against this
corpus and the owner's approval.

Note on the chain: corpus version 35ec70d6359d (2026-08-10, the hta.org domain
move) sits between 0938fff0539a and this entry and was never recorded here,
because the weekly automation that writes these entries has been failing since
2026-07-13.

## 0938fff0539a (2026-06-17)

Initial recorded version. Five agencies, 11 documents, 90 chunks: Monterey-Salinas
Transit (MST), Santa Barbara MTD (SBMTD), Yolobus, Sacramento Regional Transit
(SacRT), and Humboldt Transit Authority (HTA). Fetch dates per document are in
`corpus/manifest.yaml` and the per-snapshot `corpus/raw/<id>.meta.yaml` files.
