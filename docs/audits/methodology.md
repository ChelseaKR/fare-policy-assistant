# Second-harness audit methodology

This directory holds a **second-harness** audit of the deployed assistant:
recorded answers, replayed and re-scored by
[GovChat-Eval](https://github.com/ChelseaKR/govchat-eval) — a separate
evaluation project (the `govchat-eval` CLI), **private as of 2026-08-12, so that
link 404s without access**. It complements, and does not replace, this repo's
own evaluation in [`../../EVALS.md`](../../EVALS.md).

The heading used to read "independent audit". The word was doing more work than
the setup supports, so it now says what is actually true. The harness is
separate code with its own suites and its own judge, and it is blind to this
system's internals, which is where its value is. It is not a third party: it was
written by the same author, it is not published, `make audit` needs a local
clone at `../govchat-eval`, and the CI job that runs it is scheduled-only and
skipped on pull requests while the harness stays private. Read the numbers below
as a second opinion from a different tool, not as an outside party's finding,
and note that nobody outside this repo can rerun them today. What is checkable
from outside is the recorded dataset (`evals/govchat/golden.jsonl`, with a
`.sha256` sidecar) and the committed report beside this file.

## Why two evaluations

This repo's harness (`evals/`) is **white-box**. Its checks know the internals:
that a citation must resolve to a doc-id in this corpus, that `guards.py`
forbids determination language, that the cited agency must match the question's
scope. That is the right tool for tuning the system against its own
requirements, and the 51→96 improvement curve in the git history is the record
of doing so.

GovChat-Eval is **black-box**. It sees only the question, the recorded answer,
and declared ground truth, and applies its own suites and judge with no
knowledge of how the assistant works. A system graded only by the harness tuned
against it is a weaker claim than one a second, blind harness also grades, and
that second pass is what this directory adds. It also contributes suites this
repo's harness does not have a native form of (notably claim-level groundedness
entailment and cross-language anchor fidelity). What it does not add, until the
harness is public, is anything an outside reader can rerun.

This mirrors the wider civic-AI family: GovChat-Eval is the shared audit
engine, and `civic-rag-starter-kit` is the reference RAG template both it and
this project relate to. This project was built end to end first; the starter
kit and the eval engine are the generalization of exactly the
"adapt-this-to-your-domain" promise in `docs/adapting.md`.

## How the audit runs

`make audit` records the deployed pipeline's answers (same code, corpus, and
pinned Bedrock model the Lambda serves) into a content-hashed
`evals/govchat/golden.jsonl`, then replays them through GovChat-Eval with the
`scripted` target. Recording is the one live step; the committed dataset is
then byte-reproducible and runs offline, matching the family's
record-then-replay pattern. The dataset carries a `.sha256` sidecar, so a
tampered or wrong-version file fails closed rather than evaluating silently.

Replaying it requires the harness, which is the part outsiders do not have:
`make audit` fails fast unless a clone exists at `../govchat-eval` (override with
`EVAL_HARNESS=<path>`), and CI's audit job is scheduled-only for the same reason.

The dataset quotes agency fare text in each row's `sources[]`. Its per-row
`license` field states that this project grants no rights over that text; see
[`../../corpus/LICENSE-NOTE.md`](../../corpus/LICENSE-NOTE.md). Correcting that
note later is `make audit-restamp-license`, which rewrites the note and nothing
else, so recorded answers and the provenance header stay as they were recorded.

## Suite mapping

This repo's five suites map onto GovChat-Eval's as follows:

| This repo | GovChat-Eval | Ground truth used |
|---|---|---|
| groundedness | groundedness | retrieved corpus passages as `sources` (English answered cases) |
| edge_cases | accuracy | `required_facts` → `expected_facts` |
| refusal | refusal | `refuse_redirect` → `should_refuse` |
| multilingual | multilingual | `mirror_of` → `pair_id`, English mirror as the reference anchor |
| freshness | accuracy / refusal | by expected behavior (no native freshness suite — see Gaps) |

Determination phrases and PII strings from `forbidden_content` become
representational `forbidden_terms` (must-not-appear). Regex `required_facts`
are converted to the literal substring they matched in the recorded answer, so
a fact this repo's own check found present stays testable for the independent
lexical check; a pattern that did not match falls back to a readable literal so
the gap is still flagged.

## What the numbers do and do not mean

GovChat-Eval's **default judge is deterministic and lexical** (token overlap,
with negation and figure guards). That makes the audit reproducible and
CI-able, but it is a floor, not a benchmark:

- Groundedness here splits the answer into claims and checks lexical
  entailment against the retrieved passages. Boilerplate claims ("contact the
  agency directly") and paraphrase will score as unsupported even when the
  substance is grounded. Read low deterministic groundedness as "worth a human
  look," not "fabricated."
- Multilingual fidelity checks that figures and program names survive
  translation (anchor preservation), not full meaning equivalence.

For real signal, run the LLM judge: `make audit` with
`govchat-eval run --judge llm` (Anthropic API; a different model family from
this repo's Bedrock answer model, so the judge stays independent of the system
under test). The deterministic report is what is committed here because it is
reproducible without credentials.

## Gaps, stated plainly

- **Freshness** has no native GovChat-Eval suite. Those ten cases ride the
  accuracy and refusal suites (their "as of" dates and expired-program dates
  are real golden facts), but the specific behavior this repo cares about —
  disclosing the snapshot date and declining to speculate about the future —
  is only fully exercised by this repo's own freshness suite.
- **a11y** now runs: every item carries an accessible HTML transcript of its
  turn (`govchat_export.render_transcript`), which GovChat-Eval's structural
  checker verifies (declared language, heading order, no uncaptioned images or
  controls, inline contrast). It is a transcript-structure check, complementary
  to this repo's own `web/a11y.py` gate on the live page; neither replaces a
  manual screen-reader pass.
- **bias** stays omitted: this domain dataset declares no fairness segments,
  and a selected-but-inapplicable suite fails closed.
- **Accuracy overlap.** GovChat-Eval's accuracy suite is lexical
  fact-containment, close in spirit to this repo's `required_facts` check, so
  it is not a strongly independent signal. The independence lives in the
  groundedness, multilingual, and refusal suites and in the outside framing.
