# Independent audit methodology

This directory holds an **independent** audit of the deployed assistant,
produced by [GovChat-Eval](https://github.com/ChelseaKR/govchat-eval) — a
separate evaluation project (the `govchat-eval` CLI). It complements, and does
not replace, this repo's own evaluation in [`../../EVALS.md`](../../EVALS.md).

## Why two evaluations

This repo's harness (`evals/`) is **white-box**. Its checks know the internals:
that a citation must resolve to a doc-id in this corpus, that `guards.py`
forbids determination language, that the cited agency must match the question's
scope. That is the right tool for tuning the system against its own
requirements, and the 51→96 improvement curve in the git history is the record
of doing so.

GovChat-Eval is **black-box**. It sees only the question, the recorded answer,
and declared ground truth, and applies its own suites and judge with no
knowledge of how the assistant works. A system graded only by its author is a
weaker claim than one an outside tool also audits — that second, independent
pass is the credibility this directory adds. It also contributes suites this
repo's harness does not have a native form of (notably claim-level groundedness
entailment and cross-language anchor fidelity).

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
- **Accuracy overlap.** GovChat-Eval's accuracy suite is lexical
  fact-containment, close in spirit to this repo's `required_facts` check, so
  it is not a strongly independent signal. The independence lives in the
  groundedness, multilingual, and refusal suites and in the outside framing.
