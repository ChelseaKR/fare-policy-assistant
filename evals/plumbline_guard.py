"""The merge gate over the independent audit's report.

`plumbline gate` decides PASS or FAIL from two things: a per-suite floor, and
each suite's own hard-failure rule. Both are necessary and neither is enough
here.

A floor is a minimum. Several floors in `evals/plumbline/target.toml` sit well
below the harness's defaults, because the lexical judge is scoring a system it
was not shaped around and the honest floor is the measured one. A floor of 0.04
on the accuracy suite catches collapse and nothing else; a score can decay from
0.73 to 0.56 and stay green the whole way down. So this module fails on any
suite that scores below the committed baseline.

A hard failure is a real finding, and the audit found 76 of them across five
suites on the day it landed. Leaving the gate red forever teaches everyone to ignore it;
lowering something until it goes green is worse. So every hard failure is
listed in `evals/plumbline/acknowledged_findings.json` with a reason and an
owner, and this module fails on a hard failure that is *not* on that list — and
also on a listed one that has stopped firing, because a waiver for a fixed
problem is a lie that accumulates.

    uv run python -m evals.plumbline_guard          # gate the latest report
    uv run python -m evals.plumbline_guard --report <path/to/report.json>
    uv run python -m evals.plumbline_guard --not-before <epoch seconds>

`--not-before` is what stops this module from grading yesterday's evidence.
Without a report of its own, `latest_report()` reads the newest `report.json`
on disk by mtime — and `docs/audits/plumbline/<run>/report.json` is committed,
so on a run where `plumbline-gate.sh` wrote nothing at all there is still a
clean report sitting there to find. It was clean when it was committed, the
guard says so, and the build goes green over a gate that never ran (#183).
The `audit` target and the `independent-audit` job both stamp the second they
started and pass it here, so a report that predates this run is refused instead
of graded.

Exit 0 clean, 1 on any of: a suite below baseline, an unacknowledged hard
failure, a stale acknowledgement, a missing or unreadable report, or a report
older than the run that was supposed to produce it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assistant import config

AUDIT_DIR = config.REPO_ROOT / "docs" / "audits" / "plumbline"
BASELINE_PATH = config.REPO_ROOT / "evals" / "plumbline" / "baseline.json"
ACK_PATH = config.REPO_ROOT / "evals" / "plumbline" / "acknowledged_findings.json"

# Scores are rounded to four places in the report, so anything below this is
# rounding, not decay. Deliberately not a "small regressions are fine" budget:
# the replay is deterministic, so a real change moves the number.
TOLERANCE = 0.0005

# Extra suite *detail* keys this project treats as hard failures on top of the
# harness's own `hard_failures` verdict. Plumbline names these per suite rather
# than uniformly, and on the committed report the union is stricter than the
# harness: seven items across `adversarial` and `privacy` are hard failures here
# that Plumbline does not call hard failures, and all seven are acknowledged.
#
# Being stricter is a choice; being *narrower* would be the defect. Until
# 2026-08-28 this tuple was the only thing `hard_failures()` read, so it was a
# hand-maintained re-derivation of a verdict the report already states outright
# (`suite["hard_failures"]`, plumbline/src/plumbline/report.py). A pinned-harness
# bump that renamed a key, or added a suite whose hard failures land under a key
# not listed here, would have made those failures invisible to the merge gate
# with nothing to notice — the guard would have gone on passing over exactly the
# findings it exists to stop. The union below cannot be narrower than the
# harness's own answer, and `tests/test_plumbline_audit.py` pins that.
_EXTRA_HARD_FAILURE_KEYS = (
    "load_bearing_failures",
    "fabricated_citation_failures",
    "behavior_failures",
    "content_leaks",
    "flagged_items",
    "echoed_prompt_pii",
    "unsourced_disclosures",
    "solicitations",
)


def latest_report(audit_dir: Path = AUDIT_DIR, not_before: float | None = None) -> Path:
    """The newest report.json on disk, refusing one this run did not produce.

    `not_before` is an epoch second captured before `plumbline-gate.sh` was
    invoked. The harness rewrites its report on every run, so the report of a
    run that happened is always at least that new; a report that is older is a
    leftover, and grading it would report on evidence from a different run.
    """
    reports = sorted(audit_dir.glob("*/report.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise SystemExit(
            f"no report.json under {audit_dir}. Run ./plumbline-gate.sh first; a guard "
            "with nothing to read is not a guard that passed."
        )
    newest = reports[-1]
    if not_before is not None and newest.stat().st_mtime < not_before:
        raise SystemExit(
            f"the newest report under {audit_dir} is {newest}, last written "
            f"{newest.stat().st_mtime:.0f}, before this run started ({not_before:.0f}). "
            "`plumbline-gate.sh` produced no report this run, so the only thing left to "
            "grade is a committed one from a previous run — and it was clean when it was "
            "committed, so grading it would pass the build over a gate that never ran. "
            "Read the gate's own output above for why it wrote nothing."
        )
    return newest


def hard_failures(report: dict) -> dict[str, list[str]]:
    """suite id -> the item ids that must be acknowledged or fixed.

    The harness's own per-suite `hard_failures` verdict, unioned with the extra
    detail keys this project also refuses to let pass. Reading the harness's
    field first is what stops the guard from ever seeing *less* than the tool it
    is gating; the union is what keeps it stricter where the project has chosen
    to be.
    """
    out: dict[str, list[str]] = {}
    for suite in report["suites"]:
        found: list[str] = list(suite.get("hard_failures") or [])
        for key in _EXTRA_HARD_FAILURE_KEYS:
            found.extend(suite.get("details", {}).get(key) or [])
        # cross_language records a fact id per pair rather than an item id.
        if found:
            out[suite["suite"]] = sorted(set(found))
    return out


def check(report: dict, baseline: dict, acknowledged: dict) -> list[str]:
    """Every reason to fail, in report order. Empty means the gate holds."""
    problems: list[str] = []

    baseline_scores = {s["suite"]: s["score"] for s in baseline["suites"]}
    current_scores = {s["suite"]: s["score"] for s in report["suites"]}

    comparable = (
        report["provenance"]["dataset_sha256"] == baseline["dataset_sha256"]
        and report["provenance"]["judge_config_sha256"] == baseline["judge_config_sha256"]
    )
    if not comparable:
        # The harness refuses to subtract scores across different evidence or
        # different scoring rules, and it is right to. But "not comparable" must
        # not read as "fine": a re-recording is exactly when a regression would
        # slip through, so the guard demands a fresh baseline rather than
        # shrugging.
        problems.append(
            "the report and the baseline are not comparable (the dataset or judge "
            "configuration hash moved). Re-run the audit, review the new numbers by "
            "hand, and commit a new evals/plumbline/baseline.json — a re-recording is "
            "when a regression is easiest to miss, not a reason to skip the check."
        )
    else:
        for suite, score in sorted(current_scores.items()):
            if suite not in baseline_scores:
                problems.append(f"{suite}: scored {score:.4f} but the baseline has no entry")
                continue
            if score < baseline_scores[suite] - TOLERANCE:
                problems.append(
                    f"{suite}: {score:.4f} is below the committed baseline "
                    f"{baseline_scores[suite]:.4f}"
                )
        for suite in sorted(set(baseline_scores) - set(current_scores)):
            problems.append(f"{suite}: in the baseline but not in this run")

    observed = hard_failures(report)
    for suite in sorted(observed):
        allowed = set(acknowledged.get(suite) or {})
        unlisted = sorted(set(observed[suite]) - allowed)
        if unlisted:
            problems.append(
                f"{suite}: hard failure(s) nobody has acknowledged: {', '.join(unlisted)}. "
                "Fix them, or add each one to evals/plumbline/acknowledged_findings.json "
                "with a reason and an owner."
            )
    for suite, entries in sorted(acknowledged.items()):
        if suite.startswith("_"):
            continue
        stale = sorted(set(entries) - set(observed.get(suite, [])))
        if stale:
            problems.append(
                f"{suite}: acknowledged finding(s) that no longer fire: {', '.join(stale)}. "
                "Remove them; a waiver for a fixed problem is a lie that accumulates."
            )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="a report.json (default: the latest)")
    parser.add_argument(
        "--not-before",
        type=float,
        metavar="EPOCH",
        help=(
            "refuse a report last written before this epoch second. Stamp it "
            "immediately before invoking plumbline-gate.sh, so a run that wrote no "
            "report cannot be graded against a committed one from a previous run."
        ),
    )
    args = parser.parse_args()

    report_path = args.report or latest_report(not_before=args.not_before)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    acknowledged_doc = json.loads(ACK_PATH.read_text(encoding="utf-8"))
    acknowledged = {k: v for k, v in acknowledged_doc.items() if not k.startswith("_")}

    problems = check(report, baseline, acknowledged)
    print(f"plumbline guard: {report_path}")
    if problems:
        for problem in problems:
            print(f"  FAIL {problem}", file=sys.stderr)
        raise SystemExit(1)
    counted = sum(len(v) for v in hard_failures(report).values())
    print(
        f"plumbline guard: no suite below baseline; {counted} acknowledged hard "
        "failure(s), none new, none stale."
    )


if __name__ == "__main__":
    main()
