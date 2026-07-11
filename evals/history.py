"""Eval-history trend artifact: render every recorded run into one page.

Walks `evals/runs/*/summary.json` and emits `docs/eval-history.md` plus a
hand-built `docs/eval-history.svg`, so the 2026-06-12 → present trajectory —
per-suite pass rates, cost, duration, and each prompt-version bump — is one
linkable page instead of JSON scattered across ~31 directories.

Honesty rule (CLAUDE.md, "the improvement curve is part of the story", and
the audit methodology's "different instruments" note): mock/offline runs are
scored by deterministic checks against a mock model; live runs call a real
answer + judge model. They are different instruments and are never plotted on
the same series. Runs are grouped by instrument = mode (full/smoke) + offline
(mock) vs live, and the page states this caveat explicitly.

No third-party dependencies (same discipline as `web/offline.py` and
`evals/report.py`): stdlib in, Markdown + SVG out, deterministic.

    uv run python -m evals.history      # regenerate docs/eval-history.{md,svg}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from assistant import config

# Distinct, print-safe colours for the per-series polylines. "overall" is drawn
# last and heaviest so the headline trajectory reads first.
_SERIES_COLORS = [
    "#1d4ed8",
    "#b91c1c",
    "#047857",
    "#b45309",
    "#7c3aed",
    "#0891b2",
    "#be185d",
    "#4d7c0f",
    "#9a3412",
]
_OVERALL_COLOR = "#111827"


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def instrument_of(summary: dict) -> str:
    """Group key. Mock/offline runs and live runs are different instruments and
    must never share a series; smoke and full are different sample sizes too."""
    mode = summary.get("mode", "?")
    kind = "offline (mock)" if summary.get("offline") else "live"
    return f"{mode} · {kind}"


def _overall_pct(summary: dict) -> float:
    total = summary.get("total", {})
    n = total.get("total", 0)
    return round(100 * total.get("passed", 0) / n, 1) if n else 0.0


def _version_sig(prompt_versions: dict) -> dict[str, str]:
    """Leading `vN` token of each prompt entry (e.g. "v7 2026-06-30 (…)" → "v7").
    A change in this signature between consecutive runs is a prompt bump."""
    sig: dict[str, str] = {}
    for key, val in (prompt_versions or {}).items():
        token = str(val).split(" ", 1)[0].strip()
        sig[key] = token or "?"
    return sig


def _version_delta(prev: dict[str, str], cur: dict[str, str]) -> list[str]:
    """Human-readable list of prompt-version changes from `prev` to `cur`."""
    changes: list[str] = []
    for key in sorted(set(prev) | set(cur)):
        before, after = prev.get(key), cur.get(key)
        if before == after:
            continue
        if before is None:
            changes.append(f"{key} (new {after})")
        elif after is None:
            changes.append(f"{key} (dropped {before})")
        else:
            changes.append(f"{key} {before}→{after}")
    return changes


def load_runs(runs_dir: Path | None = None) -> list[dict]:
    """Every run under `runs_dir`, in timestamp order (directory names sort
    chronologically), each enriched with the fields the page needs."""
    runs_dir = runs_dir or config.EVAL_RUNS_DIR
    runs: list[dict] = []
    if not runs_dir.exists():
        # runs/ is gitignored; on a fresh checkout there is nothing to render yet.
        return runs
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        # "cost" is absent in older runs (e.g. 20260612T060312Z) — default it.
        cost = summary.get("cost") or {}
        runs.append(
            {
                "run_id": run_dir.name,
                "run_at": summary.get("run_at", run_dir.name),
                "mode": summary.get("mode", "?"),
                "offline": bool(summary.get("offline")),
                "instrument": instrument_of(summary),
                "overall": _overall_pct(summary),
                "suites": {
                    name: s.get("pass_rate", 0.0) for name, s in summary.get("suites", {}).items()
                },
                "est_usd": cost.get("total_est_usd"),
                "duration": summary.get("duration_seconds", 0.0),
                "version_sig": _version_sig(summary.get("prompt_versions", {})),
            }
        )
    return runs


def _group_by_instrument(runs: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for run in runs:
        groups.setdefault(run["instrument"], []).append(run)
    return groups


def _suite_names(runs: list[dict]) -> list[str]:
    """Union of suite names across `runs`, in first-appearance order."""
    ordered: list[str] = []
    for run in runs:
        for name in run["suites"]:
            if name not in ordered:
                ordered.append(name)
    return ordered


def _fmt_usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def generate_markdown(runs: list[dict], svg_name: str = "eval-history.svg") -> str:
    lines = [
        "# Evaluation history",
        "",
        "Every recorded eval run in `evals/runs/`, oldest first, on one page: "
        "per-suite pass rates, cost, duration, and each prompt-version bump. "
        "The run directories themselves are a local, gitignored archive; this "
        "rendered page (and the SVG) is the committed artifact. Regenerate with "
        "`make report` (or `python -m evals.history`).",
        "",
        "> **Read within an instrument, never across.** Mock/offline runs are "
        "scored by deterministic checks against a mock answer model; live runs "
        "call a real answer and judge model. They are different instruments and "
        "measure different things, so the chart and tables below group runs by "
        "instrument (mode + offline/live) and never plot mock and live scores on "
        "the same series. Smoke and full runs differ in sample size too. Only the "
        "trajectory *within* one instrument is a like-for-like comparison.",
        "",
    ]

    if not runs:
        lines += ["_No runs found under `evals/runs/`._", ""]
        return "\n".join(lines)

    lines += [
        f"![Pass-rate trajectory per instrument]({svg_name})",
        "",
    ]

    groups = _group_by_instrument(runs)
    for instrument, group in groups.items():
        suites = _suite_names(group)
        header = ["Run (UTC)", "Overall"] + suites + ["Est USD", "Duration"]
        ncols = len(header)
        lines += [
            f"## {instrument} — {len(group)} run(s)",
            "",
            "| " + " | ".join(header) + " |",
            "|" + "|".join(["---"] * ncols) + "|",
        ]
        prev_sig: dict[str, str] | None = None
        for run in group:
            if prev_sig is not None:
                delta = _version_delta(prev_sig, run["version_sig"])
                if delta:
                    note = "**↑ prompt bump:** " + ", ".join(delta)
                    lines.append("| " + note + " |" + " |" * (ncols - 1))
            cells = [
                f"`{run['run_at']}`",
                f"**{run['overall']}%**",
            ]
            cells += [
                f"{run['suites'][name]}%" if name in run["suites"] else "—" for name in suites
            ]
            cells += [_fmt_usd(run["est_usd"]), f"{run['duration']}s"]
            lines.append("| " + " | ".join(cells) + " |")
            prev_sig = run["version_sig"]
        lines.append("")

    return "\n".join(lines)


def _panel_svg(instrument: str, group: list[dict], top: int) -> tuple[str, int, int]:
    """One instrument panel (axes + a polyline per suite and for overall),
    returning the SVG fragment, the y-offset for the next panel, and the panel
    width."""
    pad_l, pad_r, pad_t, pad_b = 48, 170, 40, 44
    plot_w, plot_h = 560, 200
    width = pad_l + plot_w + pad_r
    panel_h = pad_t + plot_h + pad_b
    # Local (panel) coordinates: the whole panel is wrapped in a
    # translate(0, top) group, so nothing here may add `top` again.
    x0, y0 = pad_l, pad_t

    def px(i: int, n: int) -> float:
        if n <= 1:
            return x0 + plot_w / 2
        return x0 + plot_w * i / (n - 1)

    def py(v: float) -> float:
        return y0 + plot_h * (1 - max(0.0, min(100.0, v)) / 100)

    parts: list[str] = [f'<g transform="translate(0,{top})">']
    parts.append(
        f'<text x="{x0}" y="24" font-size="15" font-weight="700" '
        f'fill="#111827">{_esc(instrument)} — {len(group)} run(s)</text>'
    )
    # y gridlines + labels at 0/25/50/75/100.
    for val in (0, 25, 50, 75, 100):
        gy = pad_t + plot_h * (1 - val / 100)
        parts.append(
            f'<line x1="{x0}" y1="{gy}" x2="{x0 + plot_w}" y2="{gy}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x0 - 8}" y="{gy + 4}" font-size="11" '
            f'text-anchor="end" fill="#6b7280">{val}</text>'
        )
    # x axis baseline + first/last run labels.
    parts.append(
        f'<line x1="{x0}" y1="{pad_t + plot_h}" x2="{x0 + plot_w}" '
        f'y2="{pad_t + plot_h}" stroke="#9ca3af" stroke-width="1"/>'
    )
    n = len(group)
    first_lbl = group[0]["run_at"][:10]
    last_lbl = group[-1]["run_at"][:10]
    ylab = pad_t + plot_h + 18
    parts.append(
        f'<text x="{x0}" y="{ylab}" font-size="10" fill="#6b7280">{_esc(first_lbl)}</text>'
    )
    if n > 1:
        parts.append(
            f'<text x="{x0 + plot_w}" y="{ylab}" font-size="10" '
            f'text-anchor="end" fill="#6b7280">{_esc(last_lbl)}</text>'
        )

    # One series per suite, then overall on top. Missing suites break the line.
    series: list[tuple[str, str, list[tuple[int, float]]]] = []
    for idx, name in enumerate(_suite_names(group)):
        color = _SERIES_COLORS[idx % len(_SERIES_COLORS)]
        pts = [(i, r["suites"][name]) for i, r in enumerate(group) if name in r["suites"]]
        series.append((name, color, pts))
    series.append(("overall", _OVERALL_COLOR, [(i, r["overall"]) for i, r in enumerate(group)]))

    for name, color, pts in series:
        width_stroke = "3" if name == "overall" else "1.5"
        if len(pts) >= 2:
            coords = " ".join(f"{px(i, n):.1f},{py(v):.1f}" for i, v in pts)
            parts.append(
                f'<polyline fill="none" stroke="{color}" '
                f'stroke-width="{width_stroke}" points="{coords}"/>'
            )
        for i, v in pts:
            parts.append(f'<circle cx="{px(i, n):.1f}" cy="{py(v):.1f}" r="2.5" fill="{color}"/>')

    # Legend down the right margin.
    lx = x0 + plot_w + 16
    ly = pad_t + 6
    for name, color, _pts in series:
        parts.append(
            f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 20}" y2="{ly - 4}" '
            f'stroke="{color}" stroke-width="{"3" if name == "overall" else "2"}"/>'
        )
        parts.append(
            f'<text x="{lx + 26}" y="{ly}" font-size="11" fill="#374151">{_esc(name)}</text>'
        )
        ly += 18

    parts.append("</g>")
    return "\n".join(parts), top + panel_h, width


def generate_svg(runs: list[dict]) -> str:
    groups = _group_by_instrument(runs)
    fragments: list[str] = []
    top = 0
    width = 778
    for instrument, group in groups.items():
        frag, top, width = _panel_svg(instrument, group, top)
        fragments.append(frag)
    if not groups:
        fragments.append(
            '<text x="20" y="40" font-size="14" fill="#6b7280">'
            "No runs found under evals/runs/.</text>"
        )
        top = 80
    body = "\n".join(fragments)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{top}" '
        f'viewBox="0 0 {width} {top}" font-family="-apple-system, BlinkMacSystemFont, '
        f"'Segoe UI', Roboto, Helvetica, Arial, sans-serif\" "
        f'role="img" aria-label="Eval pass-rate trajectory per instrument">\n'
        f'<rect width="{width}" height="{top}" fill="#ffffff"/>\n'
        f"{body}\n</svg>\n"
    )


def generate(runs_dir: Path | None = None, docs_dir: Path | None = None) -> None:
    runs = load_runs(runs_dir)
    docs_dir = docs_dir or (config.REPO_ROOT / "docs")
    docs_dir.mkdir(parents=True, exist_ok=True)
    md = generate_markdown(runs)
    svg = generate_svg(runs)
    (docs_dir / "eval-history.md").write_text(md, encoding="utf-8")
    (docs_dir / "eval-history.svg").write_text(svg, encoding="utf-8")
    print(f"wrote docs/eval-history.md and docs/eval-history.svg from {len(runs)} run(s)")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
