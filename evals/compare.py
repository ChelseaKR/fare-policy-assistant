"""Paired A/B comparison of two eval runs.

    python -m evals.compare <run_dir_A> <run_dir_B>

For a prompt or retrieval change, a single before/after run delta is a
one-sample read of a noisy instrument: a suite can move a point or two purely
from LLM-judge variance. This joins two run directories by case id and treats
each case as its own control — the paired unit is the same question scored
under config A and config B.

It reports the McNemar-style flip counts:

    b = cases A passed and B failed   (candidate regressions)
    c = cases A failed and B passed   (candidate improvements)

and the exact two-sided McNemar p-value from those two discordant counts (see
`evals.stats.mcnemar_exact_p`), plus per-suite pass-rate deltas. Concordant
cases (both pass or both fail) carry no information about the change and are
only counted.

Exits nonzero when either run is malformed (missing files, unreadable JSON) or
the two runs do not describe the same set of cases — a paired test on
mismatched cases is meaningless.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.stats import mcnemar_exact_p


def _case_passed(record: dict) -> bool:
    """Binary pass for a case. A replicated run (`--replicates`) records a
    `pass_fraction`; binarize it by majority vote so it lines up with a single
    run's boolean `passed`."""
    if "pass_fraction" in record:
        return record["pass_fraction"] >= 0.5
    return bool(record["passed"])


def load_run(run_dir: Path) -> tuple[dict, dict[str, dict]]:
    """Return `(summary, {case_id: record})` for a run directory.

    Raises `SystemExit` (nonzero) on any malformation: missing summary.json or
    results.jsonl, unparseable JSON, a record without a case id, or a duplicate
    case id within the run.
    """
    summary_path = run_dir / "summary.json"
    results_path = run_dir / "results.jsonl"
    if not summary_path.exists() or not results_path.exists():
        raise SystemExit(f"malformed run {run_dir}: missing summary.json or results.jsonl")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"malformed run {run_dir}: cannot read summary.json ({e})") from e
    records: dict[str, dict] = {}
    for lineno, line in enumerate(results_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"malformed run {run_dir}: results.jsonl line {lineno}: {e}") from e
        cid = rec.get("case_id")
        if not cid:
            raise SystemExit(f"malformed run {run_dir}: results.jsonl line {lineno} has no case_id")
        if cid in records:
            raise SystemExit(f"malformed run {run_dir}: duplicate case_id {cid!r}")
        records[cid] = rec
    if not records:
        raise SystemExit(f"malformed run {run_dir}: results.jsonl is empty")
    return summary, records


def compare(dir_a: Path, dir_b: Path) -> dict:
    """Paired comparison of two run directories. Raises `SystemExit` on
    malformed runs or a case-id mismatch."""
    summary_a, recs_a = load_run(dir_a)
    summary_b, recs_b = load_run(dir_b)

    ids_a, ids_b = set(recs_a), set(recs_b)
    if ids_a != ids_b:
        only_a = sorted(ids_a - ids_b)
        only_b = sorted(ids_b - ids_a)
        raise SystemExit(
            "mismatched runs: case sets differ "
            f"(only in A: {only_a or '—'}; only in B: {only_b or '—'})"
        )

    b = c = concordant = 0
    flips_regressed: list[str] = []
    flips_improved: list[str] = []
    for cid in sorted(ids_a):
        pa = _case_passed(recs_a[cid])
        pb = _case_passed(recs_b[cid])
        if pa and not pb:
            b += 1
            flips_regressed.append(cid)
        elif pb and not pa:
            c += 1
            flips_improved.append(cid)
        else:
            concordant += 1

    p_value = mcnemar_exact_p(b, c)

    # Per-suite pass-rate deltas, from the two summaries. A suite present in only
    # one summary is reported with a None on the missing side.
    suites = sorted(set(summary_a.get("suites", {})) | set(summary_b.get("suites", {})))
    suite_deltas = []
    for name in suites:
        ra = summary_a.get("suites", {}).get(name, {}).get("pass_rate")
        rb = summary_b.get("suites", {}).get(name, {}).get("pass_rate")
        delta = round(rb - ra, 1) if ra is not None and rb is not None else None
        suite_deltas.append({"suite": name, "a": ra, "b": rb, "delta": delta})

    return {
        "n_cases": len(ids_a),
        "concordant": concordant,
        "b": b,
        "c": c,
        "flips_regressed": flips_regressed,
        "flips_improved": flips_improved,
        "mcnemar_p": p_value,
        "suite_deltas": suite_deltas,
        "run_a": str(dir_a),
        "run_b": str(dir_b),
    }


def format_report(result: dict) -> str:
    lines = [
        f"Paired comparison: {result['n_cases']} cases",
        f"  A = {result['run_a']}",
        f"  B = {result['run_b']}",
        "",
        f"Concordant (no change): {result['concordant']}",
        f"Discordant b (A pass → B fail, regressions): {result['b']}"
        + (f"  {result['flips_regressed']}" if result["flips_regressed"] else ""),
        f"Discordant c (A fail → B pass, improvements): {result['c']}"
        + (f"  {result['flips_improved']}" if result["flips_improved"] else ""),
        f"McNemar exact two-sided p = {result['mcnemar_p']:.4f}",
        "",
        "Per-suite pass-rate delta (B − A):",
        "  suite                    A       B     delta",
    ]
    for s in result["suite_deltas"]:
        a = "—" if s["a"] is None else f"{s['a']:.1f}%"
        bb = "—" if s["b"] is None else f"{s['b']:.1f}%"
        d = "—" if s["delta"] is None else f"{s['delta']:+.1f}"
        lines.append(f"  {s['suite']:<22} {a:>6}  {bb:>6}  {d:>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        raise SystemExit("usage: python -m evals.compare <run_dir_A> <run_dir_B>")
    result = compare(Path(argv[0]), Path(argv[1]))
    print(format_report(result))


if __name__ == "__main__":
    main()
