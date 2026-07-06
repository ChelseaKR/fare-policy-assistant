"""Regression gate over the *committed* `EVALS.md`, not just a fresh local run.

Why this exists (see `docs/audits/eval-regression-2026-06-30.md` for the full
writeup): `evals/runner.py::check_regression` correctly fails a *live run* that
regresses against `evals/baseline.json`. But that check only ever ran locally
or in the nightly `full-evals-nightly` CI job, which uploads its run as an
artifact and does not commit anything back to the repo. Nothing previously
re-checked the `EVALS.md` a human actually committed against the baseline it
should have passed — so a locally-regenerated report from a run whose exit
code was 1 could be (and was) committed anyway, with no PR-time signal at all.

This module closes that gap without needing a live model call: `evals/report.py`
embeds the run's `suites` scoreboard in `EVALS.md`'s machine-readable provenance
comment (the same block `evals/provenance.py` reads). This re-applies
`suite_regressed` — the exact, already-tested logic `check_regression` uses —
to the *committed* scoreboard vs. the *committed* baseline. It is pure, offline,
and fast, so it runs on every PR (`ci.yml`, `make verify`), not just nightly.

    python -m evals.check_report_regression   # exit 1 if EVALS.md regresses

There is deliberately no waiver/acknowledgement escape hatch here (contrast
`evals/provenance.py`'s `stale_acknowledged.json`): the only legitimate ways to
turn this gate green are (1) fix the regression and regenerate `EVALS.md` from
a passing live run, or (2) a maintainer deliberately runs
`python -m evals.runner --update-baseline` with a written, owner-approved
rationale in the PR (AIEV-27). Silently lowering a threshold to pass this check
would be exactly the failure mode it exists to catch.
"""

from __future__ import annotations

import json
import sys

from assistant import config
from evals import provenance
from evals.runner import suite_regressed

EVALS_MD_PATH = config.REPO_ROOT / "EVALS.md"
BASELINE_PATH = config.REPO_ROOT / "evals" / "baseline.json"


def check(evals_md_text: str, baseline: dict) -> list[str]:
    """Return a list of human-readable regression descriptions; empty is clean.

    A suite present in the baseline but absent from the committed report's
    provenance is not flagged here (that is a missing-provenance problem for
    `evals/provenance.py`, not a regression this function can evaluate).
    """
    payload = provenance.read_evals_md(evals_md_text)
    if payload is None or "suites" not in payload:
        return [
            "EVALS.md has no embedded suites provenance to check against "
            "(regenerate with `make eval` or `python -m evals.report`)"
        ]
    regressions = []
    for suite, base in baseline.get("suites", {}).items():
        now = payload["suites"].get(suite)
        if now is None:
            continue
        if suite_regressed(base, now):
            regressions.append(
                f"{suite}: {base['passed']}/{base['total']} (baseline) -> "
                f"{now['passed']}/{now['total']} (committed EVALS.md)"
            )
    return regressions


def main() -> int:
    if not BASELINE_PATH.exists():
        print("no evals/baseline.json; skipping committed-report regression gate", file=sys.stderr)
        return 0
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    evals_md = EVALS_MD_PATH.read_text(encoding="utf-8")
    regressions = check(evals_md, baseline)
    if regressions:
        print(
            "COMMITTED-REPORT REGRESSION — EVALS.md regresses vs. evals/baseline.json:",
            file=sys.stderr,
        )
        for r in regressions:
            print(f"  {r}", file=sys.stderr)
        print(
            "\nSee docs/audits/eval-regression-2026-06-30.md. Fix the regression and "
            "regenerate EVALS.md from a passing live run, or a maintainer deliberately "
            "runs `python -m evals.runner --update-baseline` with a written, "
            "owner-approved rationale in the PR. Never silently.",
            file=sys.stderr,
        )
        return 1
    print("check_report_regression: EVALS.md does not regress vs. the committed baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
