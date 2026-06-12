# Transit Fare Policy Assistant + Evaluation Harness

A small retrieval-augmented assistant that answers rider questions about fare
and reduced-fare policies for four California transit agencies, wrapped in a
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

70 cases across five suites, each case written against a specific passage in
the corpus and readable by a non-engineer:

| Suite | What it tests |
|---|---|
| groundedness | claims trace to retrieved passages; prices and ages match the documents |
| refusal | PII, prompt injection, determination-seeking, out-of-corpus agencies |
| edge_cases | real eligibility boundaries: 62 vs 65, Medicare vs Medi-Cal, veteran documents |
| multilingual | Spanish parity, measured against mirrored English cases |
| freshness | "as of" disclosure, expired programs, refusal to speculate about future fares |

Scoring combines deterministic checks (citation resolves to the corpus,
forbidden phrases absent, response language matches the question) with an
LLM judge for groundedness and helpfulness. The judge model is different from
the answer model, its prompts are versioned in `prompts/`, and judge output
that fails to parse counts as an error rather than a pass.

A 25-case smoke suite runs in CI on every pull request. The full suite runs
nightly. A drop of more than 2 points on any suite fails the build.

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
(SBMTD), Yolobus, and Sacramento Regional Transit (SacRT), snapshotted with
fetch dates in `corpus/manifest.yaml`. MST's Spanish fares page is included,
which makes part of the multilingual suite a same-language retrieval test and
the rest an honest cross-lingual one. MST and SBMTD are the two agencies live
on Cal-ITP Benefits, so the corpus overlaps with a real eligibility
verification domain.

Unitrans was in the original pilot list; its WAF blocks non-browser clients,
so SacRT was substituted rather than working around the block
(`docs/decisions/0002`).

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
