"""Tests for the paired A/B run comparison (evals/compare.py)."""

from __future__ import annotations

import json

import pytest

from evals import compare


def _write_run(run_dir, cases, *, suites=None):
    """Write a tiny run dir. `cases` maps case_id -> (suite, passed[, extra])."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for cid, spec in cases.items():
            suite, passed = spec[0], spec[1]
            rec = {"case_id": cid, "suite": suite, "passed": passed}
            if len(spec) > 2:
                rec.update(spec[2])
            f.write(json.dumps(rec) + "\n")
    summary = {"suites": suites or {}}
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


def test_compare_counts_concordant_and_discordant(tmp_path):
    a = _write_run(
        tmp_path / "A",
        {
            "c1": ("s", True),
            "c2": ("s", True),
            "c3": ("s", False),
            "c4": ("s", False),
        },
        suites={"s": {"pass_rate": 50.0}},
    )
    b = _write_run(
        tmp_path / "B",
        {
            "c1": ("s", True),
            "c2": ("s", False),
            "c3": ("s", True),
            "c4": ("s", False),
        },
        suites={"s": {"pass_rate": 50.0}},
    )
    result = compare.compare(a, b)
    assert result["n_cases"] == 4
    assert result["concordant"] == 2  # c1 (TT), c4 (FF)
    assert result["b"] == 1 and result["flips_regressed"] == ["c2"]  # A pass, B fail
    assert result["c"] == 1 and result["flips_improved"] == ["c3"]  # A fail, B pass
    assert result["mcnemar_p"] == pytest.approx(1.0)  # b==c==1


def test_compare_reports_significant_one_sided_shift(tmp_path):
    # Five cases all regress A→B: b=5, c=0, p = 0.0625.
    a = _write_run(tmp_path / "A", {f"c{i}": ("s", True) for i in range(5)})
    b = _write_run(tmp_path / "B", {f"c{i}": ("s", False) for i in range(5)})
    result = compare.compare(a, b)
    assert result["b"] == 5 and result["c"] == 0
    assert result["mcnemar_p"] == pytest.approx(0.0625)


def test_compare_per_suite_deltas(tmp_path):
    a = _write_run(
        tmp_path / "A",
        {"c1": ("x", True), "c2": ("y", False)},
        suites={"x": {"pass_rate": 80.0}, "y": {"pass_rate": 40.0}},
    )
    b = _write_run(
        tmp_path / "B",
        {"c1": ("x", True), "c2": ("y", True)},
        suites={"x": {"pass_rate": 90.0}, "y": {"pass_rate": 55.0}},
    )
    deltas = {d["suite"]: d["delta"] for d in compare.compare(a, b)["suite_deltas"]}
    assert deltas == {"x": 10.0, "y": 15.0}


def test_compare_binarizes_pass_fraction_from_replicated_runs(tmp_path):
    # A replicated run records pass_fraction, not a boolean passed. 0.6 → pass,
    # 0.2 → fail. (A record still carries a boolean too, but fraction wins.)
    a = _write_run(
        tmp_path / "A",
        {
            "c1": ("s", False, {"pass_fraction": 0.6, "replicates": 5}),
            "c2": ("s", True, {"pass_fraction": 0.2, "replicates": 5}),
        },
    )
    b = _write_run(
        tmp_path / "B",
        {
            "c1": ("s", True),
            "c2": ("s", True),
        },
    )
    result = compare.compare(a, b)
    # c1: A pass(0.6) / B pass → concordant. c2: A fail(0.2) / B pass → improvement.
    assert result["concordant"] == 1
    assert result["c"] == 1 and result["b"] == 0


def test_compare_exits_nonzero_on_case_mismatch(tmp_path):
    a = _write_run(tmp_path / "A", {"c1": ("s", True), "c2": ("s", True)})
    b = _write_run(tmp_path / "B", {"c1": ("s", True), "c3": ("s", True)})
    with pytest.raises(SystemExit, match="mismatched runs"):
        compare.compare(a, b)


def test_compare_exits_nonzero_on_missing_files(tmp_path):
    missing = tmp_path / "nope"
    missing.mkdir()
    other = _write_run(tmp_path / "B", {"c1": ("s", True)})
    with pytest.raises(SystemExit, match="malformed run"):
        compare.compare(missing, other)


def test_compare_exits_nonzero_on_bad_json(tmp_path):
    bad = tmp_path / "A"
    bad.mkdir()
    (bad / "summary.json").write_text("{}", encoding="utf-8")
    (bad / "results.jsonl").write_text("{not json}\n", encoding="utf-8")
    other = _write_run(tmp_path / "B", {"c1": ("s", True)})
    with pytest.raises(SystemExit, match="malformed run"):
        compare.compare(bad, other)


def test_compare_exits_nonzero_on_duplicate_case_id(tmp_path):
    dup = tmp_path / "A"
    dup.mkdir()
    (dup / "summary.json").write_text("{}", encoding="utf-8")
    (dup / "results.jsonl").write_text(
        json.dumps({"case_id": "c1", "suite": "s", "passed": True})
        + "\n"
        + json.dumps({"case_id": "c1", "suite": "s", "passed": False})
        + "\n",
        encoding="utf-8",
    )
    other = _write_run(tmp_path / "B", {"c1": ("s", True)})
    with pytest.raises(SystemExit, match="duplicate case_id"):
        compare.compare(dup, other)


def test_main_prints_report_and_validates_argc(tmp_path, capsys):
    a = _write_run(tmp_path / "A", {"c1": ("s", True)}, suites={"s": {"pass_rate": 100.0}})
    b = _write_run(tmp_path / "B", {"c1": ("s", False)}, suites={"s": {"pass_rate": 0.0}})
    compare.main([str(a), str(b)])
    out = capsys.readouterr().out
    assert "Paired comparison" in out
    assert "McNemar" in out
    with pytest.raises(SystemExit, match="usage"):
        compare.main([str(a)])
