# Procurement and data-handling brief

One page for a buyer, an IT reviewer, or a procurement officer who has been told
to ask every vendor "what is your AI story." It is written to be read without
reading the code, and every claim here points to the file that backs it so a
skeptic can check it. This is a reference implementation, not a product or a
service offering; the brief describes how the artifact behaves and how it was
tested.

Last updated 2026-07-17.

## What it is in one paragraph

A retrieval-augmented assistant that answers rider questions about published
fare and reduced-fare policy for ten California transit agencies (MST, SBMTD,
Yolobus, SacRT, HTA, e-tran, SCMTD, SolTrans, FAX, VTA), in English or
Spanish, with a citation on every answer.
The headline deliverable is the evaluation harness around it: 118 graded cases,
deterministic safety checks, an LLM judge held to a different model than the one
being graded, and a second, blind harness that re-scores the recorded answers
(that harness is a separate project by the same author and is not public, so it
is a second opinion rather than a third party's). The assistant
exists so the harness has something to measure.

## What it will not do, and how that is enforced

| Commitment | Enforcement | Where to check |
|---|---|---|
| Never decides a person's eligibility. It explains published criteria and routes the decision to the agency. | Output guard blocks determination language in English and Spanish; eval suites assert the same rules so a regression fails the build twice. | `src/assistant/guards.py`, `evals/suites/refusal.yaml` |
| Never answers without a citation that resolves to a dated policy snapshot. | Output guard blocks an uncited answer and replaces it with a refusal that points to the agency. | `src/assistant/guards.py`, `evals/suites/groundedness.yaml` |
| Does not collect personal information. ID numbers, birth dates, and contact details are refused before retrieval and never echoed or logged. | Input guard runs before the model; the deployed handler logs only response kind, language, and timing. | `src/assistant/guards.py`, `web/handler.py` |
| No medical, legal, or immigration advice. | Input guard redirects these topics to a qualified contact. | `src/assistant/guards.py` |
| Does not pretend to be current. Every answer carries the snapshot date of its sources. | Answer prompt and output check require an "as of" disclosure; the UI shows how long ago policies were fetched. | `prompts/`, `web/index.html` |

These are the hard limits in [`CLAUDE.md`](../CLAUDE.md). They are design
constraints, not configuration; the project does not ship a switch that relaxes
them.

## Data handling and privacy

- No rider data is collected, stored, or used for training. The corpus is public
  agency web pages; rider questions are answered and discarded.
- The deployed demo persists nothing a rider types. Request logs carry only the
  response kind, the language, and timing, so abuse stays visible without keeping
  content. See ADR 0004 (`docs/decisions/0004-demo-deploy.md`) and the handler
  (`web/handler.py`).
- The corpus is fetched politely: an identified user agent, robots.txt and crawl
  delays honored, snapshots committed and dated. Provenance per document is in
  `corpus/manifest.yaml`.
- Optional privacy-safe feedback records only a thumbs verdict, the response
  kind, and the corpus version. Never the question or the answer.

## How it is tested, in plain terms

Two independent layers, on purpose.

1. **The project's own harness (white-box).** 270 cases across nine suites
   (groundedness, refusal, edge cases, multilingual, freshness, multi-turn
   conversation, cross-agency, counterfactual sensitivity, and stretch-language
   Tagalog). Each case is a YAML record a non-engineer can read: the
   question, the agency scope, the expected behavior, and the facts or citations
   it must contain. Scoring combines deterministic checks (citation resolves,
   forbidden phrases absent, language matches, "as of" present) with an LLM judge
   for groundedness and helpfulness. The judge model differs from the answer
   model, the judge prompts are versioned, and unparseable judge output counts as
   an error, not a pass. Current scores, the per-run cost, and judge-versus-human
   agreement are in [`EVALS.md`](../EVALS.md).

2. **An outside audit (black-box).** The deployed pipeline's answers are recorded
   into a content-hashed dataset and replayed through GovChat-Eval, a separate
   project that sees only questions, recorded answers, and declared ground truth.
   A tool graded only by its author is a weaker claim than one an outside tool
   also checks. The committed audit and its method are in
   [`docs/audits/`](audits/methodology.md).

### Reading the audit scores honestly

The committed GovChat-Eval run uses its deterministic lexical judge. That judge
cannot tell a faithful paraphrase or a redirect from a fabricated claim, so its
groundedness number floors near zero even though the white-box LLM-judge
groundedness suite scores at the top of its range, and its cross-language number
is held to a lexical proxy. The other suites (prompt-injection resistance, no
determination language or PII echoed, accessibility of transcripts, golden-fact
accuracy, refusal) pass. The point of committing the low lexical number rather
than hiding it is the project's whole thesis: show the method and its limits, do
not cherry-pick. The `--judge llm` path produces the real signal and is
documented in the audit methodology.

## Accessibility status (read before any "production-ready" claim)

The demo page targets WCAG 2.2 AA. A pure-Python structural gate (`web/a11y.py`)
runs in CI on every change: page language, labeled controls, heading order, link
text, zoom not disabled, and a 24px minimum target size. An advisory pa11y/axe
pass cross-checks computed contrast and ARIA. The page also offers reader text-
size and high-contrast controls. What automation cannot certify is the lived
experience: a manual screen-reader and keyboard walkthrough is a pending human
step recorded in the model card, and it should be done and recorded before the
demo is presented as production-ready. The brief states this plainly rather than
implying a sign-off that has not happened.

## Operational posture

- Serving path: one AWS Lambda behind an HTTP API, with layered cost guards
  (reserved concurrency, a per-container request budget, a question-length cap,
  and a pinned answer-token ceiling). Deployed by `infra/deploy.sh`.
- Models are pinned in `src/assistant/config.py`. Default backend is Claude on
  Amazon Bedrock via the standard AWS credential chain; the direct Anthropic API
  is available behind a config switch. CI authenticates by OIDC role assumption,
  so the repository holds no cloud secrets.
- Reproducibility: `make eval` regenerates the report end to end; each run records
  its model and prompt versions and its exact token usage and estimated cost.
- A full eval run costs a few dollars. The latest run's exact figure is in
  `EVALS.md`.

## Known limits, stated up front

- Spanish is at parity with English where an agency publishes a Spanish page
  (MST); for the others, Spanish answers rely on cross-lingual retrieval over
  English documents, and the parity table in `EVALS.md` shows where that falls
  short. Only English and Spanish are covered today.
- A handful of cases sit on the LLM judge's decision boundary, so the headline
  pass count is a band (about 113 of 118), not a fixed number. The deterministic
  safety checks (no determination language, citation present, PII not echoed) do
  not vary.
- The corpus is ten agencies and a fixed snapshot date. Fare policy goes stale;
  the answer says so, and snapshots must be refreshed and evals re-run before any
  renewed use.

## NIST AI RMF crosswalk

Some reviewers organize vendor review around the NIST AI Risk Management
Framework (AI RMF 1.0) and its four functions. The table maps this repository's
existing artifacts onto those functions so a reviewer can find the evidence
quickly. It is a self-assessment pointing at files, not a certification; no
conformity assessment has been performed, and the framework itself is
voluntary. Each row claims only that the named artifact exists and does what
its own text says.

| Function | What this project does | Where to check |
|---|---|---|
| **Govern** | The hard limits (no eligibility determinations, no PII collection, citation required) are design constraints recorded in the root instruction file and enforced in code and CI, not policies on a shelf. Risk ownership and decisions are written down: an AI risk register, a DPIA, an EU AI Act self-classification, security reporting terms, and a decision log that keeps its negative results instead of deleting them. | [`CLAUDE.md`](../CLAUDE.md), [`docs/ai-risk-register.md`](ai-risk-register.md), [`docs/dpia.md`](dpia.md), [`docs/eu-ai-act-classification.md`](eu-ai-act-classification.md), [`SECURITY.md`](../SECURITY.md), [`docs/decisions/`](decisions/) |
| **Map** | Scope, intended use, affected riders, and known limits are stated before any capability claim: what the assistant is for and not for, which agencies and languages it covers, where each corpus document comes from and when it was fetched, and what stays out of scope. | [`docs/model-card.md`](model-card.md), [`docs/PROJECT-SCOPE.md`](PROJECT-SCOPE.md), `corpus/manifest.yaml`, the "Known limits" section above |
| **Measure** | The headline deliverable. A graded eval harness combines deterministic safety checks with a versioned LLM judge held to a different model than the one being graded; judge-versus-human calibration is reported with its sample size; the headline number carries confidence intervals and a leave-one-suite-out check; counterfactual minimal pairs probe eligibility boundaries; a regression gate and a provenance gate block merges; and a second, blind harness re-grades recorded answers (that harness is not public, so its report is a second opinion, not a third-party audit). | [`EVALS.md`](../EVALS.md), `evals/` (suites, `calibration.py`, `robustness.py`, `check_report_regression.py`, `provenance.py`), [`docs/eval-robustness.md`](eval-robustness.md), [`docs/audits/methodology.md`](audits/methodology.md) |
| **Manage** | Risks are handled in operation, not only at review time: input and output guards run on every request; corpus staleness has a budget, and a scheduled refresh loop opens a reviewable pull request on real drift; spend and error alarms are provisioned by the deploy (subscribing a human endpoint is an operator step); the rate limit holds across containers; models and prompts are pinned and versioned. | `src/assistant/guards.py`, `.github/workflows/corpus-freshness.yml`, [`infra/README.md`](../infra/README.md), `src/assistant/config.py`, `prompts/` |

What this mapping does not cover: the manual assistive-technology walkthrough
is still pending (see the accessibility section above), and no external party
has reviewed this crosswalk.

## What a buyer would do next

This is a reference implementation to learn from or adapt, not a managed service.
To evaluate it: read `EVALS.md` and the audit report, skim `evals/suites/` to see
the cases in plain YAML, and read the model card (`docs/model-card.md`) for scope
and limits. To adapt the harness to another domain (for example a
benefits-eligibility assistant), `docs/adapting.md` describes what changes and
what stays.
