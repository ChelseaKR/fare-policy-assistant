# Ideation — large-scale fixes and expansions

Drafted 2026-07-01. Status: **ideas for evaluation, not commitments.**

## What this folder is

A third planning layer for the fare-policy assistant, produced from a fresh
read of the whole repository (source, evals, CI, corpus, docs, git history).
The two existing layers are:

- [`docs/ROADMAP.md`](../ROADMAP.md) — the original build spec and its
  productionalization phases (P0–P3), largely executed.
- The 2026-06 research pass — [`docs/research/synthetic-personas-feedback.md`](../research/synthetic-personas-feedback.md)
  (R0–R3 backlog, mostly executed) and, on the still-unmerged
  `research-panel-and-roadmap` branch, `docs/RESEARCH-ROADMAP.md` /
  `docs/USER-RESEARCH.md` (RR1–RR10, RE1–RE7).

This folder deliberately does **not** restate any item from those documents.
Where an idea builds on an existing item it references the item's ID (P1-4,
R3-1, RR6, RE5, …) and goes beyond it. Everything here is net-new: structural
fixes and expansion bets the existing roadmaps do not contain.

## Contents

| File | What it holds |
|---|---|
| [`01-deep-dive.md`](01-deep-dive.md) | Current-state assessment from this read: architecture, strengths, observed structural debt, portfolio position |
| [`02-large-scale-fixes.md`](02-large-scale-fixes.md) | FIX-01 … FIX-12 — deep structural fixes with effort, risk, and a measurable bar for each |
| [`03-expansions.md`](03-expansions.md) | EXP-01 … EXP-14 — expansion ideas in three horizons (deepen core / adjacent / transformative) |
| [`04-impact-and-sequencing.md`](04-impact-and-sequencing.md) | Impact×effort matrix, dependencies, a Now/Next/Later sequence, and the honest list of human/legal/SME/credential gates |

## How to read it

The repo's ethos binds every idea here: honesty as a feature, liability
candor, reproducibility, accessibility, multilingual equity, and the hard
rules in [`CLAUDE.md`](../../CLAUDE.md) (no eligibility determinations, no
PII, every answer cited, corpus dated). Several ideas are explicitly gated on
things this environment does not have — live Bedrock credentials, a human
with a screen reader, legal counsel, agency partners — and
`04-impact-and-sequencing.md` separates those out rather than pretending they
can be closed by code alone. Where a claim below rests on something not
verified in this pass, the text says so.
