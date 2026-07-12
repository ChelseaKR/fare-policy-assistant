# Score robustness

The harness is deterministic (proven by the temp-0 answer/judge probe in `docs/audits/eval-remediation-2026-07-11.md`), so re-running gives the same score. These figures say how *precise* that score is.

**Overall:** 192/201 = 95.5% (Wilson 95% CI: 91.7%–97.6%).

## Per-suite pass rate with 95% CI

| Suite | Passed | Total | Rate | 95% CI |
|---|---|---|---|---|
| conversation | 8 | 10 | 80.0% | 49.0%–94.3% |
| cross_agency | 3 | 3 | 100.0% | 43.9%–100.0% |
| edge_cases | 46 | 48 | 95.8% | 86.0%–98.8% |
| freshness | 9 | 10 | 90.0% | 59.6%–98.2% |
| groundedness | 27 | 29 | 93.1% | 78.0%–98.1% |
| multilingual | 22 | 22 | 100.0% | 85.1%–100.0% |
| refusal | 34 | 34 | 100.0% | 89.8%–100.0% |
| sensitivity | 28 | 30 | 93.3% | 78.7%–98.2% |
| stretch_tagalog | 15 | 15 | 100.0% | 79.6%–100.0% |

## Leave-one-suite-out (jackknife)

Change in the overall rate, in points, when each suite is dropped:

| Suite dropped | Δ overall (points) |
|---|---|
| refusal | -0.91 |
| conversation | +0.81 |
| multilingual | -0.55 |
| groundedness | +0.41 |
| sensitivity | +0.38 |
| stretch_tagalog | -0.36 |
| freshness | +0.29 |
| edge_cases | -0.10 |
| cross_agency | -0.07 |

## Next: paraphrase sensitivity

Determinism covers *identical* inputs. The open question is whether the score moves when a question is *reworded* to the same meaning. The planned experiment: hand-author meaning-preserving paraphrases for a stratified sample, run both versions live, and report the pass/fail flip rate. It needs live generation and a labeled sample, so it is specified here rather than computed from a finished run.

