# ADR 0000: Record architecture decisions

Date: 2026-07-16. Status: accepted.

## Context

This repo makes a small number of consequential, hard-to-reverse decisions —
retrieval design, corpus versioning, guard architecture, deploy shape, which
model calls happen where. The reasoning behind a structural choice must not
live only in a commit message or a closed PR thread, or a later change will
either re-litigate a settled question or unknowingly reverse a decision made
for a reason nobody re-reads.

This repo has recorded such decisions since its first week: ADRs 0001–0017
live in [`docs/decisions/`](../decisions/), predating this file. The portfolio
standards expect the seed record at `docs/adr/0000-record-architecture-decisions.md`,
so this file formalizes the practice at that path rather than rewriting
history by moving seventeen cross-referenced records.

## Decision

We record architecture decisions in **Architecture Decision Records (ADRs)**
using the format described by Michael Nygard.

- Each ADR is a short Markdown file, numbered sequentially and named
  `NNNN-title-in-kebab-case.md`. The existing log is `docs/decisions/`
  (ADRs 0001–0017 and onward); new ADRs continue that sequence there.
- Each ADR carries a date, a status (proposed, accepted, deprecated, or
  superseded), a Decision section, and its reasoning.
- A superseded ADR is not deleted; it is marked superseded and points to the
  ADR that replaces it.
- ADRs are immutable once accepted, except to change their status. A new
  decision is a new ADR, not an edit to an old one.

## Consequences

- The reasoning behind structural decisions is preserved and versioned
  alongside the code it explains.
- Writing an ADR is a small, deliberate friction on consequential change —
  intended, since it makes reversing a load-bearing decision a visible act
  rather than an accident.
- Two directories exist: this seed at the standards-mandated path, and the
  working log in `docs/decisions/`. Cross-references throughout the repo
  (README, model card, source comments) point at `docs/decisions/` and stay
  valid.
