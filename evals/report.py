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

from assistant import config, corpus
from evals import provenance

FAILURES_PER_SUITE = 3


def _rate_cell(suite: dict) -> str:
    """Scoreboard pass-rate cell. When the run recorded a Wilson interval
    (a `--replicates` run), render `mean% (low–high)`; otherwise just `mean%`."""
    if "ci_low" in suite and "ci_high" in suite:
        return f"{suite['pass_rate']}% ({suite['ci_low']}–{suite['ci_high']})"
    return f"{suite['pass_rate']}%"


def _variance_section() -> str:
    """Static documentation of the variance tooling, linked from the scoreboard
    when replicated, and always present so the methods are discoverable."""
    return "\n".join(
        [
            "Deterministic checks are stable run to run; LLM-as-judge verdicts are not. "
            "Two tools quantify that noise instead of leaving it as a prose caveat.",
            "",
            "**Replicated runs.** `python -m evals.runner --replicates N` scores every "
            "case N times and reports, per suite, the mean pass rate over all N·(cases) "
            "trials with a Wilson 95% confidence interval (`pass_rate`, `ci_low`, "
            "`ci_high` in `summary.json`; `pass_fraction` per case in `results.jsonl`). "
            "`N=1` is the default and is byte-identical to a single run. Replicates make "
            "live calls, so they are gated behind credentials like any live run.",
            "",
            "**Paired A/B comparison.** `python -m evals.compare <run_dir_A> <run_dir_B>` "
            "joins two runs by case id and treats each case as its own control. It "
            "reports McNemar flip counts — `b` cases that regressed (A pass → B fail) "
            "and `c` that improved (A fail → B pass) — with an exact two-sided McNemar "
            "p-value, plus per-suite pass-rate deltas. Use it to decide a prompt change "
            "from a paired test rather than a single before/after delta.",
        ]
    )


def _corpus_version(summary: dict) -> str:
    """Prefer the version recorded at run time; fall back to the current corpus
    for older runs whose summary predates corpus_version recording."""
    return summary.get("corpus_version") or corpus.corpus_version()


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
    out = [f"{c['name']}: {c['detail'] or 'failed'}" for c in record["checks"] if not c["passed"]]
    out += [f"judge/{j['name']}: {j['detail']}" for j in record["judges"] if j["passed"] is False]
    return out


def _parity_table(records: list[dict], suite: str, lang_label: str) -> str | None:
    """Compare each case in `suite` against its English mirror (`mirror_of`).

    Shared by the Spanish suite (near parity expected) and any stretch-language
    suite (a parity gap is expected and reported honestly, not hidden)."""
    by_id = {r["case_id"]: r for r in records}
    pairs = [(r, by_id.get(r.get("mirror_of", ""))) for r in records if r["suite"] == suite]
    if not pairs:
        return None
    rows = [f"| {lang_label} case | passed | English mirror | passed |", "|---|---|---|---|"]
    for other, en in pairs:
        rows.append(
            f"| {other['case_id']} | {'✓' if other['passed'] else '✗'} "
            f"| {en['case_id'] if en else '—'} "
            f"| {('✓' if en['passed'] else '✗') if en else '—'} |"
        )
    return "\n".join(rows)


def _spanish_parity(records: list[dict]) -> str | None:
    return _parity_table(records, "multilingual", "Spanish")


def _stretch_language_parity(records: list[dict]) -> str | None:
    """Tagalog stretch suite vs. its English mirrors — see docs/ROADMAP.md P3-3
    and evals/suites/stretch_tagalog.yaml. Expected to trail the Spanish table;
    that gap is the point, not a regression (evals/baseline.json carries no
    threshold for this suite, so a low score here never fails a build)."""
    return _parity_table(records, "stretch_tagalog", "Tagalog (stretch)")


def _cost_line(summary: dict) -> str:
    """One-line cost summary; token counts are exact, USD is an estimate."""
    cost = summary.get("cost")
    if not cost:
        return "- Cost: not recorded for this run"
    total = cost.get("total_est_usd")
    answer = cost.get("answer_model", {}).get("est_usd")
    judge = cost.get("judge_model", {}).get("est_usd")
    cache_create = sum(
        cost.get(kind, {}).get("cache_creation_input_tokens", 0)
        for kind in ("answer_model", "judge_model")
    )
    cache_read = sum(
        cost.get(kind, {}).get("cache_read_input_tokens", 0)
        for kind in ("answer_model", "judge_model")
    )
    cache_note = f"; cache write/read {cache_create:,}/{cache_read:,}"
    if total is None or answer is None or judge is None:
        unpriced = ", ".join(cost.get("unpriced_models", [])) or "unspecified model"
        return (
            f"- Cost (estimated): unavailable for {cost['total_tokens']:,} tokens — "
            f"unpriced: {unpriced} (exact tokens{cache_note}; "
            "unpriced usage is never assigned a zero-dollar estimate)"
        )
    return (
        f"- Cost (estimated): ${total:.4f} for "
        f"{cost['total_tokens']:,} tokens — "
        f"answer ${answer:.4f}, "
        f"judge ${judge:.4f} "
        f"(exact tokens{cache_note}, list-price estimate)"
    )


