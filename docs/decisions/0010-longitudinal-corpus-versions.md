# ADR 0010: Longitudinal, time-versioned corpus retention

Date: 2026-07-08. Status: accepted. Resolves ideation EXP-05
(`docs/ideation/03-expansions.md`).

## Decision

`corpus/raw/` and `corpus/processed/` still hold exactly one snapshot each and
are still overwritten in place by `make fetch` / `make ingest` — that part of
the pipeline is unchanged. What changes: `assistant.ingest.process_all()` now
ends by calling `assistant.corpus.archive_version(chunks, manifest)`, which
retains a permanent, processed-only copy of that content under
`corpus/versions/<corpus_version>/`:

- `chunks.jsonl` — the exact chunk set for that version, in the same shape
  `load_chunks` already reads.
- `manifest.snapshot.yaml` — which documents (id, agency, title, url, format,
  language) made up this version, independent of the manifest's crawl-policy
  fields, which are not part of the corpus's identity.
- `version.json` — the `corpus_summary()` for that version plus `archived_at`.

Archiving is content-addressed and idempotent: `corpus_version` is a
deterministic hash of chunk text and fetch dates (unchanged from R2-6), so a
`make ingest` that reproduces the same content is a no-op against an existing
archive, and a `make ingest` that changes anything gets a new, permanent
directory. Nothing under `corpus/versions/` is ever edited or deleted by the
pipeline.

`assistant.corpus` gains three read paths over that retained history:

- `load_chunks(version=<id>)` — the exact chunk set for a past version, the
  same shape as `load_chunks()` for the live corpus.
- `list_versions()` — every retained version id, oldest first.
- `changelog()` — `diff_corpus` chained across every consecutive pair of
  retained versions, i.e. the full "what changed when" history generated from
  what is actually retained, not hand-seeded.

`web/handler.py`'s `/version` endpoint now also reports `known_versions` (most
recent 10, if any are present). This is provenance only: the serving path
stays pinned to the one corpus version an operator approved
(`FPA_PINNED_CORPUS_VERSION`) — there is no time-travel answering for riders,
matching the ideation doc's explicit boundary.

## Why

Before this, corpus history lived only in git: reconstructing what the
assistant would have said on a past date meant checking out an old commit and
rebuilding the retrieval index by hand, and `corpus/CHANGELOG.md`'s own header
said the entries were meant to be hand-appended by the weekly freshness
automation. Neither gives an operator or a researcher a queryable answer to
"what corpus did eval run X see" without archaeology.

The impact is threefold (per the ideation pitch): an audit trail for what the
assistant would have said on date X (a liability-candor asset, given the
Air-Canada-style precedent noted in the research roadmap); a generated,
non-hand-seeded changelog; and a small longitudinal fare-policy dataset as a
byproduct, which is a genuinely scarce public artifact in this space.

## Repo-size mitigation

Retention is processed-only, as the ideation doc's risk section calls for: no
raw HTML is archived per version, only chunks (already the smallest
lossless-enough representation the pipeline produces) plus a small manifest
snapshot and metadata file. `infra/deploy.sh` already only ships
`corpus/processed/chunks.jsonl` into the Lambda bundle, so `corpus/versions/`
adds nothing to the deploy artifact regardless of how large the retained
history grows — it is a dev-checkout and CI artifact, not a serving-path one.

## Consequences

- The weekly corpus-freshness workflow (`.github/workflows/corpus-freshness.yml`)
  already runs `assistant.ingest process`, so it archives a new version for
  free whenever a re-fetch changes anything, and the archive lands in the same
  refresh PR as the rest of the corpus diff (its `add-paths: corpus/` already
  covers `corpus/versions/`).
- Every eval run's `summary.json` already records `corpus_version`
  (unchanged); that id is now guaranteed loadable via
  `corpus.load_chunks(version=...)` for as long as the archive is retained, so
  "what did eval run X see" stops requiring git archaeology.
- The currently committed corpus (`0938fff0539a`, matching the existing
  `corpus/CHANGELOG.md` entry) was retroactively archived as part of landing
  this change, so the archive is populated from day one rather than starting
  empty.
