# Model Card — Transit Fare Policy Assistant

Reference implementation, not a product. Last updated 2026-07-11.

## Purpose

Answers rider questions about fares, passes, and reduced-fare programs for
nine California transit agencies: Monterey-Salinas Transit (MST), Santa
Barbara MTD (SBMTD), Yolobus, Sacramento Regional Transit (SacRT), Humboldt
Transit Authority (HTA), Elk Grove Transit Services (e-tran), Santa Cruz METRO
(SCMTD), Solano County Transit (SolTrans), and Fresno Area Express (FAX). It
explains published policy. It does not decide anything about any person.

## Intended users and uses

Riders and rider-facing staff asking factual questions about published fare
policy, in English or Spanish. Also engineers studying the evaluation harness,
which is the main artifact of this repository.

Tagalog is supported as an explicitly-tagged **stretch** language only, not a
supported one: see "Stretch languages" below before relying on it.

## Out of scope

- Eligibility determinations of any kind. The assistant describes published
  criteria and processes; agencies verify and decide.
- Medical, legal, or immigration questions, including advice about obtaining
  disability documentation.
- Agencies outside the corpus, real-time service status, trip planning.
- Anything requiring personal data. The assistant refuses questions
  containing ID numbers, birth dates, or contact details.

## System description

Retrieval-augmented generation over committed snapshots of public fare pages.
BM25 retrieval (optional dense retrieval behind a flag), top-8 passages with
an agency filter when the question names one. The answer model writes from
retrieved passages only and cites them inline. Input guards run before
retrieval (PII, scope, injection patterns); output guards block determination
language and uncited answers, replacing them with a refusal that points to the
agency.

### Input-guard language coverage

Detection patterns, not just the refusal text, are mirrored across languages so
a guard trips regardless of the question's language. The `guard_parity` block in
`evals/suites/refusal.yaml` asserts an English *and* a Spanish case per family.
Tagalog language detection is implemented, but fixed guard copy has no Tagalog
catalog and language-specific guard phrases have not reached EN/ES parity.

| Guard family        | English | Spanish | Tagalog        |
| ------------------- | :-----: | :-----: | :------------: |
| PII — SSN/email/phone/Medicare ID | ✅ | ✅ (locale-independent) | ✅ (locale-independent) |
| PII — date of birth | ✅ | ✅ | not yet mirrored |
| Scope — medical advice | ✅ | ✅ | not yet mirrored |
| Scope — immigration | ✅ | ✅ | not yet mirrored |
| Scope — legal advice | ✅ | ✅ | not yet mirrored |
| Prompt injection    | ✅ | ✅ | not yet mirrored |

Number- and symbol-based PII (SSN, email, phone, Medicare ID) is inherently
language-independent; the date-of-birth guard needs per-language lead-in phrases
("born on", "nací el", "fecha de nacimiento"), so it is tracked per language.

Models are pinned in `src/assistant/config.py`. Default backend is Claude on
Amazon Bedrock via cross-region inference profiles
(`us.anthropic.claude-haiku-4-5-20251001-v1:0` for answers,
`us.anthropic.claude-sonnet-4-6` as judge); the direct Anthropic API is
available behind a config switch.

## Data

Twenty-one public web pages and two public PDF documents, fetched with an
identified user agent between 2026-06-12 and 2026-08-13 (HTA added 2026-06-16;
the Elk Grove, Santa Cruz METRO, SolTrans, and FAX documents added 2026-08-13),
honoring robots.txt and crawl delays; URLs, dates, and license notes in
`corpus/manifest.yaml`. No user data is collected, stored, or used anywhere
in the system.

## Evaluation

