"""Tests for the eval-history trend artifact (evals/history.py)."""

from __future__ import annotations

import json
from pathlib import Path

from evals import history


def _write_run(runs_dir: Path, run_id: str, summary: dict) -> None:
    d = runs_dir / run_id
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _make_runs(runs_dir: Path) -> None:
    # Oldest offline run, no "cost" key (older-schema, like 20260612T060312Z).
    _write_run(
        runs_dir,
        "20260612T060312Z",
        {
            "run_at": "2026-06-12T06:03:12+00:00",
            "mode": "full",
            "offline": True,
            "prompt_versions": {"system": "v1 2026-06-11", "answer_user": "v1 2026-06-11"},
            "duration_seconds": 0.0,
            "suites": {
                "groundedness": {"passed": 0, "total": 16, "pass_rate": 0.0},
                "refusal": {"passed": 7, "total": 14, "pass_rate": 50.0},
            },
            "total": {"passed": 7, "total": 30},
        },
    )
    # Later offline run with a prompt bump (system v1 → v2) and a cost block.
    _write_run(
        runs_dir,
        "20260613T000305Z",
        {
            "run_at": "2026-06-13T00:03:05+00:00",
            "mode": "full",
            "offline": True,
            "prompt_versions": {
                "system": "v2 2026-06-12 (v1 2026-06-11; v2 tightened refusals)",
                "answer_user": "v1 2026-06-11",
            },
            "duration_seconds": 0.1,
            "cost": {"total_tokens": 0, "total_est_usd": 0.0},
            "suites": {
                "groundedness": {"passed": 4, "total": 16, "pass_rate": 25.0},
                "refusal": {"passed": 9, "total": 14, "pass_rate": 64.3},
            },
            "total": {"passed": 13, "total": 30},
        },
    )
    # A live run — a different instrument that must not share a series.
    _write_run(
        runs_dir,
        "20260617T003330Z",
        {
            "run_at": "2026-06-17T00:33:30+00:00",
            "mode": "full",
            "offline": False,
            "prompt_versions": {"system": "v2 2026-06-12", "answer_user": "v1 2026-06-11"},
            "duration_seconds": 42.5,
            "cost": {"total_tokens": 1000, "total_est_usd": 0.1234},
            "suites": {
                "groundedness": {"passed": 15, "total": 16, "pass_rate": 93.8},
                "refusal": {"passed": 13, "total": 14, "pass_rate": 92.9},
            },
            "total": {"passed": 28, "total": 30},
        },
    )


def test_load_runs_defaults_missing_cost(tmp_path: Path) -> None:
    _make_runs(tmp_path)
    runs = history.load_runs(tmp_path)
    assert [r["run_id"] for r in runs] == [
        "20260612T060312Z",
        "20260613T000305Z",
        "20260617T003330Z",
    ]
    # The oldest run lacks a cost block: est_usd defaults to None, not a crash.
    assert runs[0]["est_usd"] is None
    assert runs[1]["est_usd"] == 0.0
    assert runs[2]["est_usd"] == 0.1234


def test_instrument_separation(tmp_path: Path) -> None:
    _make_runs(tmp_path)
    runs = history.load_runs(tmp_path)
    instruments = {r["instrument"] for r in runs}
    assert instruments == {"full · offline (mock)", "full · live"}
    groups = history._group_by_instrument(runs)
    # Offline and live never land in the same group/series.
    assert len(groups["full · offline (mock)"]) == 2
    assert len(groups["full · live"]) == 1


def test_markdown_has_tables_and_caveat(tmp_path: Path) -> None:
    _make_runs(tmp_path)
    runs = history.load_runs(tmp_path)
    md = history.generate_markdown(runs)
    # Caveat about instruments is stated explicitly.
    assert "different instruments" in md
    # A section per instrument.
    assert "## full · offline (mock)" in md
    assert "## full · live" in md
    # Missing cost renders as n/a, present cost renders as a dollar figure.
    assert "n/a" in md
    assert "$0.1234" in md
    # Overall pass rate cells present.
    assert "**23.3%**" in md  # 7/30 for the oldest run


def test_version_bump_annotation(tmp_path: Path) -> None:
    _make_runs(tmp_path)
    runs = history.load_runs(tmp_path)
    md = history.generate_markdown(runs)
    # The system prompt bumped v1 → v2 between the two offline runs.
    assert "prompt bump" in md
    assert "system v1→v2" in md


