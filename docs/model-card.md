# Model Card — Transit Fare Policy Assistant

Reference implementation, not a product. Last updated 2026-06-12.

## Purpose

Answers rider questions about fares, passes, and reduced-fare programs for
five California transit agencies: Monterey-Salinas Transit (MST), Santa
Barbara MTD (SBMTD), Yolobus, Sacramento Regional Transit (SacRT), and
Humboldt Transit Authority (HTA). It explains published policy. It does not
decide anything about any person.

## Intended users and uses

Riders and rider-facing staff asking factual questions about published fare
policy, in English or Spanish. Also engineers studying the evaluation harness,
which is the main artifact of this repository.

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
Tagalog is pending FIX-11 (language detection).

| Guard family        | English | Spanish | Tagalog        |
| ------------------- | :-----: | :-----: | :------------: |
| PII — SSN/email/phone/Medicare ID | ✅ | ✅ (locale-independent) | ✅ (locale-independent) |
| PII — date of birth | ✅ | ✅ | pending FIX-11 |
| Scope — medical advice | ✅ | ✅ | pending FIX-11 |
| Scope — immigration | ✅ | ✅ | pending FIX-11 |
| Scope — legal advice | ✅ | ✅ | pending FIX-11 |
| Prompt injection    | ✅ | ✅ | pending FIX-11 |

Number- and symbol-based PII (SSN, email, phone, Medicare ID) is inherently
language-independent; the date-of-birth guard needs per-language lead-in phrases
("born on", "nací el", "fecha de nacimiento"), so it is tracked per language.

Models are pinned in `src/assistant/config.py`. Default backend is Claude on
Amazon Bedrock via cross-region inference profiles
(`us.anthropic.claude-haiku-4-5-20251001-v1:0` for answers,
`us.anthropic.claude-sonnet-4-6` as judge); the direct Anthropic API is
available behind a config switch.

## Data

Twelve public web pages fetched 2026-06-12 (HTA added 2026-06-16) with an
identified user agent,
honoring robots.txt and crawl delays; URLs, dates, and license notes in
`corpus/manifest.yaml`. No user data is collected, stored, or used anywhere
in the system.

## Evaluation

118 cases across groundedness, refusal, edge-case, multilingual, freshness, and
multi-turn conversation suites; method and current scores in
[EVALS.md](../EVALS.md). Deterministic
checks run on every case; LLM-judge scores apply to live runs. Each live run
also records its exact token usage and an estimated cost, and checks the LLM
judge against a hand-labeled sample (`evals/calibration/judge_labels.jsonl`):
the report prints judge-vs-human agreement and Cohen's κ over that sample
(harness in `evals/calibration.py`). An independent black-box audit by the
external GovChat-Eval harness is in [docs/audits/](audits/methodology.md).

Known limits found by the harness so far:

- BM25 absolute scores do not reliably separate out-of-corpus questions from
  in-corpus ones, so low-confidence refusal cannot rest on a score threshold
  alone (see `docs/decisions/0001`). The system prompt and the
  missing-citation guard provide the second and third layer.
- Spanish coverage is strongest for MST, which publishes a Spanish fares
  page. For the other agencies Spanish answers depend on cross-lingual
  retrieval over English documents, and the parity table in EVALS.md shows
  where that falls short.
- The overall pass count moves by a couple of cases run to run. A handful of
  cases sit at the LLM judge's groundedness/helpfulness decision boundary (and
  the answer model is not perfectly deterministic at temperature 0 on Bedrock),
  so the headline is a band (~113 of 118) rather than a fixed number. The
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
