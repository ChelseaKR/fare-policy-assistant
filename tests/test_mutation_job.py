"""The advisory mutation job has to be able to be red (issue #246).

From 2026-08-10 to at least 2026-09-07 every weekly `Mutation (advisory)` run
crashed before scoring a single mutant and reported success. The sandbox mutmut
builds under `mutants/` did not carry `docs/answer-contract.schema.json`, which
`evals/checks.py` reaches through `assistant.contract`, so the first test
raised `FileNotFoundError`. `continue-on-error` sat on the step, so no API view
showed a failure, and `docs/mutation-testing.md` kept publishing a ~75% score
that nothing re-derived.

These tests cover the three holes: the sandbox is missing a file the tests
open, a run that scored nothing is reported as a pass, and the job is not
allowed to fail.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from assistant import config, contract, gtfs

REPO_ROOT = Path(__file__).resolve().parent.parent


def _script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _mutmut_config() -> dict[str, Any]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["mutmut"]


def _copied(relative: str, entries: list[str]) -> bool:
    target = Path(relative)
    return any(target == Path(entry) or Path(entry) in target.parents for entry in entries)


@pytest.mark.parametrize(
    "path",
    [config.ANSWER_SCHEMA_PATH, contract.SCHEMA_PATH, gtfs.GTFS_RAW_DIR / "current.json"],
)
def test_mutation_sandbox_carries_the_data_the_tests_read(path: Path) -> None:
    # The schema is the file whose absence crashed five weekly runs:
    # checks.run_checks validates every structured answer against it on disk.
    # The GTFS pointer is the next one the fare-consistency tests open; without
    # it they fail in the sandbox only, which also aborts the run.
    mutmut = _mutmut_config()
    relative = path.relative_to(config.REPO_ROOT).as_posix()
    entries = [*mutmut.get("also_copy", []), *mutmut.get("source_paths", [])]
    assert _copied(relative, entries), (
        f"{relative} is read by the mutated modules' tests but is not in "
        "[tool.mutmut] also_copy, so every mutant run crashes before scoring"
    )


def test_also_copy_files_land_in_a_directory_mutmut_creates() -> None:
    # mutmut copies a listed *file* with a bare shutil.copy2 and does not create
    # its parent, so a file whose directory holds no mutated source crashes the
    # copy itself. Directories are copied with copytree and are always safe.
    mutmut = _mutmut_config()
    created = {Path(".")} | {
        parent for source in mutmut["source_paths"] for parent in Path(source).parents
    }
    for entry in mutmut.get("also_copy", []):
        if (REPO_ROOT / entry).is_file():
            assert Path(entry).parent in created, (
                f"also_copy file {entry} would be copied into a mutants/ directory "
                "that does not exist; list its directory instead"
            )


# --- the zero-mutant guard -------------------------------------------------


@pytest.fixture(name="summary")
def _summary() -> Any:
    return _script("mutation_summary")


def _write_meta(root: Path, name: str, codes: dict[str, int | None]) -> None:
    meta = root / "mutants" / "evals" / f"{name}.py.meta"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")


def test_no_meta_files_is_a_refusal_not_a_zero(summary: Any, tmp_path: Path) -> None:
    with pytest.raises(summary.NoMutationScore, match="no mutmut results"):
        summary.tally(tmp_path / "mutants")


def test_a_run_that_generated_no_mutants_is_refused(summary: Any, tmp_path: Path) -> None:
    _write_meta(tmp_path, "checks", {})
    with pytest.raises(summary.NoMutationScore, match="zero mutants"):
        summary.tally(tmp_path / "mutants")


def test_a_crash_before_any_mutant_was_scored_is_refused(summary: Any, tmp_path: Path) -> None:
    # The shape of the five green runs: mutants generated, every one unchecked.
    _write_meta(tmp_path, "checks", {"evals.checks.x_a__mutmut_1": None})
    _write_meta(tmp_path, "judges", {"evals.judges.x_b__mutmut_1": None})
    with pytest.raises(summary.NoMutationScore, match="none of the 2 mutants"):
        summary.tally(tmp_path / "mutants")


def test_a_scored_run_is_counted_and_dated(summary: Any, tmp_path: Path) -> None:
    _write_meta(
        tmp_path,
        "checks",
        {"m1": 1, "m2": 1, "m3": 0, "m4": None},
    )
    _write_meta(tmp_path, "judges", {"m5": 1, "m6": 36})
    tally = summary.tally(tmp_path / "mutants")
    assert tally.generated == 6
    assert tally.killed == 3
    assert tally.survived == 1
    assert tally.by_status["not checked"] == 1
    assert tally.by_status["timeout"] == 1
    assert tally.survived_by_module == {"checks": 1}
    table = summary.render(tally, date="2026-10-02", commit="abc1234")
    assert "2026-10-02" in table and "abc1234" in table
    assert "| Mutation score | 50% (3/6) |" in table


def test_main_exits_nonzero_on_an_empty_run(summary: Any, tmp_path: Path) -> None:
    _write_meta(tmp_path, "checks", {"m1": None})
    assert summary.main(["--mutants", str(tmp_path / "mutants")]) == 1


def test_main_appends_the_table_to_the_step_summary(
    summary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_meta(tmp_path, "checks", {"m1": 1, "m2": 0})
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    assert summary.main(["--mutants", str(tmp_path / "mutants")]) == 0
    assert "| Mutation score | 50% (1/2) |" in step_summary.read_text(encoding="utf-8")


# --- the job itself ---------------------------------------------------------


def test_mutation_target_runs_the_zero_mutant_guard() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nmutation:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/mutation_summary.py" in recipe


def test_mutation_workflow_can_be_red() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "mutation.yml").read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["mutation"]
    assert "continue-on-error" not in job, "a job-level continue-on-error hides the crash too"
    for step in job["steps"]:
        assert "continue-on-error" not in step, (
            f"step {step.get('name')!r}: a step that cannot fail reports success over a crash"
        )
    assert any("make mutation" in step.get("run", "") for step in job["steps"])