def test_svg_has_polyline_per_instrument(tmp_path: Path) -> None:
    _make_runs(tmp_path)
    runs = history.load_runs(tmp_path)
    svg = history.generate_svg(runs)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    # The two-run offline instrument yields polylines; the single live run
    # yields at least plotted points. Both instruments are labelled.
    assert "full · offline (mock)" in svg
    assert "full · live" in svg
    assert "<polyline" in svg


def test_generate_writes_both_files(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _make_runs(runs_dir)
    docs_dir = tmp_path / "docs"
    history.generate(runs_dir=runs_dir, docs_dir=docs_dir)
    assert (docs_dir / "eval-history.md").exists()
    assert (docs_dir / "eval-history.svg").exists()
    assert (docs_dir / "eval-history.svg").read_text(encoding="utf-8").startswith("<svg")


def test_empty_runs_dir_is_graceful(tmp_path: Path) -> None:
    runs = history.load_runs(tmp_path)
    assert runs == []
    md = history.generate_markdown(runs)
    assert "No runs found" in md
    svg = history.generate_svg(runs)
    assert svg.startswith("<svg")


# ── a run that measured nothing must not plot as a run that scored zero ───────


def _minimal(run_at: str, *, total: dict, suites: dict) -> dict:
    return {
        "run_at": run_at,
        "mode": "full",
        "offline": True,
        "prompt_versions": {"system": "v1 2026-06-11"},
        "duration_seconds": 1.0,
        "suites": suites,
        "total": total,
    }


def test_a_run_that_scored_no_case_has_no_overall_rate(tmp_path: Path) -> None:
    """0.0% is the worst score on a published chart; "no cases" is not a score.

    A run aborted before it scored anything used to render as a catastrophic
    drop to zero, indistinguishable from a run in which everything failed.
    """

    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "20260701T000000Z",
        _minimal("2026-07-01T00:00:00+00:00", total={"passed": 0, "total": 0}, suites={}),
    )

    run = history.load_runs(runs_dir)[0]

    assert run["overall"] is None


def test_a_suite_with_no_pass_rate_is_absent_rather_than_zero(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "20260701T000000Z",
        _minimal(
            "2026-07-01T00:00:00+00:00",
            total={"passed": 3, "total": 4},
            suites={
                "refusal": {"passed": 3, "total": 4, "pass_rate": 75.0},
                "freshness": {"passed": 0, "total": 0},
            },
        ),
    )

    run = history.load_runs(runs_dir)[0]

    assert run["suites"] == {"refusal": 75.0}
    assert "freshness" not in run["suites"]


def test_the_table_prints_an_em_dash_for_an_unmeasured_overall(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "20260701T000000Z",
        _minimal("2026-07-01T00:00:00+00:00", total={"passed": 0, "total": 0}, suites={}),
    )

    markdown = history.generate_markdown(history.load_runs(runs_dir))
    row = next(line for line in markdown.splitlines() if "2026-07-01" in line)

    assert "0.0%" not in row, f"a run that scored nothing was published as a rate: {row!r}"
    assert "—" in row


def test_the_chart_omits_a_run_with_no_overall_rate_from_the_overall_line(
    tmp_path: Path,
) -> None:
    """The chart already breaks a suite's line across a run that lacks it. The
    overall line took every run unconditionally, so an unmeasured run would
    either be drawn at zero or crash the renderer."""

    runs_dir = tmp_path / "runs"
    for index, total in enumerate(
        [
            {"passed": 4, "total": 4},
            {"passed": 0, "total": 0},
            {"passed": 3, "total": 4},
        ]
    ):
        _write_run(
            runs_dir,
            f"2026070{index + 1}T000000Z",
            _minimal(
                f"2026-07-0{index + 1}T00:00:00+00:00",
                total=total,
                suites={"refusal": {"passed": total["passed"], "total": 4, "pass_rate": 50.0}},
            ),
        )

    svg = history.generate_svg(history.load_runs(runs_dir))
    overall = [
        line for line in svg.splitlines() if "polyline" in line and history._OVERALL_COLOR in line
    ]

    assert len(overall) == 1
    # Two plotted runs, not three: the unmeasured one contributes no point.
    assert overall[0].count(",") == 2
