# Model Card — Transit Fare Policy Assistant

Reference implementation, not a product. Last updated 2026-06-12.

## Purpose

Answers rider questions about fares, passes, and reduced-fare programs for
four California transit agencies: Monterey-Salinas Transit (MST), Santa
Barbara MTD (SBMTD), Yolobus, and Sacramento Regional Transit (SacRT). It
explains published policy. It does not decide anything about any person.

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
BM25 retrieval (optional dense retrieval behind a flag), top-6 passages with
an agency filter when the question names one. The answer model writes from
retrieved passages only and cites them inline. Input guards run before
retrieval (PII, scope, injection patterns); output guards block determination
language and uncited answers, replacing them with a refusal that points to the
agency.

Models are pinned in `src/assistant/config.py`. Default backend is Claude on
Amazon Bedrock (`anthropic.claude-haiku-4-5` for answers,
`anthropic.claude-sonnet-4-6` as judge); the direct Anthropic API is
available behind a config switch.

## Data

Eleven public web pages fetched 2026-06-12 with an identified user agent,
honoring robots.txt and crawl delays; URLs, dates, and license notes in
`corpus/manifest.yaml`. No user data is collected, stored, or used anywhere
in the system.

## Evaluation

70 cases across groundedness, refusal, edge-case, multilingual, and freshness
suites; method and current scores in [EVALS.md](../EVALS.md). Deterministic
checks run on every case; LLM-judge scores apply to live runs. Judge
disagreement is spot-checked by hand on a 10% sample and human-judge agreement
is recorded in the report (protocol in `evals/judges.py`).

Known limits found by the harness so far:

- BM25 absolute scores do not reliably separate out-of-corpus questions from
  in-corpus ones, so low-confidence refusal cannot rest on a score threshold
  alone (see `docs/decisions/0001`). The system prompt and the
  missing-citation guard provide the second and third layer.
- Spanish coverage is strongest for MST, which publishes a Spanish fares
  page. For the other agencies Spanish answers depend on cross-lingual
  retrieval over English documents, and the parity table in EVALS.md shows
  where that falls short.

## Escalation

When the corpus lacks an answer, retrieval confidence is low, or a guard
trips, the assistant says so and points to the agency's customer service or
511.org. It is designed to fail toward "ask the agency," never toward a
guess.

## Freshness

Fare policy changes. Every answer carries the snapshot date of its sources,
and the corpus manifest records fetch dates per document. Snapshots should be
refreshed (and evals re-run) before any renewed use.
