# Transit Fare Policy Assistant + Evaluation Harness

[![CI](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml)

A small retrieval-augmented assistant that answers rider questions about fare
and reduced-fare policies for five California transit agencies, wrapped in a
public evaluation framework that measures whether it behaves. The eval harness
is the point of this repo; the chatbot exists so the harness has something to
evaluate.

**[Read the latest evaluation report → EVALS.md](EVALS.md)**

## What this assistant will not do

- It never determines anyone's eligibility. It explains published criteria
  ("the published criteria are 65 and older") and leaves the decision to the
  agency. An output guard blocks determination language in English and
  Spanish, and the eval suites test it.
- It never answers without a citation. Every factual claim must trace to a
  retrieved passage from a dated policy snapshot. An uncited answer is blocked
  by the output guard and counted as a critical eval failure.
- It does not collect personal information. Questions containing ID numbers,
  birth dates, or contact details are refused before retrieval runs, and the
  details are not echoed back or logged.
- It does not give medical, legal, or immigration advice, and it says so
  plainly when asked.
- It does not pretend to be current. Answers state the date the underlying
  policy documents were fetched and suggest confirming with the agency.

Each of these rules is enforced in code (`src/assistant/guards.py`) and tested
by the evaluation suites (`evals/suites/`). The model card
(`docs/model-card.md`) describes scope and limits in more detail.

## How it is evaluated

118 cases across six suites, each case written against a specific passage in
the corpus and readable by a non-engineer:

| Suite | What it tests |
|---|---|
| groundedness | claims trace to retrieved passages; prices and ages match the documents |
| refusal | PII, prompt injection, determination-seeking, out-of-corpus agencies |
| edge_cases | real eligibility boundaries: 62 vs 65, Medicare vs Medi-Cal, veteran documents |
| multilingual | Spanish parity, measured against mirrored English cases |
| freshness | "as of" disclosure, expired programs, refusal to speculate about future fares |
| conversation | multi-turn follow-ups: references resolve against prior turns; the guard holds across turns |

Scoring combines deterministic checks (citation resolves to the corpus,
forbidden phrases absent, response language matches the question) with an
LLM judge for groundedness and helpfulness. The judge model is different from
the answer model, its prompts are versioned in `prompts/`, and judge output
that fails to parse counts as an error rather than a pass.

A 25-case smoke suite runs in CI on every pull request. The full suite runs
nightly. A drop of more than 2 points on any suite fails the build.

## Independent audit

The harness above is white-box: its checks know this corpus's doc-ids, the
`guards.py` rules, and the agency-scope contract. As a second, independent
layer, the deployed assistant is also audited by
[GovChat-Eval](https://github.com/ChelseaKR/govchat-eval) — a separate
evaluation project that sees only questions, recorded answers, and declared
ground truth. A system graded only by its author is a weaker claim than one an
outside tool also audits. (This is the same eval engine, and `civic-rag-starter-kit`
the same RAG template, that the rest of the civic-AI family is built on; this
project was built end to end first, and those are the generalization of its
[`docs/adapting.md`](docs/adapting.md) promise.)

`make audit` records the deployed pipeline's answers into a content-hashed
dataset and replays them through GovChat-Eval. Latest run (committed under
[`docs/audits/`](docs/audits/eval-report.md)). Read the table with the note
directly beneath it: the two low scores are the floor of a deterministic lexical
judge, not fabrication, and the explanation is part of the result, not an excuse
for it.

| Suite | Score | Threshold | |
|---|---|---|---|
| adversarial (prompt-injection resistance) | 1.000 | 0.95 | ✅ |
| representational (no determination phrases / PII echoed) | 1.000 | 1.00 | ✅ |
| a11y (accessible chat transcripts) | 1.000 | 1.00 | ✅ |
| accuracy (golden-fact coverage) | 0.920 | 0.90 | ✅ |
| refusal | 0.955 | 0.95 | ✅ |
| multilingual (cross-language anchor fidelity) | 0.667 | 0.85 | ✕ |
| groundedness | 0.040 | 0.90 | ✕ |

Read the two misses as an independent floor, not a contradiction of the
white-box results. GovChat-Eval's committed run uses its **deterministic
lexical judge**, which cannot tell paraphrase or redirect boilerplate from a
fabricated claim — so groundedness floors near zero even though this repo's
LLM-judge groundedness suite is at 100%, and cross-language anchor fidelity is
held to a lexical proxy. The other five suites pass, including the accessibility
and prompt-injection checks. The method, the suite mapping, and the
`--judge llm` path for real signal are in
[`docs/audits/methodology.md`](docs/audits/methodology.md).

The accessibility score above is the automated transcript and structural check.
It is not a sign-off on the lived experience: a manual screen-reader and
keyboard walkthrough is still pending, tracked in
[`docs/audits/a11y-walkthrough.md`](docs/audits/a11y-walkthrough.md) and noted in
the model card. Treat the demo as accessibility-reviewed by automation, not yet
by a person.

For a buyer or IT reviewer who wants the safety, privacy, and testing posture on
one page without reading the code, see
[`docs/procurement-brief.md`](docs/procurement-brief.md).

## Live demo

Try it at <https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com/>. The page
states what the assistant will not do, answers in English or Spanish, and
cites the policy snapshot behind every answer. Questions are answered and
discarded; nothing you type is stored. The serving path is one Lambda behind
an HTTP API with layered cost guards (ADR 0004), deployed by
`infra/deploy.sh`.

For riders with no signal at the stop, `/offline` renders every agency's dated
policy text on one printable page, built from the committed corpus with no model
call (`make offline` writes it locally for inspection).

An agency can embed the assistant in its own fare page with one iframe pointing
at `/embed`:

```html
<iframe src="https://<demo-host>/embed" title="Transit fare policy assistant"
        width="100%" height="520"
        style="border:1px solid #d6d3cb;border-radius:8px"></iframe>
```

`/embed` is the only frameable route: it carries the reference-implementation
notice and the will-not-do line, and is served same-origin so its `/api/ask`
call stays under `connect-src 'self'`. The main page keeps `x-frame-options:
DENY`. By default the widget allows any ancestor for the demo; set
`FPA_EMBED_ANCESTORS` to a space-separated origin allowlist (the agency's own
domains) in a real deployment.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). Snapshots of the corpus are
committed, so the offline path works with no API key and no network:

```sh
make test                                  # unit tests
uv run python -m evals.runner --offline    # full eval, deterministic checks only
uv run python -m assistant.cli --offline "What proof do I need for the veteran fare on MST?"
```

Live runs use Claude on Amazon Bedrock by default, authenticated through the
standard AWS credential chain. The recommended local setup is IAM Identity
Center (SSO) — no long-lived keys on disk:

```sh
aws configure sso                          # once; creates a profile
aws sso login --profile my-profile
AWS_PROFILE=my-profile AWS_REGION=us-west-2 uv run python -m evals.runner --full
uv run python -m assistant.cli "¿Cuánto cuesta el pasaje reducido en Yolobus?"
```

CI authenticates the same way in spirit: GitHub Actions assumes an IAM role
via OIDC federation (`AWS_OIDC_ROLE_ARN` repository variable), so the repo
holds no AWS secrets at all. Without credentials, eval runs fall back to
offline mode automatically.

To use the direct Anthropic API instead, set `FPA_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY`.

To rebuild the corpus from the live agency sites (polite, manifest-driven,
about two minutes because of crawl delays):

```sh
make fetch && make ingest
```

## Corpus

Published fare pages from Monterey-Salinas Transit (MST), Santa Barbara MTD
(SBMTD), Yolobus, Sacramento Regional Transit (SacRT), and Humboldt Transit
Authority (HTA), snapshotted with fetch dates in `corpus/manifest.yaml`. MST's Spanish fares page is included,
which makes part of the multilingual suite a same-language retrieval test and
the rest an honest cross-lingual one. MST and SBMTD are the two agencies live
on Cal-ITP Benefits, so the corpus overlaps with a real eligibility
verification domain.

Unitrans was in the original pilot list; its WAF blocks non-browser clients,
so SacRT was substituted rather than working around the block
(`docs/decisions/0002`).

The corpus has a stable version id, a deterministic hash of its chunk content
and fetch dates (`uv run python -m assistant.corpus`). The `/version` endpoint
reports it, and a deployment can approve a version in `corpus/CHANGELOG.md` and
pin to it with `FPA_PINNED_CORPUS_VERSION`; `/version` then reports whether the
running deploy matches. PDF policies are supported too (text-first, with an OCR
fallback for scans; ADR 0008), so a fare program published as PDF is citable
like an HTML page.

## Layout

```
corpus/          manifest, raw HTML snapshots, processed chunks
src/assistant/   ingest, retrieve (BM25, optional dense), guards, answer, cli
prompts/         versioned system, answer, and judge prompts
evals/           suites (YAML), runner, deterministic checks, judges, report
docs/            model card, ADRs, generated HTML report
```

## Adapting this harness to another domain

The pattern is not specific to transit: a corpus manifest with dated
snapshots, chunked policy text, an answer pipeline with input/output guards,
and YAML cases scored by deterministic checks plus a separate judge model.
`docs/adapting.md` walks through what to change for, say, a
benefits-eligibility assistant.

---

Reference implementation. No accounts, no persistence of user queries.
Fare information shown is based on policies published as of the dates in
`corpus/manifest.yaml`; confirm anything time-sensitive with the agency.

MIT licensed (see LICENSE). Corpus snapshots remain the work of their
respective transit agencies.
