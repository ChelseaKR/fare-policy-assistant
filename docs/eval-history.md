# Evaluation history

Every recorded eval run in `evals/runs/`, oldest first, on one page: per-suite pass rates, cost, duration, and each prompt-version bump. The run directories themselves are a local, gitignored archive; this rendered page (and the SVG) is the committed artifact. Regenerate with `make report` (or `python -m evals.history`).

> **Read within an instrument, never across.** Mock/offline runs are scored by deterministic checks against a mock answer model; live runs call a real answer and judge model. They are different instruments and measure different things, so the chart and tables below group runs by instrument (mode + offline/live) and never plot mock and live scores on the same series. Smoke and full runs differ in sample size too. Only the trajectory *within* one instrument is a like-for-like comparison.

![Pass-rate trajectory per instrument](eval-history.svg)

## full · offline (mock) — 6 run(s)

| Run (UTC) | Overall | edge_cases | freshness | groundedness | multilingual | refusal | conversation | cross_agency | Est USD | Duration |
|---|---|---|---|---|---|---|---|---|---|---|
| `2026-06-12T06:03:12+00:00` | **11.4%** | 0.0% | 0.0% | 0.0% | 7.1% | 50.0% | — | — | n/a | 0.0s |
| `2026-06-12T06:07:25+00:00` | **11.4%** | 0.0% | 0.0% | 0.0% | 7.1% | 50.0% | — | — | n/a | 0.0s |
| **↑ prompt bump:** answer_user v1→v2, judge_helpfulness v1→v2, system v1→v4 | | | | | | | | | | |
| `2026-06-13T00:24:22+00:00` | **10.7%** | 0.0% | 0.0% | 0.0% | 10.0% | 47.4% | — | — | n/a | 0.0s |
| **↑ prompt bump:** answer_user v2→v3, system v4→v6 | | | | | | | | | | |
| `2026-06-30T17:18:26+00:00` | **9.1%** | 0.0% | 0.0% | 0.0% | 9.5% | 47.4% | 0.0% | 0.0% | $0.0000 | 0.1s |
| **↑ prompt bump:** answer_user v3→v4, system v6→v7 | | | | | | | | | | |
| `2026-06-30T17:27:28+00:00` | **8.7%** | 0.0% | 0.0% | 0.0% | 9.1% | 47.4% | 0.0% | 0.0% | $0.0000 | 0.1s |
| `2026-06-30T17:28:31+00:00` | **8.7%** | 0.0% | 0.0% | 0.0% | 9.1% | 47.4% | 0.0% | 0.0% | $0.0000 | 0.1s |

## smoke · offline (mock) — 2 run(s)

| Run (UTC) | Overall | edge_cases | freshness | groundedness | multilingual | refusal | Est USD | Duration |
|---|---|---|---|---|---|---|---|---|
| `2026-06-12T06:07:34+00:00` | **8.0%** | 0.0% | 0.0% | 0.0% | 0.0% | 40.0% | n/a | 0.0s |
| `2026-06-12T06:14:41+00:00` | **8.0%** | 0.0% | 0.0% | 0.0% | 0.0% | 40.0% | n/a | 0.0s |

## full · live — 17 run(s)