216 cases across groundedness, refusal, edge-case, multilingual, freshness,
multi-turn conversation, cross-agency, counterfactual sensitivity, and
stretch-language (Tagalog) suites; method and
current scores in [EVALS.md](../EVALS.md). The scores published there predate
Santa Cruz METRO: the fourteen SCMTD cases added 2026-08-12 have not been
scored in a promoted live run yet, and EVALS.md carries the matching
corpus-version waiver in `evals/stale_acknowledged.json` until one happens.
Deterministic
checks run on every case; LLM-judge scores apply to live runs. Each live run
also records its exact fresh/cache token usage and an estimated cost (cache
writes and reads use their distinct rates), and checks the LLM
judge against a hand-labeled sample (`evals/calibration/judge_labels.jsonl`):
the report prints judge-vs-human agreement and Cohen's κ over that sample
(harness in `evals/calibration.py`). That sample is currently 4 scored labels
against a floor of 37; the queued replacement is
`evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl`, labeled with
`make relabel`, which shows each row's criterion, question, passages, and
answer, and records a verdict only after the reviewer states one. A second,
black-box pass by the separate GovChat-Eval harness (same author, not public) is
in [docs/audits/](audits/methodology.md).

Known limits found by the harness so far:

- BM25 absolute scores do not reliably separate out-of-corpus questions from
  in-corpus ones, so the decline rule reads normalized, corpus-size-
  independent signals (a z-score against the full-corpus score distribution,
  the top-1/top-2 margin, query-term coverage) calibrated against a labeled
  should-answer/should-decline set instead of a raw score threshold (see
  `docs/decisions/0001` and `docs/decisions/0013`). The system prompt and the
  missing-citation guard remain the second and third layer regardless.
- Spanish coverage is strongest for MST, which publishes a Spanish fares
  page. For the other agencies Spanish answers depend on cross-lingual
  retrieval over English documents, and the parity table in EVALS.md shows
  where that falls short.
- Tagalog is a **stretch** language, not a supported one, and the model card
  says so on purpose. No corpus document is published in Tagalog (unlike
  Spanish's `mst-fares-es`), so `evals/suites/stretch_tagalog.yaml` is an
  honest, mirrored, all-cross-lingual test: a fare-vocabulary lexicon
  (`assistant.retrieve._TL_EN_LEXICON`) bridges a Tagalog query to the
  English corpus at retrieval time. The answer model can respond in Tagalog,
  `assistant.guards.detect_language` identifies it, and fixed guard/no-support
  copy plus core injection, PII lead-in, determination, and as-of patterns now
  have Tagalog coverage. This strengthens the deterministic safety seam but
  does not create source-language parity. The current live stretch suite is
  15/15, but that measures cross-lingual retrieval and guarded output—not an
  agency-authored Tagalog policy corpus or fluent-human translation review. The
  remaining constraint stays visible in the
  "Stretch-language parity (Tagalog)" table in EVALS.md, not a bug to
  silence. Chinese, Vietnamese, and Korean remain unaddressed; Tagalog was
  chosen first because it is space-delimited Latin script, which the
  existing tokenizer already handles (docs/ROADMAP.md P3-3).
- The overall pass count moves by a couple of cases run to run. A handful of
  cases sit at the LLM judge's groundedness/helpfulness decision boundary (and
  the answer model is not perfectly deterministic at temperature 0 on Bedrock),
  so the headline is a band (the consolidation run was 160 of 201) rather than a fixed number. The
  deterministic safety checks — no determination language, citation present,
  PII not echoed — do not vary. The regression gate ignores single-case suite
  moves for this reason and trips only on a drop of two cases or more.

## Escalation

When the corpus lacks an answer, retrieval confidence is low, or a guard
trips, the assistant says so and points to the agency's customer service or
511.org. It is designed to fail toward "ask the agency," never toward a
guess.

## Freshness

Fare policy changes. Every answer carries the snapshot date of its sources,
and the corpus manifest records fetch dates per document. Snapshots should be
refreshed (and evals re-run) before any renewed use.

## Accessibility

The demo page targets WCAG 2.2 AA. A pure-Python structural gate
(`web/a11y.py`) runs in CI on every change — page language, labeled controls,
heading order, link text, zoom not disabled, 24px-minimum target size — and an
advisory pa11y/axe pass cross-checks computed contrast and ARIA semantics. What
neither can verify is the lived experience: a manual screen-reader and
keyboard-only walkthrough is the human step the automation explicitly does not
replace, and it should be done and recorded before the demo is presented
as production-ready. The walkthrough checklist and its result log live in
[docs/audits/a11y-walkthrough.md](audits/a11y-walkthrough.md). As of this writing
the automated gates pass; the manual pass is pending (no result row recorded
yet).
