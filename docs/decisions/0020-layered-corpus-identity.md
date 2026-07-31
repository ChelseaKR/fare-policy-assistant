# 0020 — Layered corpus identity and source-complete archives

Date: 2026-07-30. Status: accepted for the web-policy evidence plane.

## Context

The original `corpus_version` is a 12-character digest over chunk ID, fetch
date, and text. It is already a public response field, an evaluation-provenance
field, a deployment pin, a cache input, and the name of processed-only archive
directories. Changing it in place would make rollback and existing evidence
ambiguous.

That digest is not behavior-complete. A chunk's agency, agency name, document
title, URL, language, section, document ID, and file order affect retrieval,
prompt context, citations, or rendering but were omitted. Conversely, a later
verification date changed `corpus_version` even when policy content was
identical. The legacy archives retain neither raw source bytes nor fetch
receipts, so they cannot prove a source-level snapshot after the working
`corpus/raw/` tree changes.

## Decision

Keep `corpus_version` unchanged as a legacy compatibility identity. Add two
full 64-character SHA-256 identities with schema-framed canonical JSON inputs:

- `content_version` covers the complete stored `Chunk` shape except
  `fetch_date`, in actual chunk order with an explicit ordinal. Order is part
  of behavior because equal-score retrieval ties currently inherit corpus
  order.
- `snapshot_version` covers `content_version` plus one observation per
  document, sorted by document ID: chunk fetch date, requested URL, final URL,
  successful HTTP status, effective format, raw SHA-256, and raw byte count.

Every chunk and observation is validated before hashing. Duplicate chunk IDs,
duplicate or missing document observations, inconsistent per-document chunk
metadata, non-HTTP URLs, malformed dates, unsupported formats, unsuccessful
receipts, and digest/size mismatches fail closed. Reverification on a later date
therefore preserves `content_version` while changing `snapshot_version`.

Schema-2 archives live under
`corpus/snapshots/<full-snapshot-version>/`. Each archive is self-contained:

```text
version.json
chunks.jsonl
manifest.snapshot.yaml
source-evidence.json
raw/<document-id>.<effective-format>
raw/<document-id>.meta.yaml
```

`version.json` records both full identities, their schema domains, the legacy
corpus ID, evidence status, counts, dates, and SHA-256 plus byte count for every
other archive artifact. The exact raw and receipt bytes are retained. The
legacy schema-1 directory remains processed-only; no snapshot identity is
invented for evidence it never stored.

Archive publication stages beneath `corpus/snapshots/`, durably writes every
file, reloads the stage, recomputes both identities, verifies every artifact,
and only then performs a same-filesystem atomic rename. Retries validate the
existing directory without changing its first `archived_at`. A concurrent
writer may win, but its archive must validate identically. Corrupt or
conflicting destinations are never overwritten. Ingestion first captures and
validates every receipt/raw byte pair, derives chunks from those retained bytes,
and archives the same capture; a concurrent fetch therefore cannot bind chunks
derived from one source revision to evidence from another. It publishes both
the schema-2 and legacy compatibility archives before atomically replacing the
live `chunks.jsonl`; an archive failure leaves the prior serving corpus intact.

The eleven pre-existing receipts predated the current `format` field. They are
explicitly migrated to `format: html`, matching their manifest defaults and
actual `.html` files. The strict identity validator is not weakened to infer
missing evidence. Repository attributes also disable line-ending conversion for
raw evidence and every schema-2 artifact; otherwise Git clean/checkout filters
could change byte-addressed HTML while leaving its receipt digest untouched.

## Rollout boundary

This decision makes `content_version` additive in corpus summaries. Existing
runtime pins, caches, history signatures, eval artifacts, and clients continue
to use `corpus_version` during this first compatibility release.

A following release will bind runtime, configuration, evaluation, console, and
deployment state into `config_version` and `release_version`, then require the
bundled descriptor and numeric candidate to agree. That staged rollout retains
one legacy-only rollback target.

GTFS and GTFS Scorecard artifacts are outside this web-policy snapshot. The
current GTFS pipeline discards original ZIPs and does not yet provide equivalent
transactional receipts. It will receive a separate evidence identity after its
snapshot process is made transactional; release identity can then aggregate
the independently scoped evidence.

## Consequences

- Any answer-, retrieval-, citation-, or rendering-relevant chunk mutation
  changes `content_version`.
- A source can be reverified without falsely reporting a policy-content change.
- Source-level archive corruption and partial publication are detected before
  promotion.
- Empty or partial manifest processing now fails rather than silently replacing
  the corpus with incomplete output.
- During the additive compatibility window, a behavior-changing chunk metadata
  or order change that collides on the narrower legacy `corpus_version` fails
  promotion instead of rewriting that compatibility archive. The following
  release-identity slice removes this operational ambiguity.
- Archives are larger because they retain exact source bytes. Git compression
  and delta storage mitigate repeated HTML; retention can move to a
  content-addressed blob store later without changing identity inputs.
- `content_version` is deliberately order-sensitive until retrieval implements
  an explicit stable tie-break independent of input order.

## Rejected alternatives

- **Redefine `corpus_version`.** This would silently reinterpret deployed pins,
  old evaluations, caches, and schema-1 archives.
- **Hash only normalized text.** Metadata and order change real behavior.
- **Include fetch date in semantic content.** It conflates observation with
  policy meaning.
- **Archive only digests.** A digest can identify missing bytes but cannot
  reproduce or independently inspect them later.
- **Write directly into the final directory.** A crash can create a destination
  that looks published but is incomplete.
- **Fold GTFS into this identity now.** Its present evidence contract is weaker
  and GTFS remains an advisory tripwire, not rider-facing policy truth.
