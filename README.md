# Transit Fare Policy Assistant + Evaluation Harness

[![CI](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml)

A small retrieval-augmented assistant that answers rider questions about fare
and reduced-fare policies for five California transit agencies, wrapped in a
public evaluation framework that measures whether it behaves. The eval harness
is the point of this repo; the chatbot exists so the harness has something to
evaluate.

**[Read the latest evaluation report → EVALS.md](EVALS.md)**

## Status: Beta

Deployed and evaluated (see the live demo and EVALS.md below), but not
production-grade: the manual accessibility walkthrough is still pending, the
EN/ES answer-quality gap exceeds this project's own ≤5-point target, and the
judge-calibration sample is smaller than the standard's floor. All three are
tracked in the [Standards conformance](#standards-conformance) table below.
This is, and is meant to be read as, a reference implementation — see the
closing note.

## Standards conformance

Assessed against [`ChelseaKR/portfolio-standards`](https://github.com/ChelseaKR/portfolio-standards)
(pinned version in `.standards-version`; `standards.yml` checks staleness on
every push). "Applies" means the standard's AUTO/REVIEW gates are being worked
toward, not that they all pass yet — see the linked gap for current state.

| Standard | Applies? | State |
|---|---|---|
| Quality & Metrics | Applies | Partial. Coverage gate (90% branch) is green; DORA ledger and AI-capabilities checklist not yet started. No tracking issue filed yet — this row is the gap record until one is. |
| Code Quality | Applies | Partial. `ruff format --check`, pytest strict flags, and `.python-version` landed 2026-07-05; mypy strict mode and ruff's full pinned rule set (`S`, `C90`) are not yet on; no pre-commit config; no CODEOWNERS-enforced review. No tracking issue filed yet. |
| Security & Supply Chain | Applies | Partial. SAST (Semgrep) and secret-scan (gitleaks) are blocking; dependency-vulnerability scanning (`pip-audit`) landed 2026-07-05 (see `security.yml`). No ASVS level declared, no CodeQL, no zizmor, no Scorecard yet. No tracking issue filed yet. |
| CI/CD | Applies | Partial. OIDC-only credentials, SHA-pinned actions, per-job least-privilege permissions (including `corpus-freshness.yml`, fixed 2026-07-05). No branch-ruleset artifact, no CODEOWNERS-enforced review (`CODEOWNERS` file added 2026-07-05; the hosted branch-protection setting itself is a manual, human action — see the 2026-07-05 execution log in the audit folder). No tracking issue filed yet. |
| Release & Versioning | Applies | Partial. `.github/workflows/release.yml` (added 2026-07-10) is tag-triggered on `v*`: checks the tag matches `pyproject.toml`'s version, re-runs `make verify` at the tagged commit, builds sdist+wheel, generates a CycloneDX 1.7 SBOM, attests SLSA build provenance, and creates a GitHub Release with the matching `CHANGELOG.md` section as notes. Nothing is published to a package index (no PyPI project registered, no other repo pins this one), so the GitHub Release is the publish target, not Trusted Publishing — the pipeline still exists so the deployed artifact is traceable to a signed, tested, tagged build. No tracking issue filed yet. |
| Accessibility | Applies | Partial. Merge-blocking structural gate (`web/a11y.py`) is green; the advisory browser pass (pa11y/axe) has not yet graduated to blocking, and the manual screen-reader walkthrough is still pending (`docs/audits/a11y-walkthrough.md`). No tracking issue filed yet. |
| Observability | Applies (Tier: informational/low-traffic demo service — no SLO, no paging). JSON structured logs exist (no PII, test-enforced); no OpenTelemetry, no alerting. This tier declaration is the gap-closer for OBS-21; raising the tier is tracked in `docs/ROADMAP.md` P1-3. | — |
| Internationalization | Applies | Best-conforming standard in this repo: gettext catalogs with 9 merge-blocking gates (`docs/I18N.md`). Known, tracked gap: the disaggregated EN/ES answer-quality delta (~14pp) exceeds the ≤5pp bar — root-caused in `docs/audits/eval-regression-2026-06-30.md`. |
| AI Evaluation | Applies | This is the project's thesis. 128-case harness, versioned prompts, a committed regression baseline, an independent GovChat-Eval audit. Multilingual recovered to its 20/21 baseline in the 2026-07-11 live run. **Currently red**: judge-calibration κ is below the 0.60 floor because nine answer-bound labels became stale after prompt changes; they must be relabeled by humans (`evals/calibration.py`). |
| Documentation | Applies | Partial. This table is new (2026-07-05); ADRs, model card, and CONTRIBUTING exist and are dated. `CHANGELOG.md` added 2026-07-05. No tracking issue filed yet. |
| Responsible Tech Framework | Applies (civic domain touching age/disability/income/veteran status). Misuse-resistance is code-enforced and tested (`src/assistant/guards.py`). No DPIA, AI-risk register, or EU-AI-Act classification yet: the source material for all three already exists in ADR 0004, `SECURITY.md`, and the model card; writing them up is future work. | — |

No GitHub tracking issues are linked above: this pass verified `gh auth
status` succeeds against this repo but did not file issues autonomously (that
write action was outside this remediation pass's scope — see the 2026-07-05
execution log). Until issues exist, the linked doc/file in each row is the
authoritative gap record; open the issues by hand (or ask an agent to, in a
session that's explicitly scoped for it) and replace these notes with links.

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

118 cases across six core suites, each case written against a specific
passage in the corpus and readable by a non-engineer:

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

### Stretch language: Tagalog

Only Spanish is at parity. `evals/suites/stretch_tagalog.yaml` adds 15 more
cases, each mirroring an existing English case, that ask the same questions
in Tagalog — chosen over Chinese, Vietnamese, or Korean because it is
space-delimited Latin script, so the existing tokenizer needs only a
fare-vocabulary lexicon, not a script change, to bridge a query into the
English-only corpus. No agency in the corpus publishes a Tagalog page, so
this suite is deliberately kept out of the core count and the CI smoke gate:
it is a clearly-tagged, non-parity suite that is expected to score well below
English and Spanish, and EVALS.md prints its own "Stretch-language parity
(Tagalog)" table against the same English mirrors the Spanish table uses, so
the gap is a counted number, not a claim. Full details in
`docs/model-card.md`.

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
[`docs/procurement-brief.md`](docs/procurement-brief.md). The security posture,
how to report a vulnerability, and a deployment hardening checklist are in
[`SECURITY.md`](SECURITY.md).

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
DENY`. By default the widget is frameable only same-origin (`frame-ancestors
'self'`); set `FPA_EMBED_ANCESTORS` to a space-separated origin allowlist (the
agency's own domains) to let those sites embed it.

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

A fourth backend, `FPA_PROVIDER=local`, talks to a small model served
locally by [Ollama](https://ollama.com) — no network call, no per-query
cost, for an offline kiosk deployment (EXP-13 in
`docs/ideation/03-expansions.md`). `evals/backend_comparison.py` runs the
same guarded pipeline against `local` and `bedrock` and publishes the
measured delta; see `docs/decisions/0010-local-model-kiosk-backend.md` for
the result (a small model measured well short of the bar — generation does
not ship on the kiosk today).

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

A second, structured evidence source checks the prose corpus against reality:
`make gtfs-fetch` / `make gtfs-check` cross-validate agency fares against
their published GTFS(-Fares) feeds (MST and SBMTD, confirmed live; ADR 0009),
flagging disagreement without ever overriding an answer.

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
benefits-eligibility assistant, and `make template TARGET=<dir>` extracts the
domain-agnostic modules into a starter skeleton for a second domain
assistant, so it can start from this project's audited harness without
forking the repo (`template/MANIFEST.yaml`, `docs/ROADMAP.md` P3-5).

---

Reference implementation. No accounts, no persistence of user queries.
Fare information shown is based on policies published as of the dates in
`corpus/manifest.yaml`; confirm anything time-sensitive with the agency.

MIT licensed (see LICENSE). Corpus snapshots remain the work of their
respective transit agencies.
