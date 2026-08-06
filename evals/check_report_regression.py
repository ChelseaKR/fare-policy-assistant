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

This module also re-applies the bilingual parity gate (M-1; audit P1-1;
AIEV-10/11, I18N-22) to the committed report: the Spanish-vs-mirrored-English
delta must stay within 5 points (on 2+ cases), and no gated suite may sit more
than 5 points below the macro pass rate. The parity half *does* have one loud
escape hatch — `evals/expected_below_macro.json`, a committed suite→rationale
map for the below-macro form only — because a small suite can honestly trail
the macro (the failures are documented in the report, not hidden) without that
being an equity gap. The Spanish-parity delta itself has no waiver.
"""

from __future__ import annotations

import json
import sys

from assistant import config
from evals import provenance
from evals.runner import (
    expected_below_macro,
    parity_regressed,
    suite_regressed,
    suites_below_macro,
)

EVALS_MD_PATH = config.REPO_ROOT / "EVALS.md"
BASELINE_PATH = config.REPO_ROOT / "evals" / "baseline.json"


def check(evals_md_text: str, baseline: dict) -> list[str]:
    """Return a list of human-readable regression descriptions; empty is clean.

    Three ways a committed report can describe a worse system than the baseline,
    and until 2026-08-05 only the first was checked:

    1. **A suite's pass rate dropped** — `suite_regressed`, the original check.
    2. **A suite vanished.** A baseline suite absent from the committed report
       used to be skipped, on the reasoning that it was "a missing-provenance
       problem for `evals/provenance.py`". It is not: `provenance.py` compares
       prompt and corpus versions and has never looked at suite composition, so
       the responsibility was assigned to a module that does not implement it.
       Regenerating `EVALS.md` from a `--suite` subset, or deleting a suite
       outright, left every gate green.
    3. **A suite shrank.** `suite_regressed` needs both a pass-rate drop and a
       pass-count drop, so removing the cases a suite fails *raises* its pass
       rate and can never trip it. Deleting the failing test is the oldest way
       to turn a board green, and nothing here could see it.

    (2) and (3) share the regression escape hatch this module already documents:
    a maintainer who genuinely retires a suite or a case runs
    `python -m evals.runner --update-baseline` with a written rationale, which
    is a visible line in the diff. What is closed is the silent path.
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
            regressions.append(
                f"{suite}: {base['passed']}/{base['total']} at baseline but absent from the "
                "committed EVALS.md — a suite that disappears is not a suite that passed"
            )
            continue
        if now.get("total", 0) < base.get("total", 0):
            regressions.append(
                f"{suite}: {base['total']} cases at baseline, {now['total']} in the committed "
                f"EVALS.md — {base['total'] - now['total']} case(s) removed. Dropping cases "
                "raises a pass rate without fixing anything, so it is gated separately"
            )
        if suite_regressed(base, now):
            regressions.append(
                f"{suite}: {base['passed']}/{base['total']} (baseline) -> "
                f"{now['passed']}/{now['total']} (committed EVALS.md)"
            )
    return regressions


def _parity_from_table(evals_md_text: str) -> dict | None:
    """Fallback for committed reports generated before the provenance payload
    carried `parity`: re-derive the pair outcomes from the rendered
    `## Spanish parity` table (the exact format `report._parity_table` emits).
    Rows whose mirror is absent from the run (`—`) are skipped, matching
    `runner.parity_delta`. Returns None when no table or no complete pair."""
    lines = evals_md_text.splitlines()
    try:
        start = lines.index("## Spanish parity")
    except ValueError:
        return None
    pairs = passed = mirror_passed = 0
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 4:
            continue
        if cells[1] not in ("✓", "✗") or cells[3] not in ("✓", "✗"):
            continue  # header, separator, or a row with a missing mirror (—)
        pairs += 1
        passed += cells[1] == "✓"
        mirror_passed += cells[3] == "✓"
    if pairs == 0:
        return None
    return {
        "suite": "multilingual",
        "pairs": pairs,
        "passed": passed,
        "mirror_passed": mirror_passed,
        "delta_pp": round((mirror_passed - passed) * 100 / pairs, 1),
    }


def check_parity_committed(
    evals_md_text: str, annotations: dict[str, str] | None = None
) -> list[str]:
    """Parity findings for the *committed* EVALS.md; empty is clean.

    Reads the machine-readable `parity` payload when present, else falls back
    to the rendered Spanish-parity table. A report with neither (or with no
    complete mirror pair) skips the delta half with a note on stdout — partial
    runs are legitimate — but the below-macro half still applies whenever the
    suites scoreboard is embedded.
    """
    notes = expected_below_macro() if annotations is None else annotations
    payload = provenance.read_evals_md(evals_md_text) or {}
    problems = []
    parity = payload.get("parity") or _parity_from_table(evals_md_text)
    if parity is None:
        print("committed EVALS.md carries no complete Spanish/English mirror pairs; delta skipped")
    elif parity_regressed(parity):
        problems.append(
            f"Spanish parity: {parity['passed']}/{parity['pairs']} vs mirrored English "
            f"{parity['mirror_passed']}/{parity['pairs']} in the committed EVALS.md — "
            f"gap {parity['delta_pp']} pp exceeds the 5-point gate on 2+ cases"
        )
    for name, o in sorted(suites_below_macro(payload.get("suites", {})).items()):
        if name in notes:
            continue
        problems.append(
            f"{name}: {o['pass_rate']}% in the committed EVALS.md is below the macro floor "
            f"{o['floor']}% (macro {o['macro']}%) with no written annotation in "
            "evals/expected_below_macro.json"
        )
    return problems


def main() -> int:
    if not BASELINE_PATH.exists():
        print("no evals/baseline.json; skipping committed-report regression gate", file=sys.stderr)
        return 0
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    evals_md = EVALS_MD_PATH.read_text(encoding="utf-8")
    parity_findings = check_parity_committed(evals_md)
    if parity_findings:
        print(
            "COMMITTED-REPORT PARITY (M-1) — EVALS.md trips the bilingual parity gate:",
            file=sys.stderr,
        )
        for p in parity_findings:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nFix the gap and regenerate EVALS.md from a passing live run, or — for the "
            "below-macro form only — annotate the suite in evals/expected_below_macro.json "
            "with a written rationale. The Spanish-parity delta has no waiver.",
            file=sys.stderr,
        )
        return 1
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
    print(
        "check_report_regression: EVALS.md does not regress vs. the committed baseline "
        "and holds the bilingual parity gate."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
