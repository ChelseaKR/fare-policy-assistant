import json

import evals.robustness as rob

ROWS = [
    {"suite": "a", "passed": True},
    {"suite": "a", "passed": True},
    {"suite": "a", "passed": False},
    {"suite": "b", "passed": True},
    {"suite": "b", "passed": True},
]


def test_overall_ci_bounds_the_rate():
    passed, n, lo, hi = rob.overall_ci(ROWS)
    assert (passed, n) == (4, 5)
    assert 0.0 <= lo <= passed / n <= hi <= 1.0


def test_suite_cis_cover_every_suite():
    cis = rob.suite_cis(ROWS)
    assert set(cis) == {"a", "b"}
    assert cis["a"][:2] == (2, 3)
    assert cis["b"][:2] == (2, 2)


def test_jackknife_reports_delta_per_suite():
    jk = rob.jackknife_by_suite(ROWS)
    assert set(jk) == {"a", "b"}
    # Dropping suite b (both pass) lowers the overall rate; dropping a (2/3) raises it.
    assert jk["b"] < 0 < jk["a"]


def test_render_has_all_sections():
    md = rob.render(ROWS)
    for heading in ("# Score robustness", "95% CI", "jackknife", "paraphrase sensitivity"):
        assert heading in md


def _write_run(dirpath, rows):
    dirpath.mkdir(parents=True)
    (dirpath / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_latest_run_and_main_write(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(runs / "20260101T000000Z", ROWS)
    _write_run(runs / "20260102T000000Z", ROWS)  # newer; latest_run_dir must pick this
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(rob.config, "EVAL_RUNS_DIR", runs)
    monkeypatch.setattr(rob.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rob.sys, "argv", ["robustness", "--write"])
    assert rob.latest_run_dir().name == "20260102T000000Z"
    assert rob.main() == 0
    assert "Score robustness" in (tmp_path / "docs" / "eval-robustness.md").read_text()


def test_no_runs_raises(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(rob.config, "EVAL_RUNS_DIR", empty)
    try:
        rob.latest_run_dir()
        raise AssertionError("expected SystemExit")
    except SystemExit:
        pass
