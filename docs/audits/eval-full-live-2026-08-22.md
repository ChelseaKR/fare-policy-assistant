# Full live evaluation, 2026-08-22 — measured, not promoted

This records a full live run that is **not** promoted. `EVALS.md` still carries
the 2026-07-12 run, because a promoted report has to hold the bilingual parity
gate and this one does not (`tests/test_parity_gate.py::test_committed_evals_md_holds_the_parity_gate`).
The numbers are here so the measurement exists in the repository rather than
only in a CI artifact that expires.

## Provenance

| | |
|---|---|
| Run | `2026-08-22T13:12:46Z` (`evals/runs/20260822T131246Z`) |
| Mode | full, live, **cold** (`--refresh-cache`) — 369/369 answer calls and 730/730 judge calls went to the provider; cache hit rate 0.0% by design |
| Provider | AWS Bedrock, `anthropic.AnthropicBedrock`, credentials from the AWS chain, `us-west-2` |
| Answer model | `us.anthropic.claude-haiku-4-5-20251001-v1:0` (served `claude-haiku-4-5-20251001`) |
| Judge model | `global.anthropic.claude-sonnet-4-6` (served `claude-sonnet-4-6`) |
| Prompts | system v22 2026-08-16 · answer_user v7 2026-07-11 · judge_groundedness v3 2026-08-16 · judge_helpfulness v3 2026-07-02 |
| Corpus | `10deac978967` (18 agencies, 52 documents, 301 chunks) |
| Commit | `a4b3e0a`, working tree clean |
| Cost | $8.5006 for 3,898,166 tokens — answer $2.8829, judge $5.6177 |
| Duration | 813s at 4 workers |

On the models: Bedrock has no entitlement for `claude-sonnet-5` on this
account, so `claude-sonnet-4-6` is the judge. The answer model stays on the
`us.` profile because that is the exact inference profile `infra/deploy.sh`
grants the Lambda in IAM; the judge never runs inside the Lambda, so it uses
the `global.` profile, which is the same model at list price rather than the
`us.` profile's 1.10x.

## Scoreboard

| Suite | Passed | Total | Pass rate |
|---|---|---|---|
| conversation | 8 | 10 | 80.0% |
| cross_agency | 9 | 21 | 42.9% |
| edge_cases | 105 | 124 | 84.7% |
| freshness | 23 | 30 | 76.7% |
| groundedness | 57 | 70 | 81.4% |
| multilingual | 30 | 40 | 75.0% |
| refusal | 40 | 45 | 88.9% |
| sensitivity | 28 | 30 | 93.3% |
| stretch_tagalog | 12 | 15 | 80.0% |
| **all** | **312** | **385** | **81.0%** |

`not_applicable` is 0: this run does not set `FPA_DISABLED_DOC_IDS`, so the
Yolobus documents were live and all 385 cases were scored.

## Why it is not promoted

The bilingual parity gate, which has no waiver by design:

```
Spanish parity: 30/40 vs mirrored English 33/40 — gap 7.5 pp exceeds the
5-point gate on 2+ cases
```

Four pairs fail in Spanish while their English mirror passes (`ml-016`/`edge-012`,
`ml-023`/`ground-033`, `ml-025`/`xagency-009`, `ml-marin-001`/`ground-marin-001`)
and one the other way (`ml-vta-001`/`ground-vta-002`). Two of the four are the
`as_of` provenance mismatch in #163. Tracked as #165.

`cross_agency` at 42.9% is also below the macro floor of 72.9%. That is the
open finding in #138, re-measured here against a 100% baseline that was three
cases in July and is twenty-one now.

## Comparison against the same morning's nightly

The scheduled nightly ran at the same HEAD (`3fdd468`) about four hours
earlier, mostly from cache (85% hit rate) and with the `us.` judge profile.

| Suite | This run (cold) | Nightly (85% cached) |
|---|---|---|
| conversation | 80.0% | 80.0% |
| cross_agency | **42.9%** | **47.6%** |
| edge_cases | 84.7% | 84.7% |
| freshness | **76.7%** | 73.3% |
| groundedness | 81.4% | 81.4% |
| multilingual | 75.0% | 75.0% |
| refusal | 88.9% | 88.9% |
| sensitivity | **93.3%** | 90.0% |
| stretch_tagalog | 80.0% | 80.0% |
| all | 312/385 | 311/385 |

The two runs differ on exactly one `cross_agency` case, `xagency-samtrans-001`
(PASS in the nightly, FAIL here). Nothing on the branch under test touches that
case — containment was measured across all 349 suite questions and only
`xagency-010` and `refuse-014` retrieve differently — so the 4.7-point spread
is judge variance between a cached and a cold run plus a judge routing change,
not a regression. Read 42.9% and 47.6% as two samples of one state.

## The Yolobus containment hole, measured exactly

The 42 cases in #151 appear only under the deploy environment, which this run
does not use. Measured separately with an offline full run under
`FPA_DISABLED_DOC_IDS=yolobus-fares`:

```
42 case(s) not applicable under the active source policy and excluded from
the denominator: source_disabled:yolobus-fares (42)   -> 21/343
```

Those same 42 case ids in this live run: **42/42 measured, 36 passed, 6
failed** (`conv-003`, `edge-011`, `edge-025`, `fresh-004`, `sens-001b`,
`xagency-003`). Containment was hiding six real failures, not just withholding
passes. #151 is closed on this evidence; lifting the containment itself is
#164.

## What this run produced

- #150 — measured. ADR 0027's enumeration retrieval works and flips no case;
  #150 stays open naming the gap.
- #161 — `golden.jsonl` re-recorded live and the fabrication is gone; the
  re-record re-opens the independent-audit baseline, so it is a separate,
  owner-gated change.
- #162, #163, #165 — filed from this run's evidence.
- #138 — re-measured at 42.9%, worse than the issue title's 57.1%.
