# ADR 0014: Local-model kiosk backend added; NO-GO on generation, measured

Date: 2026-07-08. Status: accepted (backend), NO-GO (kiosk generation use).

## Decision

`src/assistant/models.py` gains a fourth backend, `LocalModel`
(`FPA_PROVIDER=local`), talking to a small model served by
[Ollama](https://ollama.com) on `localhost:11434` — the offline, no-per-query-
cost path EXP-13 in `docs/ideation/03-expansions.md` describes for a kiosk
deployment at a transit center or library with no network dependency. It adds
no new dependency: it uses `httpx`, already required for the Anthropic SDK's
transport, against Ollama's plain HTTP API rather than the `ollama` Python
package.

`evals/backend_comparison.py` runs the identical guarded pipeline —
retrieval, prompt assembly, citation extraction, guards, checks — against
both `bedrock` and `local`, with the judge held fixed at this repo's normal
judge model (Bedrock Sonnet) for both backends' answers, so the measured
delta is the answer-generation backend's, not judge variance layered on top.

**The decision criterion was written into the script before it was ever run**
(see `evals/backend_comparison.py`'s module docstring, unedited since): the
local backend is viable for the kiosk if, relative to Bedrock on the same
cases, (a) overall pass rate does not drop more than 10 points, (b) the
guard-trip rate does not rise more than 5 points, and (c) the refusal suite
does not regress by more than 1 case.

## The evidence

Real run, `uv run python -m evals.backend_comparison` (smoke subset, 25
cases, `us.anthropic.claude-haiku-4-5-20251001-v1:0` vs. `llama3.2:3b` — a
kiosk-appropriate 3B model, not the 30B vision-language model that happened
to already be pulled on the build machine), fixed judge
`us.anthropic.claude-sonnet-4-6`, run 2026-07-08:

| suite | bedrock | local | delta |
|---|---|---|---|
| edge_cases | 100.0% | 0.0% | −100.0 |
| freshness | 100.0% | 25.0% | −75.0 |
| groundedness | 100.0% | 40.0% | −60.0 |
| multilingual | 66.7% | 0.0% | −66.7 |
| refusal | 100.0% | 80.0% | −20.0 |
| **overall** | **23/25 (92.0%)** | **7/25 (28.0%)** | **−64.0** |

Guard-trip rate: 0.0% both backends — the guards did not fire more on local
output; local's failures are wrong or absent answers, not answers the guards
caught and flagged. Full traces: `evals/runs/backend-comparison-<run>/`
(`summary.json` plus one `<backend>-records.jsonl` per backend, gitignored
like every other eval run — regenerate with the command above).

**Go/no-go: NO-GO.** Criterion (a) alone fails by 54 points (64-point drop
against a 10-point limit); (b) and (c) were not the binding constraint.

Representative failures, not cherry-picked:

- **edge-001** (a 62-year-old rider asking about the senior discount):
  `llama3.2:3b` answered that the rider qualifies. The corpus states the
  senior discount applies at 65+. The judge's own words: *"the assistant
  incorrectly [characterizes the rider] as '65 years and older.'"* This is
  the exact failure mode the harness's edge-case suite exists to catch —
  and did — but it is also exactly the failure a kiosk rider would act on
  before anyone reviewed a log.
- **ml-001** (a Spanish fare question): `llama3.2:3b` returned
  `answered_guarded` with no resolvable citation and declined to state the
  fare, where Bedrock cited the fare table directly. The guard did its job
  (nothing false shipped), but the rider got nothing useful either.
- **ground-001**: passed the judges (grounded, helpful) but failed the
  deterministic `language_match` check — answered an English question in
  Spanish. A pipeline-level check catching a backend-level fluency problem,
  not a guard-relevant one.

## Consequences

- The `local` backend ships in `models.py` — it is real, tested
  (`tests/test_models.py`), and a legitimate config choice for a future
  small local model that clears the bar this ADR states. Nothing about the
  adapter itself is provisional.
- **Generation on the kiosk does not ship.** Per EXP-13's own stated
  fallback, the honest choice for a no-network kiosk today is EXP-07's
  no-model guided fare finder (`docs/ideation/...`, retrieval-and-template,
  no generation step to fail this way), not `local` generation. "We
  measured, generation didn't clear it" is the published result, not a
  reason to hide the number.
- This is a smoke-subset (25/121 cases) measurement, intentionally bounded
  for cost and time — it is not a claim that the full suite would land
  differently; a 64-point overall gap and a wrong-eligibility-direction
  failure on the very first edge case is not evidence that needs 121 cases
  to be dispositive. A full-suite run is real follow-on work if a
  materially better local model (larger, or fine-tuned on this domain)
  becomes worth measuring.
- Two small models are pulled for the `local` provider's own answer/judge
  pair (`llama3.2:3b`, `qwen2.5:3b` — see `config._DEFAULT_MODELS["local"]`)
  so `FPA_PROVIDER=local` works standalone through the normal
  `evals/runner.py`, the same as `bedrock` and `anthropic`. The backend
  comparison itself does not use the local judge, for the reason stated
  above.
