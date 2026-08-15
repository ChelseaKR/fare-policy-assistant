# 0023 — Evaluation identity and promotion attestation

Date: 2026-07-30. Status: accepted.

## Context

ADR 0021 names a deterministic application release, but the evaluation plane
still identifies only prompt headers and the 12-character compatibility corpus
digest. That is too weak for a release claim:

- whole-case reuse omits required facts, forbidden content, rationale, injected
  history, evaluator code, the structured fact table, and the GTFS files read by
  deterministic checks;
- requested model IDs are recorded, but provider-reported served IDs are not;
- a committed report cannot contain the final commit's source-based
  `release_version`, because committing that report changes the source;
- the operator console selects the newest ignored local run rather than an
  explicitly promoted evaluation; and
- Lambda configuration, the rider runtime, evaluation evidence, and the public
  evidence surface can therefore describe different releases while each looks
  individually plausible.

GTFS inputs have an additional limitation. The legacy fetcher retained selected
extracted files but discarded the ZIP, so no historical ZIP digest can be
reconstructed honestly.

## Decision

Use three related but separate records.

### 1. Stable evaluation context

`fare-assistant.eval-attestation.v1` captures one context before model calls:

- **subject:** source state and exact source/config/content/snapshot/release/
  compatibility identities;
- **evidence:** the complete ordered suite semantics, exact `facts.jsonl`, and
  exact GTFS evaluation inputs;
- **protocol:** prompt byte receipts, requested provider/model/call settings,
  whether judges run, replicate count, and evaluator implementation identity.

Its `context_version` hashes only those stable fields. Wall-clock time, run
status, and promotion decisions are excluded so an identical context can reuse
an identical prior case.

Each case has its own `case_semantics_version` over the complete canonical
post-flatten mapping. The v2 whole-case key hashes that value, the stable
context, judge mode, and replicate count. A legacy record lacks those inputs
and is never reusable.

The GTFS component is initially labelled
`fare-assistant.gtfs-legacy-eval-input.v1` and
`legacy_extracted_only`. It hashes the manifest configuration and every regular
file actually available to the evaluator. It never claims a ZIP digest.
Transactional GTFS will introduce a new schema and naturally invalidate all
legacy case keys.

### 2. Completed evaluation record

The run summary composes the stable context with:

- evaluated time and mode;
- cache, reuse, and replicate state;
- requested and provider-reported served answer/judge model IDs;
- exact results digest;
- parity and regression gate status; and
- promotion eligibility plus explicit ineligibility reasons.

A promotable run must be post-commit, descriptor-verified, full, live,
uncached, unreused, single-replicate, independently judged, and gate-passing.
Credential failure is fatal; it cannot fall back to an offline run.

Committed `EVALS.md`, baselines, and audit datasets remain comparison and
research artifacts. Exact release evaluation is a post-commit CI/deployment
artifact, resolving the source-identity recursion identified in ADR 0021.

### 3. Promotion attestation

`fare-assistant.promotion-attestation.v1` is created only after an immutable
numbered Lambda version exists. It binds:

- the complete logical release tuple;
- AWS artifact digest and numeric function version;
- the exact completed summary bytes, evaluation-attestation digest, and results
  bytes;
- passing gate state; and
- promotion time.

This record is outside the deployment ZIP, avoiding a recursive artifact
digest. Strict parsing rejects duplicate keys, missing or unknown fields,
malformed identities, non-canonical digests, and invalid timestamps.
The builder independently recomputes record count, unique case IDs, every
per-suite pass/total, and the overall scoreboard from `results.jsonl`; a
well-formed but incomplete result set cannot inherit a full-run score.

## Console and evidence semantics

The operator console must compare three observations:

1. stable, unweighted, qualified AWS alias configuration;
2. the rider runtime's verified `/version` response; and
3. the explicitly bundled promotion/evaluation attestation.

It may show a current score only when the complete identities agree and the
evaluation is within its age budget.

- AWS/runtime incoherence or an ambiguous alias is `503 invalid`.
- Missing, non-promotable, failed, cached, offline, partial, or mismatched
  evaluation evidence is `409 invalid`.
- Matching but old evaluation evidence is `409 warning`.
- Exact, fresh agreement is `200 verified`.

These states affect evidence claims, not rider availability. An evidence
artifact aging or becoming unavailable never takes the currently reviewed
policy service offline.

The public Pages surface uses an additional disclosure boundary:
`fare-assistant.public-evidence.v1`. A local export first verifies the exact
summary, results, and promotion files, then emits canonical JSON containing
only release identities, digests, aggregate scores, safe case IDs, and served
model IDs. Raw traces, questions, responses, prompts, rationales, and passages
are not valid manifest fields and never enter the renderer.

Pages deployment is manual and binds three independent inputs: the full trusted
source commit, a full evidence-only commit containing exactly
`public-evidence.json`, and the expected SHA-256 of that file. The workflow
renders from the canonical public manifest, compares every attested runtime
field with the live rider `/version` response before deployment, independently
recomputes freshness from the attested run time, and repeats both checks
afterward. A once-fresh manifest cannot be replayed after its age budget. The
trace-bearing development report is not copied to the public artifact.

## Consequences

- A case expectation, evaluator, fact table, GTFS input, prompt body, model,
  retrieval setting, containment choice, or source/release change invalidates
  reuse at the correct boundary.
- Cache speedups remain available for development, but cannot enter promotion
  evidence.
- Provider routing changes are visible through served model IDs.
- The console no longer treats ignored local files as promoted evidence.
- GTFS remains evaluation/promotion evidence and does not expand rider release
  schema v1.
- Full live promotion evaluation has real latency and model cost. That cost is
  intentional and separately reported; retries may reuse only a strictly
  validated attestation for the same exact release.

## Rejected alternatives

- **Add timestamps to the case key.** This would disable legitimate reuse
  without improving identity.
- **Treat prompt headers as prompt identity.** A body can change without its
  human label changing.
- **Use requested model IDs as served identity.** Provider responses can report
  a different concrete model.
- **Infer a ZIP digest for legacy GTFS.** The original bytes no longer exist.
- **Choose the latest eval directory.** Recency is not approval or release
  agreement.
- **Commit the exact promotion report.** The commit would change the source
  revision it claims to evaluate.
- **Put artifact hash inside the Lambda ZIP.** That creates an impossible
  self-referential digest.

## Follow-on boundary

Transactional GTFS capture and GTFS Scorecard receipts are separate follow-on
work. Scorecard may gate promotion of newly derived GTFS evidence only after
exact official-feed ZIP capture; it is never rider-runtime or fare-policy truth.
