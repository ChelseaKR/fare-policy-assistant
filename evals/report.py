"""Report generator: EVALS.md (repo root) + docs/eval-report.html.

Leads with the scoreboard, then representative failures with full traces
(question → retrieved passages → answer → checks/judge reasoning). Failures
are sampled deterministically (first N per suite), not cherry-picked.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from assistant import config

FAILURES_PER_SUITE = 3


def latest_run_dir() -> Path:
    runs = sorted(d for d in config.EVAL_RUNS_DIR.iterdir() if d.is_dir())
    if not runs:
        raise SystemExit("no eval runs found; run `make eval` first")
    return runs[-1]


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return summary, records


def _failed_checks(record: dict) -> list[str]:
    out = [
        f"{c['name']}: {c['detail'] or 'failed'}" for c in record["checks"] if not c["passed"]
    ]
    out += [
        f"judge/{j['name']}: {j['detail']}"
        for j in record["judges"]
        if j["passed"] is False
    ]
    return out


def _spanish_parity(records: list[dict]) -> str | None:
    """Compare each multilingual case against its English mirror."""
    by_id = {r["case_id"]: r for r in records}
    pairs = [
        (r, by_id.get(r.get("mirror_of", "")))
        for r in records
        if r["suite"] == "multilingual"
    ]
    if not pairs:
        return None
    rows = ["| Spanish case | passed | English mirror | passed |", "|---|---|---|---|"]
    for es, en in pairs:
        rows.append(
            f"| {es['case_id']} | {'✓' if es['passed'] else '✗'} "
            f"| {en['case_id'] if en else '—'} "
            f"| {('✓' if en['passed'] else '✗') if en else '—'} |"
        )
    return "\n".join(rows)


def generate_markdown(summary: dict, records: list[dict]) -> str:
    total = summary["total"]
    lines = [
        "# Evaluation Report",
        "",
        f"Generated from the run at `{summary['run_at']}` "
        + f"({summary['mode']}, "
        + ("offline — deterministic checks only" if summary["offline"] else "live")
        + ").",
        "",
        f"- Answer model: `{summary['answer_model']}` · Judge model: `{summary['judge_model']}`",
        "- Judges ran: "
        + ("yes" if summary["judges_ran"] else "no (recorded as skipped, not passed)"),
        "- Prompt versions: "
        + ", ".join(f"{k} {v}" for k, v in summary["prompt_versions"].items()),
        f"- Duration: {summary['duration_seconds']}s",
        "",
        "## Scoreboard",
        "",
        "| Suite | Passed | Total | Pass rate |",
        "|---|---|---|---|",
    ]
    for name, s in summary["suites"].items():
        lines.append(f"| {name} | {s['passed']} | {s['total']} | {s['pass_rate']}% |")
    pct = round(100 * total["passed"] / total["total"], 1) if total["total"] else 0
    lines.append(f"| **all** | **{total['passed']}** | **{total['total']}** | **{pct}%** |")

    parity = _spanish_parity(records)
    if parity:
        lines += ["", "## Spanish parity", "", parity]

    lines += [
        "",
        "## Representative failures",
        "",
        f"First {FAILURES_PER_SUITE} failures per suite, in case order — not cherry-picked.",
        "",
    ]
    any_failures = False
    for suite in summary["suites"]:
        failures = [r for r in records if r["suite"] == suite and not r["passed"]]
        for r in failures[:FAILURES_PER_SUITE]:
            any_failures = True
            lines += [
                f"### {r['case_id']} ({suite})",
                "",
                f"**Question:** {r['question']}",
                "",
                f"**Why this case exists:** {r['rationale']}",
                "",
                "**Retrieved passages:**",
                "",
            ]
            for p in r["passages"][:3]:
                lines.append(f"- `{p['chunk_id']}` ({p['section']}, score {p['score']}): "
                             f"{p['text'][:200]}…")
            lines += [
                "",
                f"**Answer ({r['kind']}):** {r['answer']}",
                "",
                "**Failed checks:**",
                "",
            ]
            lines += [f"- {fc}" for fc in _failed_checks(r)] or ["- (judge only)"]
            lines.append("")
    if not any_failures:
        lines.append("No failures in this run.")

    lines += [
        "",
        "---",
        "Regenerate with `make eval` (full) or `python -m evals.report` (report only).",
        "",
    ]
    return "\n".join(lines)


def generate_html(markdown: str, summary: dict) -> str:
    # Deliberately minimal: the markdown is the artifact; HTML is a styled view.
    body = html.escape(markdown)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fare Policy Assistant — Evaluation Report</title>
<style>
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 56rem;
          margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  pre {{ white-space: pre-wrap; }}
</style>
</head>
<body>
<pre>{body}</pre>
</body>
</html>
"""


def generate(run_dir: Path | None = None) -> None:
    run_dir = run_dir or latest_run_dir()
    summary, records = load_run(run_dir)
    markdown = generate_markdown(summary, records)
    (config.REPO_ROOT / "EVALS.md").write_text(markdown, encoding="utf-8")
    (config.REPO_ROOT / "docs" / "eval-report.html").write_text(
        generate_html(markdown, summary), encoding="utf-8"
    )
    print(f"wrote EVALS.md and docs/eval-report.html from {run_dir.name}")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
