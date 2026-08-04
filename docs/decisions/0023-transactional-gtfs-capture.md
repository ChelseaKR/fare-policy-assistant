# 0023 — Transactional, exact GTFS feed capture

Date: 2026-07-30. Status: accepted. Extends ADR 0011's GTFS-Fares
cross-validation channel and provides the provenance prerequisite identified in
ADRs 0020–0022.

## Context

The original GTFS fetcher downloaded one ZIP at a time and wrote selected
members directly into `corpus/raw/gtfs/<agency>/`. It discarded the original
ZIP, recorded no digest, and could expose a mixed set if the first agency
succeeded and a later agency failed. The resulting files were useful for fare
checks, but they could not prove which complete feed supplied them or be matched
exactly to independent upstream evidence.

That is insufficient for a GTFS Scorecard integration. A similar agency name,
URL, or fetch date is not proof that two systems assessed the same bytes.

## Decision

`assistant.gtfs.fetch_all` publishes one coherent, content-verified set:

```text
corpus/raw/gtfs/
  current.json
  snapshots/
    <agency>/
      <receipt_sha256>/
        feed.zip
        receipt.json
        fare_attributes.txt | fare_products.txt
        rider_categories.txt (when present)
```

Each response is first downloaded into one hidden transaction directory. The
fetcher validates every ZIP member path, rejects duplicates, links, encrypted
members, unsupported special files, over-limit members, invalid CSV headers,
and a feed that does not contain the fare file required by its configured v1 or
v2 schema. It extracts only files the application reads today. The exact ZIP is
retained so a later, reviewed consumer can inspect another member without
pretending it was part of the current extracted-input contract.

`receipt.json` is canonical JSON under
`fare-assistant.gtfs-feed-receipt.v1`. It binds:

- agency and configured/inferred GTFS-Fares schema;
- requested and final response URLs;
- whole-second UTC fetch time and successful HTTP status;
- exact ZIP SHA-256 and byte count; and
- each extracted file's name, SHA-256, and byte count.

The SHA-256 of the canonical receipt bytes names the immutable feed snapshot.
Validation reloads the ZIP, receipt, and extracted inputs and proves their
agreement before publication. Existing snapshot directories are validated and
reused, never overwritten.

Only after every feed selected for the transaction has downloaded, validated,
and staged successfully are the immutable directories installed. One
same-filesystem `os.replace` then publishes canonical
`fare-assistant.gtfs-current.v1` as `current.json`. A failed download,
validation, extraction, or pre-commit publication leaves the previous pointer
and all data reachable through it unchanged. A crash may leave an unreachable
immutable directory, which is safe; it cannot become current without the
pointer commit.

The manifest's `set_version` is a SHA-256 digest under
`fare-assistant.gtfs-selected-set.v1` over the sorted feed pointers, excluding
the publication time. `current_snapshot_set_version()` exposes that verified
identity for evaluation and promotion evidence.

A partial `fetch <agency>` is permitted only after a complete transactional set
exists. It replaces the selected member while carrying forward and revalidating
the other configured members. The first transactional fetch must include every
configured agency.

## Read and migration contract

`feed_snapshot_directory(agency)`, `load_current_snapshot_set()`, and
`current_snapshot_set_version()` are the storage and identity APIs. Fare
parsing and rider-category lookup resolve through them. An agency absent from
an existing current set is reported as not selected; no unreferenced directory
is consulted.

Repositories that have not yet run the new fetcher have no `current.json`; in
that one state, reads fall back to the legacy
`corpus/raw/gtfs/<agency>/` directories. Once a transactional pointer exists,
reads fail closed on a corrupt or incomplete pointer and never silently mix in
legacy mutable files. The old directories may be removed in a later, explicit
data migration after exact captures are committed.

The GTFS set remains separate from the web-policy `snapshot_version` and rider
release identity. This change establishes exact GTFS provenance; it does not
make GTFS or any external score rider-facing policy truth.

## GTFS Scorecard boundary

Scorecard integration remains a separate change. It may attach advisory,
offline promotion evidence only when its upstream record identifies the exact
same ZIP SHA-256 and byte count selected in `current.json`, uses a supported
Scorecard schema, and agrees on the fare-model classification. Agency names,
URLs, dates, or grades alone are not an acceptable join. An upstream outage or
mismatch must preserve the current rider service and current GTFS pointer.

## Consequences

- Exact source bytes and canonical receipts make independent provenance
  comparison possible.
- A multi-feed refresh cannot expose a half-old, half-new selected set.
- Full GTFS ZIPs consume more repository or artifact storage than the legacy
  fare slice. Immutability and content receipts make that cost explicit.
- Adding another extracted member is a reviewed evidence-contract change.
- Legacy extracted directories remain readable only as a pre-pointer migration
  bridge and cannot claim an exact ZIP digest.