def _calibration_section(summary: dict, records: list[dict]) -> str | None:
    """Judge-vs-human agreement on the committed label sample. Live runs only;
    offline runs have no judge verdicts to compare against."""
    if not summary.get("judges_ran"):
        return None
    from evals.calibration import calibrate

    try:
        c = calibrate(records)
    except FileNotFoundError:
        return None
    if not c["n_matched"]:
        return None
    kappa = "n/a" if c["cohen_kappa"] is None else f"{c['cohen_kappa']:.3f}"
    lines = [
        f"Human labels checked against this run's judge verdicts on "
        f"{c['n_matched']} of {c['n_labels']} sampled (case, judge) pairs.",
        "",
        f"- Raw agreement: **{c['agreement']:.1%}**",
        f"- Cohen's κ: **{kappa}**",
        f"- Stale labels skipped (answer changed since labeling): **{c['n_stale']}**",
        f"- Note: {c['note']}.",
    ]
    if c["stale"]:
        lines.append(
            "- Stale (bound answer changed — relabel with "
            f"`python -m evals.calibration --emit`): {', '.join(c['stale'])}"
        )
    if c.get("unbound"):
        lines.append(
            f"- Unbound (no answer hash; not staleness-checked): {', '.join(c['unbound'])}"
        )
    if c["unmatched"]:
        lines.append(f"- Unmatched (no judge verdict in this run): {', '.join(c['unmatched'])}")
    return "\n".join(lines)


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
        f"- Corpus version: `{_corpus_version(summary)}`",
        f"- Duration: {summary['duration_seconds']}s",
        _cost_line(summary),
        "",
        "## Scoreboard",
        "",
        "| Suite | Passed | Total | Pass rate |",
        "|---|---|---|---|",
    ]
    for name, s in summary["suites"].items():
        lines.append(f"| {name} | {s['passed']} | {s['total']} | {_rate_cell(s)} |")
    pct = round(100 * total["passed"] / total["total"], 1) if total["total"] else 0
    lines.append(f"| **all** | **{total['passed']}** | **{total['total']}** | **{pct}%** |")
    if summary.get("replicates", 1) > 1:
        lines += [
            "",
            f"Pass rate is the mean over {summary['replicates']} replicate runs of the "
            "same cases; the parenthesized band is a Wilson 95% confidence interval. "
            "See [Measuring variance](#measuring-variance).",
        ]

    sens = summary["suites"].get("sensitivity")
    if sens and "pairs_total" in sens:
        lines += [
            "",
            f"**Counterfactual sensitivity:** {sens['pairs_passed']}/{sens['pairs_total']} "
            "boundary pairs correctly distinguished "
            "(a pair passes only if every variant passes across the boundary).",
        ]

    parity = _spanish_parity(records)
    if parity:
        lines += ["", "## Spanish parity", "", parity]

    stretch_parity = _stretch_language_parity(records)
    if stretch_parity:
        lines += [
            "",
            "## Stretch-language parity (Tagalog)",
            "",
            "Clearly non-parity by design (docs/ROADMAP.md P3-3): no corpus document is "
            "published in Tagalog, so every row below is a cross-lingual retrieval test, "
            "not a translated-source lookup like the Spanish table above. A lower pass "
            "rate here than in Spanish parity is the expected, honest result, not a "
            "regression.",
            "",
            stretch_parity,
        ]

    cal = _calibration_section(summary, records)
    if cal:
        lines += ["", "## Judge calibration", "", cal]

    lines += ["", "## Measuring variance", "", _variance_section()]

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
            lines += [f"### {r['case_id']} ({suite})", ""]
            if r.get("turns"):
                lines.append("**Conversation:**")
                lines.append("")
                for i, turn in enumerate(r["turns"], 1):
                    lines.append(f"{i}. {turn}")
                lines.append("")
            else:
                lines += [f"**Question:** {r['question']}", ""]
            lines += [
                f"**Why this case exists:** {r['rationale']}",
                "",
                "**Retrieved passages:**",
                "",
            ]
            for p in r["passages"][:3]:
                lines.append(
                    f"- `{p['chunk_id']}` ({p['section']}, score {p['score']}): {p['text'][:200]}…"
                )
            lines += [
                "",
                f"**Answer ({r['kind']}):** {r['answer']}",
                "",
            ]
            if r.get("raw_model_answer"):
                lines += [
                    "**Model text the guard blocked (never shown to riders):** "
                    + r["raw_model_answer"][:500],
                    "",
                ]
            lines += [
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
        "<!-- Machine-readable provenance for two CI gates: evals/provenance.py",
        "(prompt versions and corpus this report was generated against — fails if",
        "these drift from HEAD, so a stale report cannot masquerade as current) and",
        "evals/check_report_regression.py (the `suites` scoreboard below, re-checked",
        "against the committed evals/baseline.json — fails if this committed report",
        "describes a regression that was never actually gated; see",
        "docs/audits/eval-regression-2026-06-30.md for why that check exists). -->",
        provenance.render_evals_md_block(
            {
                "run_id": summary["run_at"],
                "corpus_version": _corpus_version(summary),
                "prompt_versions": summary["prompt_versions"],
                "suites": summary["suites"],
            }
        ),
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
    # Model answers occasionally contain Markdown hard-break spaces. The report
    # is rendered inside a preformatted block, so those spaces add no value and
    # make the committed artifacts fail git's whitespace check.
    markdown = "\n".join(line.rstrip() for line in generate_markdown(summary, records).splitlines())
    markdown += "\n"
    (config.REPO_ROOT / "EVALS.md").write_text(markdown, encoding="utf-8")
    (config.REPO_ROOT / "docs" / "eval-report.html").write_text(
        generate_html(markdown, summary), encoding="utf-8"
    )
    print(f"wrote EVALS.md and docs/eval-report.html from {run_dir.name}")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
