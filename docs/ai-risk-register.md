# AI risk register

Reference implementation, dated 2026-07-11. The risks a procurement reviewer
asks about for an LLM system, each with the concrete control that addresses it
and where that control lives in this repo. Likelihood/impact are for the
**demo** as built; a production deployment re-rates them against its own traffic
and stakes.

Scoring: L/M/H for likelihood and impact. "Residual" is the rating after the
listed controls.

| # | Risk | L | I | Controls (where) | Residual |
|---|---|---|---|---|---|
| R1 | **Ungrounded / hallucinated fare or rule** — the model states a fare or requirement not in the corpus | M | H | Every claim must carry a citation resolving to the corpus or the answer is blocked; groundedness judge + deterministic fact-consistency check; the self-test proves the gate catches a planted wrong fare (`evals/checks.py`, `evals/selftest.py`) | L–M |
| R2 | **Eligibility over-claim** — the assistant tells a rider they qualify | L | H | Output guard blocks determination language in EN/ES; refusal suite tests it; system prompt states published-criteria-only posture (`src/assistant/guards.py`, `evals/suites/refusal.yaml`) | L |
| R3 | **Fare-table row misread** — a real number read from the wrong row (e.g. a discount fare given as the general fare) | M | H | Deterministic price-existence check against the corpus fact table catches fabricated prices (proven by the self-test); a real price attributed to the wrong row is owned by the groundedness judge, since deterministic row-binding measured a 15:1+ false-positive rate and was rejected (`docs/decisions/0016-fare-row-binding-not-earned.md`) | M |
| R4 | **Stale corpus** — policy changed since the snapshot | M | M | Every answer shows the fetch date and a staleness note; corpus is versioned and dated; freshness eval suite; corpus-freshness CI workflow | L–M |
| R5 | **Prompt injection** — instructions embedded in a question | M | M | Injection-pattern input guard; the system answers only the fare-policy part; injection cases in the refusal suite (`src/assistant/guards.py`) | L |
| R6 | **Forged conversation history** — a client replays a fabricated prior "answer" as a premise | L | M | Output guard polices every new answer regardless of history; `conv-forged-*` eval cases assert re-grounding; optional `FPA_HISTORY_HMAC_KEY` restricts history to server-signed turns (`SECURITY.md`) | L |
| R7 | **Multilingual quality gap** — Spanish/Tagalog answers weaker than English | M | M | Required Spanish parity suite (mirrored cases); Tagalog is labeled a stretch, cross-lingual test; the independent lexical proxy is tracked and still below target (`docs/I18N.md`) | M |
| R8 | **LLM-judge error** — the judge mis-scores an answer | M | M | Judge model differs from the answer model; judge prompt versioned; both deterministic at temp 0 (probed); human κ calibration in progress (`evals/calibration/`) | M |
| R9 | **PII exposure** — rider volunteers or system retains personal data | M | M | Input guard refuses identifiers pre-retrieval; no content logged or stored; in-memory cache only (see `docs/dpia.md`) | L |
| R10 | **Cost / abuse (denial of wallet)** | L | M | Per-container budget, reserved concurrency, API Gateway throttle, a question-length cap and a request-body cap, IAM scoped to one model; CloudWatch alarms + AWS Budget (ADR 0004) | L |
| R11 | **Insecure browser output** — script injection via answer or citation | L | H | Answer text HTML-escaped; citation fields set as text; links validated to http(s); strict CSP with hashed inline blocks, no `unsafe-inline` (`web/csp.py`, `SECURITY.md`) | L |
| R12 | **Model/provider outage or change** | L | M | Provider-portable via a thin adapter (Bedrock default, Anthropic API switch); pinned model versions (`src/assistant/models.py`) | L |

## Ownership and review

This is a reference implementation with a single maintainer; a production
deployment assigns an owner per row and a review cadence. R3, R7, and R8 are the
open items being actively worked (see `docs/ROADMAP.md` and the eval-remediation
audit). The register is revisited whenever the corpus, prompts, or deployment
shape change — the same trigger as the provenance and calibration gates.