| Run (UTC) | Overall | edge_cases | freshness | groundedness | multilingual | refusal | conversation | Est USD | Duration |
|---|---|---|---|---|---|---|---|---|---|
| `2026-06-12T06:39:41+00:00` | **72.9%** | 83.3% | 100.0% | 87.5% | 14.3% | 85.7% | — | n/a | 494.7s |
| **↑ prompt bump:** system v1→v2 | | | | | | | | | |
| `2026-06-12T06:53:08+00:00` | **84.3%** | 83.3% | 87.5% | 93.8% | 71.4% | 85.7% | — | n/a | 488.2s |
| `2026-06-12T07:06:48+00:00` | **92.9%** | 88.9% | 100.0% | 100.0% | 92.9% | 85.7% | — | n/a | 534.6s |
| **↑ prompt bump:** system v2→v3 | | | | | | | | | |
| `2026-06-12T07:17:04+00:00` | **92.9%** | 88.9% | 100.0% | 100.0% | 92.9% | 85.7% | — | n/a | 482.0s |
| **↑ prompt bump:** system v3→v4 | | | | | | | | | |
| `2026-06-13T00:01:47+00:00` | **92.9%** | 100.0% | 87.5% | 100.0% | 78.6% | 92.9% | — | n/a | 480.5s |
| **↑ prompt bump:** answer_user v1→v2, judge_helpfulness v1→v2 | | | | | | | | | |
| `2026-06-13T00:11:12+00:00` | **95.7%** | 94.4% | 87.5% | 100.0% | 92.9% | 100.0% | — | n/a | 486.9s |
| `2026-06-13T00:36:42+00:00` | **92.2%** | 92.9% | 90.0% | 92.3% | 90.0% | 94.7% | — | n/a | 728.7s |
| `2026-06-13T00:50:43+00:00` | **93.2%** | 92.9% | 90.0% | 92.3% | 95.0% | 94.7% | — | n/a | 723.0s |
| `2026-06-17T00:04:04+00:00` | **94.2%** | 96.4% | 90.0% | 92.3% | 95.0% | 94.7% | — | n/a | 797.5s |
| **↑ prompt bump:** system v4→v5 | | | | | | | | | |
| `2026-06-17T00:46:39+00:00` | **93.2%** | 100.0% | 100.0% | 92.3% | 85.0% | 89.5% | — | $1.4324 | 745.8s |
| **↑ prompt bump:** system v5→v4 | | | | | | | | | |
| `2026-06-17T01:00:41+00:00` | **95.1%** | 96.4% | 100.0% | 92.3% | 95.0% | 94.7% | — | $1.4538 | 772.3s |
| `2026-06-17T01:38:37+00:00` | **94.5%** | 96.4% | 100.0% | 92.3% | 95.0% | 94.7% | 83.3% | $1.5837 | 839.3s |
| `2026-06-17T02:22:39+00:00` | **96.6%** | 97.0% | 100.0% | 96.6% | 100.0% | 94.7% | 83.3% | $1.6843 | 898.0s |
| `2026-06-17T02:39:35+00:00` | **97.5%** | 100.0% | 100.0% | 96.6% | 100.0% | 94.7% | 83.3% | $1.6869 | 906.4s |
| **↑ prompt bump:** system v4→v5 | | | | | | | | | |
| `2026-06-17T03:11:14+00:00` | **94.9%** | 100.0% | 100.0% | 93.1% | 95.2% | 94.7% | 66.7% | $1.6856 | 922.6s |
| **↑ prompt bump:** answer_user v2→v3, system v5→v6 | | | | | | | | | |
| `2026-06-30T04:04:35+00:00` | **95.8%** | 93.9% | 100.0% | 100.0% | 90.5% | 94.7% | 100.0% | $1.6877 | 838.2s |
| `2026-06-30T04:35:31+00:00` | **95.8%** | 100.0% | 100.0% | 100.0% | 85.7% | 100.0% | 66.7% | $1.7021 | 850.3s |

## suite:freshness · offline (mock) — 1 run(s)

| Run (UTC) | Overall | freshness | Est USD | Duration |
|---|---|---|---|---|
| `2026-06-17T00:33:30+00:00` | **0.0%** | 0.0% | $0.0000 | 0.0s |

## smoke · live — 1 run(s)

| Run (UTC) | Overall | edge_cases | freshness | groundedness | multilingual | refusal | Est USD | Duration |
|---|---|---|---|---|---|---|---|---|
| `2026-06-17T01:18:01+00:00` | **96.0%** | 100.0% | 100.0% | 100.0% | 83.3% | 100.0% | $0.3485 | 185.1s |

## suite:conversation · offline (mock) — 1 run(s)

| Run (UTC) | Overall | conversation | Est USD | Duration |
|---|---|---|---|---|
| `2026-06-17T01:24:28+00:00` | **0.0%** | 0.0% | $0.0000 | 0.0s |

## suite:edge_cases · live — 3 run(s)

| Run (UTC) | Overall | edge_cases | Est USD | Duration |
|---|---|---|---|---|
| `2026-06-30T04:10:13+00:00` | **93.9%** | 93.9% | $0.5066 | 260.2s |
| **↑ prompt bump:** answer_user v3→v2, system v6→v5 | | | | |
| `2026-06-30T04:15:38+00:00` | **100.0%** | 100.0% | $0.5062 | 263.2s |
| **↑ prompt bump:** answer_user v2→v3, system v5→v6 | | | | |
| `2026-06-30T04:20:57+00:00` | **100.0%** | 100.0% | $0.5110 | 260.4s |

## suite:edge_cases · offline (mock) — 1 run(s)

| Run (UTC) | Overall | edge_cases | Est USD | Duration |
|---|---|---|---|---|
| `2026-06-30T17:27:47+00:00` | **0.0%** | 0.0% | $0.0000 | 0.0s |
