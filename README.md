# Transit Fare Policy Assistant + Evaluation Harness

[![CI](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ChelseaKR/fare-policy-assistant/actions/workflows/ci.yml)

A small retrieval-augmented assistant that answers rider questions about fare
and reduced-fare policies for five California transit agencies, wrapped in a
public evaluation framework that measures whether it behaves. The eval harness
is the point of this repo; the chatbot exists so the harness has something to
evaluate.

**[Evaluation evidence hub](https://evals.chelseakr.com/)** ·
**[Live AWS assistant](https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com/)** ·
[Evaluation report in the repository](EVALS.md)

## Status: Beta

Deployed and evaluated (use the two public entrypoints above), but not
production-grade: the manual accessibility walkthrough is still pending, the
judge-calibration sample is 4 scored labels against a floor of 37 (and its
κ is undefined, not the 1.000 published until 2026-08-05), and the
native-Spanish half of the bilingual equity standard has never been measured
at all — 0 of its 28 Spanish answers are rated, and the parity gate that reads
0.0 points cannot see answer quality. All three are tracked in the
[Standards conformance](#standards-conformance) table below. This is, and is
meant to be read as, a reference implementation — see the closing note.

This line used to say the EN/ES answer-quality gap exceeded the project's own
≤5-point target. That was written on 2026-07-05 and is no longer what the
evidence says: the mirrored-case parity gate has read 0.0 points since the
2026-07-12 run, and on 2026-08-05 a mirror-integrity gate established that the
22 pairs it reads that number from really are pairs (three of them were not).
The measured gap is 0.0 points. What remains open is the part that was never
measured — a native-Spanish, not machine-translated, benchmark
(`docs/I18N.md` §7) — and an unmeasured property is not a passing one. That
half is now defined and scaffolded rather than merely named:
`evals/spanish_quality.py` publishes the rubric and emits a census of all 28
Spanish answers from the promoted run, committed blank at
`evals/spanish/native_es_rubric_2026-08-05.jsonl` and rated with
`make spanish-quality`. `EVALS.md` carries the state as **not measured**. The
parity gate compares pass/fail on two answers, and every check behind those
verdicts is satisfied by Spanish of any quality, which is exactly why 0.0
points says nothing about how the Spanish reads.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). Snapshots of the corpus are
committed, so the offline path works with no API key and no network:

```sh
make test                                  # unit tests
uv run python -m evals.runner --offline    # full eval, deterministic checks only
uv run python -m assistant.cli --offline "What proof do I need for the veteran fare on MST?"
```

Live model runs and the other backends are covered in
[Live runs and backends](#live-runs-and-backends).

## Standards conformance

Assessed against [`ChelseaKR/portfolio-standards`](https://github.com/ChelseaKR/portfolio-standards)
(**private repository** — the link returns 404 unless you have access; the
pinned version is in `.standards-version` and `standards.yml` checks staleness on
every push). "Applies" means the standard's AUTO/REVIEW gates are being worked
toward, not that they all pass yet — see the linked gap for current state. The
standard itself is not readable from outside, so treat each row below as this
repo's own claim about its state, evidenced by the files and gates it names,
rather than as something an outside reader can check against the rubric.

| Standard | Applies? | State |
|---|---|---|
| Quality & Metrics | Applies | Partial. Coverage gate (90% branch) is green; DORA ledger and AI-capabilities checklist not yet started. No tracking issue filed yet — this row is the gap record until one is. |
| Code Quality | Applies | Partial. `ruff format --check`, pytest strict flags, and `.python-version` landed 2026-07-05; mypy strict mode and ruff's full pinned rule set (`S`, `C90`) are not yet on; no pre-commit config; no CODEOWNERS-enforced review. No tracking issue filed yet. |
| Security & Supply-Chain | Applies | Partial. SAST (Semgrep) and secret-scan (gitleaks) are blocking; dependency-vulnerability scanning (`pip-audit`) landed 2026-07-05 (see `security.yml`). No ASVS level declared, no CodeQL, no zizmor, no Scorecard yet. No tracking issue filed yet. |
| CI/CD | Applies | Partial. OIDC-only credentials, SHA-pinned actions, per-job least-privilege permissions (including `corpus-freshness.yml`, fixed 2026-07-05). No branch-ruleset artifact, no CODEOWNERS-enforced review (`CODEOWNERS` file added 2026-07-05; the hosted branch-protection setting itself is a manual, human action — see the 2026-07-05 execution log in the audit folder). No tracking issue filed yet. |
| Release & Versioning | Applies | Partial. `.github/workflows/release.yml` (added 2026-07-10) is tag-triggered on `v*`: checks the tag matches `pyproject.toml`'s version, re-runs `make verify` at the tagged commit, builds sdist+wheel, generates a CycloneDX 1.7 SBOM, attests SLSA build provenance, and creates a GitHub Release with the matching `CHANGELOG.md` section as notes. Nothing is published to a package index (no PyPI project registered, no other repo pins this one), so the GitHub Release is the publish target, not Trusted Publishing — the pipeline still exists so the deployed artifact is traceable to a signed, tested, tagged build. No tracking issue filed yet. |
| Accessibility | Applies | Partial. Merge-blocking structural and browser pa11y/axe gates are green, and as of 2026-08-05 the structural gate covers all four public pages rather than the chat page alone — `/embed`, `/offline`, and `/guide` were previously unchecked, and all three passed on the day it was widened. The "Sources" caption is now a heading on both answering surfaces, so screen-reader heading navigation reaches it. The manual screen-reader walkthrough is still pending (`docs/audits/a11y-walkthrough.md`), and nothing above substitutes for it: no screen reader has been used on any of the four pages. |
| Observability | Applies (Tier: informational/low-traffic demo service — no SLO). Privacy-safe JSON records correlate request/model outcomes with Lambda-owned IDs and expose canonical provider/model, token-derived estimated cost, and request/model duration without content or request metadata. Promotion captures the numbered candidate's real log tail and tests the installed CloudWatch filters before moving `live`. Alarms, dashboard, 14-day retention, and the account's $20/month `fare-demo` AWS Budget provide layered backstops; a confirmed SNS subscriber remains operator-supplied. | — |
| Internationalization | Applies | English and Spanish are the supported answer languages. Gettext catalogs for EN/ES/TL have 9 merge-blocking gates (`docs/I18N.md`), but Tagalog remains experimental: its 15-case stretch suite uses cross-lingual retrieval over a corpus with no agency-authored Tagalog source page and is excluded from the production-core release denominator. The Spanish parity delta is 0.0 points over 22 mirror pairs, each of which a merge-blocking mirror-integrity gate holds to the same agency, expected behavior, and required-fact count as the English case it mirrors (added 2026-08-05; it found three malformed pairs, all of which had been reporting parity). Still open: the §7 native-Spanish benchmark has never been run, so the 0.0 covers this repo's own mirrored cases and nothing beyond them. It is now scaffolded, not just named — a published rubric plus a committed, entirely blank census of all 28 Spanish answers (`evals/spanish/native_es_rubric_2026-08-05.jsonl`, `make spanish-quality`), reported in `EVALS.md` as **not measured** and never as a zero; 0 of 28 are rated and 0 of 28 questions are externally sourced. Separately, the independent lexical multilingual proxy remains below threshold at 0.581, computed over a `golden.jsonl` export that still carries the three pre-repair pairings. |
| AI Evaluation | Applies | This is the project's thesis: 186 production-core English/Spanish cases, 15 separately reported experimental Tagalog cases, versioned prompts, a committed regression baseline, and a second-harness GovChat-Eval replay (that harness is a separate project by the same author and is private, so the replay is not a third-party audit). The promoted baseline remains **192/201 (95.5%)** overall and **177/186 (95.2%)** production-core. The latest observed nightly is lower at **190/201 overall and 175/186 production-core**, with the cross-agency gate red, so it has not replaced the baseline. A direct probe confirmed both the answer and judge models are deterministic at temperature 0. Judge calibration is the weakest evidence here and is now labeled as such on the report itself: 4 scored labels against a floor of 37 (10% of the promoted run's 367 judged pairs), and κ is **undefined**, not the 1.000 published until 2026-08-05 — every label that recorded a human/judge disagreement had gone stale, so the surviving sample was the agreeing half and could only report 100%. A floor-sized, failure-first relabeling worksheet is committed at `evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl` (`python -m evals.calibration --worksheet <run_dir>`); it holds 37 unlabeled rows and needs a human. `make relabel` walks those rows offline, showing each one's judge criterion, question, retrieved passages, and answer, and recording the reviewer's verdict and reason; it never proposes a verdict and withholds the judge's own call until after the reviewer has given theirs. |
| Documentation | Applies | Partial. This table is new (2026-07-05); ADRs, model card, and CONTRIBUTING exist and are dated. `CHANGELOG.md` added 2026-07-05. No tracking issue filed yet. |
| Responsible-Tech Framework | Applies (civic domain touching age/disability/income/veteran status). Misuse-resistance is code-enforced and tested (`src/assistant/guards.py`). The three governance artifacts now exist, synthesized from ADR 0004, `SECURITY.md`, and the model card: a DPIA (`docs/dpia.md`), an AI risk register (`docs/ai-risk-register.md`), and an EU-AI-Act classification (`docs/eu-ai-act-classification.md`) — the last of which shows the "never determine eligibility" invariant is what keeps the system below the Annex III high-risk line. | — |

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

186 production-core English/Spanish cases, plus 15 explicitly experimental
Tagalog stretch cases, across nine suites. Each case is written against a
specific passage in the corpus and readable by a non-engineer. The 201-case
research total includes 30 counterfactual sensitivity variants; the Tagalog
cases are reported separately and do not contribute to the production release
denominator.

The harness is validated beyond its own scoreboard: a defect-injection self-test
proves the gate catches planted bugs (`make eval-selftest`), a coverage map
checks no corpus provision goes untested (`make coverage`,
`docs/eval-coverage.md`), and a robustness report gives confidence intervals and
a leave-one-suite-out jackknife (`make robustness`, `docs/eval-robustness.md`).
The rendered report and the improvement curve publish to the public
[evaluation evidence hub](https://evals.chelseakr.com/) via the manual `Pages`
workflow.

The suites:

| Suite | What it tests |
|---|---|
| groundedness | claims trace to retrieved passages; prices and ages match the documents |
| refusal | PII, prompt injection, determination-seeking, out-of-corpus agencies |
| edge_cases | real eligibility boundaries: 62 vs 65, Medicare vs Medi-Cal, veteran documents |
| multilingual | Spanish parity, measured against mirrored English cases (each pair is gate-checked to be one question in two languages) |
| freshness | "as of" disclosure, expired programs, refusal to speculate about future fares |
| conversation | multi-turn follow-ups: references resolve against prior turns; the guard holds across turns |
| cross_agency | one answer attributes facts to multiple agencies correctly |
| sensitivity | minimal-pair boundaries must change the answer when policy changes |
| stretch_tagalog | measured Tagalog gap over the English-only corpus |

Scoring combines deterministic checks (citation resolves to the corpus,
forbidden phrases absent, response language matches the question) with an
LLM judge for groundedness and helpfulness. The judge model is different from
the answer model, its prompts are versioned in `prompts/`, and judge output
that fails to parse counts as an error rather than a pass.

A 26-case smoke suite runs in CI on every pull request. The full suite runs
nightly. A drop of more than 2 points on any suite fails the build.

Spanish parity is reported as the pass-rate delta between each Spanish case and
its English mirror, and a run aborts before its first model call if any
declared mirror is not one: a mirror must be the same agency, the same expected
behavior, and carry at least as many required facts as the case it mirrors.
That gate was added on 2026-08-05 after it found three of the 22 pairs were not
pairs, and the parity delta had been reporting 0.0 points across all three.
Repairing them left the delta at 0.0 points over 22 verified pairs; the number
did not move, but until then it was not measuring what it claimed to.

Both are served from a content-keyed cache of answer and judge calls, keyed on
the rendered prompt text, so a change that cannot alter an answer is not paid
for twice. Only the model call is cached: the deterministic checks, the
regression gate, and the parity gate re-execute on every run. One nightly a
week (Monday) bypasses the cache to re-measure the provider directly. See
`docs/decisions/0022-persisted-eval-cache-and-weekly-cold-run.md`.

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

## Second-harness audit

This section leads with an audit, not a victory lap, on purpose. An agency is
liable for what its chatbot tells a rider: a Canadian tribunal held an airline
responsible for fare advice its bot invented, and a New York City business
chatbot gave advice to break the law and stayed live for months. A wrong fare
or eligibility line here would be the agency's problem, not a demo footnote, so
the honest posture is to show the outside floor first. Two scores in the table
below sit near zero. They are the floor of a deterministic lexical judge that
cannot tell a paraphrase or a redirect from a fabricated claim, not evidence of
fabrication; the note under the table explains exactly why, and that note is
part of the result.

The harness above is white-box: its checks know this corpus's doc-ids, the
`guards.py` rules, and the agency-scope contract. As a second layer, the
deployed assistant's recorded answers are replayed through
[GovChat-Eval](https://github.com/ChelseaKR/govchat-eval) — a separate
evaluation project, with its own suites and its own judge, that sees only
questions, recorded answers, and declared ground truth. (This is the same eval
engine, and `civic-rag-starter-kit` the same RAG template, that the rest of the
civic-AI family is built on; this project was built end to end first, and those
are the generalization of its [`docs/adapting.md`](docs/adapting.md) promise.)

Be precise about how independent that is. The second harness is genuinely
separate code with a different scoring model, and it is blind to this system's
internals, which is why it finds things the white-box suites cannot. It is also
written by the same author and is **not public**: that GitHub link 404s for
anyone without access, `make audit` needs a local clone at `../govchat-eval`,
and the CI audit job is scheduled-only and skipped on pull requests for the same
reason. So this is a second-harness replay of committed answers, not a
third-party audit, and nobody outside can rerun it today. What an outside reader
can check right now is the input and the output: the recorded dataset
(`evals/govchat/golden.jsonl`, content-hashed) and the committed report under
[`docs/audits/`](docs/audits/eval-report.md).

`make audit` records the deployed pipeline's answers into that content-hashed
dataset and replays them through GovChat-Eval. Read the table with the note
directly beneath it: several low scores are the floor of a deterministic lexical
judge, not fabrication, and the explanation is part of the result, not an excuse
for it.

| Suite | Score | Threshold | |
|---|---|---|---|
| adversarial (prompt-injection resistance) | 1.000 | 0.95 | ✅ |
| representational (no determination phrases / PII echoed) | 0.892 | 1.00 | ✕ |
| a11y (accessible chat transcripts) | 1.000 | 1.00 | ✅ |
| accuracy (golden-fact coverage) | 0.920 | 0.90 | ✅ |
| refusal | 0.923 | 0.95 | ✕ |
| multilingual (cross-language anchor fidelity) | 0.581 | 0.85 | ✕ |
| groundedness | 0.087 | 0.90 | ✕ |

Read the misses as an independent floor and a visible expansion cost, not a contradiction of the
white-box results. GovChat-Eval's committed run uses its **deterministic
lexical judge**, which cannot tell paraphrase or redirect boilerplate from a
fabricated claim — so groundedness floors near zero even though this repo's
LLM-judge groundedness suite is at 93.1%, and cross-language anchor fidelity is
held to a lexical proxy. Accuracy now clears its threshold; refusal,
multilingual, representational, and groundedness remain below theirs on the
refreshed 195-item export. Adversarial and accessibility remain green. The method,
the suite mapping, and the
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

## Live demo and evidence

These are two distinct public surfaces:

- **[Evaluation evidence hub](https://evals.chelseakr.com/):** generated
  scoreboards, representative failures, trend history, and governance evidence.
- **[Live AWS assistant](https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com/):**
  the rider-facing system those evaluations exercise.

The live assistant states what it will not do, supports English and Spanish,
and cites the dated policy snapshot behind every answer. Tagalog behavior is
experimental, evaluated only as a 15-case stretch over a corpus with no
agency-authored Tagalog source page; it is not a supported production language.
The assistant does not read the agencies' live websites when a rider asks a
question. Questions and conversation history are processed transiently; their
raw text is not logged or used as a cache key. Successful answer payloads may
remain in a bounded in-memory cache until the serverless container is recycled.
Refused, guarded, or personal-information-like inputs are not cached.

The assistant's "How this assistant is tested" panel links to the separate
evidence hub, so a reviewer can move between the system and its evidence
without confusing the two deployments. If you are walking someone through the
project, `docs/DEMO-SCRIPT.md` is a three-minute script: the hook, a few
rehearsed queries that show grounded citations and the refusal to determine
eligibility, and the honest-failures move.

For riders with no signal at the stop, `/offline` renders every agency's dated
policy text on one printable page, built from the committed corpus with no model
call. The page displays the earliest and latest fetch dates represented so the
snapshot window cannot be mistaken for live agency data (`make offline` writes
it locally for inspection).

For riders who would rather browse than type — low signal, low literacy, or a
preference for forms over chat — `/guide` is a zero-model-call, statically
pre-rendered "which fare applies to me" walkthrough: choose an agency, then a
published fare category, to reach the criteria, price, proof, and next step.
It has no input fields on purpose and never determines eligibility; it only
shows saved copies of the agency's published text, verbatim, with a source link
and the page's earliest-to-latest snapshot window
(`make guide` writes it locally for inspection).

An agency can embed the assistant in its own fare page with one iframe pointing
at `/embed`:

```html
<iframe src="https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com/embed"
        title="Transit fare policy assistant"
        width="100%" height="520"
        style="border:1px solid #d6d3cb;border-radius:8px"></iframe>
```

`/embed` is the only frameable route: it carries the reference-implementation
notice and the will-not-do line, and is served same-origin so its `/api/ask`
call stays under `connect-src 'self'`. The main page keeps `x-frame-options:
DENY`. By default the widget is frameable only same-origin (`frame-ancestors
'self'`); set `FPA_EMBED_ANCESTORS` to a space-separated origin allowlist (the
agency's own domains) to let those sites embed it.

## Live runs and backends

The offline path above needs no credentials.
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
measured delta; see `docs/decisions/0014-local-model-kiosk-backend.md` for
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

None of that fare text belongs to this project. It is each agency's copyrighted
work, snapshotted so a dated evaluation can be re-run against what it was scored
on, and it is carved out of this repository's MIT License in
[`NOTICE`](NOTICE). [`corpus/LICENSE-NOTE.md`](corpus/LICENSE-NOTE.md) states in
plain English whose it is, what you may and may not assume about it, and where
each agency's own site and terms of use are. `corpus/manifest.yaml` records the
robots/Content-Signal review that governed fetching separately from each
agency's redistribution terms, because those are different questions.

The corpus keeps its stable legacy version ID for deployed pins and existing
clients, and now also reports a full `content_version` over every
behavior-relevant chunk field. Source-complete schema-2 archives add a separate
`snapshot_version` over content plus the verified fetch URL, date, status,
format, raw digest, and byte count; they are staged, revalidated, and atomically
published with the exact source bytes (`docs/decisions/0020`). The `/version`
endpoint still compares `FPA_PINNED_CORPUS_VERSION` against the compatibility
ID during the additive rollout. PDF policies are supported too (text-first,
with an OCR fallback for scans; ADR 0008), so a fare program published as PDF
is citable like an HTML page.

A second, structured evidence source checks the prose corpus against reality:
`make gtfs-fetch` transactionally captures exact, SHA-256-receipted GTFS ZIPs;
`make gtfs-check` cross-validates agency fares against the atomically selected
set (MST and SBMTD, confirmed live; ADRs 0011 and 0024),
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
