"""Score robustness: how much to trust a single run's headline number.

The determinism probe (docs/audits/eval-remediation-2026-07-11.md) established
that the harness is reproducible — same inputs, same score. Reproducible is not
the same as *precise*: 192/201 is one point estimate over one fixed case set.
This quantifies two things a careful reader asks:

- **Sampling uncertainty.** A Wilson 95% confidence interval on the pass rate
  (`evals/stats.py`), overall and per suite, because 201 Bernoulli trials carry
  real width — a suite of 10 cases at 90% is not meaningfully different from one
  at 80%.
- **Concentration.** A leave-one-suite-out jackknife: how far the overall rate
  moves if any single suite is dropped, so no one suite is silently carrying (or
  sinking) the headline.

    python -m evals.robustness            # report against the latest run
    python -m evals.robustness --write    # also regenerate docs/eval-robustness.md

Both are pure arithmetic over a finished run's pass/fail column — no model
calls. The paraphrase-sensitivity experiment (does the score move when a
question is reworded?) needs live generation and is specified in
docs/eval-robustness.md as the next step, not run here.
"""

from __future__ import annotations

import json
import sys

from assistant import config
from evals.stats import wilson_interval


def latest_run_dir():
    runs = sorted(p for p in config.EVAL_RUNS_DIR.glob("*/") if (p / "results.jsonl").exists())
    if not runs:
        raise SystemExit("no eval runs found under evals/runs/; run `make eval` first")
    return runs[-1]


def load_rows(run_dir) -> list[dict]:
    path = run_dir / "results.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def overall_ci(rows: list[dict]) -> tuple[int, int, float, float]:
    passed = sum(1 for r in rows if r.get("passed"))
    n = len(rows)
    lo, hi = wilson_interval(passed, n)
    return passed, n, lo, hi


def suite_cis(rows: list[dict]) -> dict[str, tuple[int, int, float, float]]:
    by_suite: dict[str, list[dict]] = {}
    for r in rows:
        by_suite.setdefault(r.get("suite", "?"), []).append(r)
    out: dict[str, tuple[int, int, float, float]] = {}
    for suite, srows in sorted(by_suite.items()):
        passed = sum(1 for r in srows if r.get("passed"))
        lo, hi = wilson_interval(passed, len(srows))
        out[suite] = (passed, len(srows), lo, hi)
    return out


def jackknife_by_suite(rows: list[dict]) -> dict[str, float]:
    """Overall pass rate with each suite removed, in points relative to the full
    rate — a small delta means no single suite dominates the headline."""
    full_passed = sum(1 for r in rows if r.get("passed"))
    full_rate = full_passed / len(rows) if rows else 0.0
    suites = {r.get("suite", "?") for r in rows}
    out: dict[str, float] = {}
    for suite in sorted(suites):
        kept = [r for r in rows if r.get("suite") != suite]
        if not kept:
            continue
        rate = sum(1 for r in kept if r.get("passed")) / len(kept)
        out[suite] = (rate - full_rate) * 100
    return out


def render(rows: list[dict]) -> str:
    passed, n, lo, hi = overall_ci(rows)
    lines = ["# Score robustness", ""]
    lines.append(
        "The harness is deterministic (proven by the temp-0 answer/judge probe in "
        "`docs/audits/eval-remediation-2026-07-11.md`), so re-running gives the same "
        "score. These figures say how *precise* that score is."
    )
    lines.append("")
    lines.append(
        f"**Overall:** {passed}/{n} = {passed / n * 100:.1f}% "
        f"(Wilson 95% CI: {lo * 100:.1f}%–{hi * 100:.1f}%)."
    )
    lines.append("")
    lines.append("## Per-suite pass rate with 95% CI")
    lines.append("")
    lines.append("| Suite | Passed | Total | Rate | 95% CI |")
    lines.append("|---|---|---|---|---|")
    for suite, (p, t, slo, shi) in suite_cis(rows).items():
        lines.append(
            f"| {suite} | {p} | {t} | {p / t * 100:.1f}% | {slo * 100:.1f}%–{shi * 100:.1f}% |"
        )
    lines.append("")
    lines.append("## Leave-one-suite-out (jackknife)")
    lines.append("")
    lines.append("Change in the overall rate, in points, when each suite is dropped:")
    lines.append("")
    lines.append("| Suite dropped | Δ overall (points) |")
    lines.append("|---|---|")
    for suite, delta in sorted(jackknife_by_suite(rows).items(), key=lambda kv: -abs(kv[1])):
        lines.append(f"| {suite} | {delta:+.2f} |")
    lines.append("")
    lines.append("## Next: paraphrase sensitivity")
    lines.append("")
    lines.append(
        "Determinism covers *identical* inputs. The open question is whether the "
        "score moves when a question is *reworded* to the same meaning. The planned "
        "experiment: hand-author meaning-preserving paraphrases for a stratified "
        "sample, run both versions live, and report the pass/fail flip rate. It "
        "needs live generation and a labeled sample, so it is specified here rather "
        "than computed from a finished run."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    write = "--write" in sys.argv
    rows = load_rows(latest_run_dir())
    md = render(rows)
    print(md)
    if write:
        out = config.REPO_ROOT / "docs" / "eval-robustness.md"
        out.write_text(md + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
