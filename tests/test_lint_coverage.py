"""The lint and typecheck gates must actually cover the tree they claim to.

Background: `C90` (mccabe, CQ-05) was selected repo-wide on 2026-08-21. From
that day until 2026-08-28 it could not report a single finding in `scripts/`,
because CI's `checks` job ran `ruff check src tests evals web` while the
Makefile ran `ruff check src tests evals web scripts`. Four C901 violations
landed in the evidence-site and promotion-attestation builders, `make verify`
was red on `main` for a week, and every CI run over the same tree was green.
`tools/`, which holds the merge-blocking i18n gates, was in neither list and had
never been linted or typechecked at all.

Two properties keep that from recurring, and both are checked here rather than
trusted:

1. every first-party directory holding Python is inside `LINT_PATHS`;
2. CI invokes the Makefile targets instead of re-spelling their command lines,
   so there is one definition of the gate set and it is the one contributors
   run locally.

Property 2's workflow half lives in `tests/test_workflow_safety.py`, next to the
other static CI invariants.
"""

import re

import pytest

from assistant import config

# Directories that hold Python but are not this project's source: virtualenvs,
# caches, build output, and the vendored standards code that is verified at its
# own immutable source boundary (see [tool.ruff] extend-exclude).
_NOT_FIRST_PARTY = {
    ".git",
    ".mypy_cache",
    ".plumbline-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
}


def _make_variable(name: str) -> list[str]:
    """The value of a `NAME := a b c` assignment in the Makefile."""
    text = (config.REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*:?=\s*(.+)$", text, re.MULTILINE)
    assert match is not None, f"Makefile no longer defines {name}"
    return match.group(1).split()


def _first_party_python_dirs() -> set[str]:
    """Top-level directories that contain at least one first-party .py file."""
    found: set[str] = set()
    for path in config.REPO_ROOT.rglob("*.py"):
        parts = path.relative_to(config.REPO_ROOT).parts
        if len(parts) < 2 or "__pycache__" in parts:
            continue
        if _NOT_FIRST_PARTY.intersection(parts):
            continue
        found.add(parts[0])
    return found


def test_every_first_party_python_directory_is_linted():
    uncovered = _first_party_python_dirs() - set(_make_variable("LINT_PATHS"))
    assert not uncovered, (
        f"{sorted(uncovered)} hold first-party Python that `make lint` never reads. "
        "A linter aimed at a directory it does not visit is a gate that cannot fail: "
        "add the directory to LINT_PATHS in the Makefile (CI runs the same target)."
    )


def test_typechecked_paths_are_a_subset_of_linted_paths():
    """`tools/` and `scripts/` may be narrower under mypy than under ruff, but a
    directory nobody lints must not be reachable only through mypy: the two lists
    have to stay in a stated relationship rather than drifting independently."""
    lint = set(_make_variable("LINT_PATHS"))
    types = set(_make_variable("TYPE_PATHS"))
    assert types <= lint, f"TYPE_PATHS entries outside LINT_PATHS: {sorted(types - lint)}"


def test_the_pre_commit_mypy_hook_covers_every_typechecked_directory():
    """`.pre-commit-config.yaml` was the third copy of the path list, and stale.

    It read `^(src|web)/` while the Makefile typechecked `src web scripts`, so a
    contributor with the hooks installed got a *narrower* check than CI, which is
    the wrong direction for a local guardrail that advertises itself as mirroring
    the merge gate.
    """
    import re as _re

    import yaml

    config_text = (config.REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(config_text)
    patterns = [
        hook["files"]
        for repo in parsed["repos"]
        for hook in repo["hooks"]
        if hook["id"] == "mypy" and "files" in hook
    ]
    assert patterns, "the pre-commit mypy hook no longer declares a `files` pattern"
    for pattern in patterns:
        compiled = _re.compile(pattern)
        for directory in _make_variable("TYPE_PATHS"):
            assert compiled.match(f"{directory}/module.py"), (
                f"the pre-commit mypy hook pattern {pattern!r} does not cover {directory}/, "
                "which `make typecheck` does. The local hook must not be narrower than CI."
            )


@pytest.mark.parametrize("directory", ["scripts", "tools"])
def test_the_two_directories_that_were_missing_stay_covered(directory):
    """Named explicitly, so deleting them from LINT_PATHS is a red build with a
    message that says what happened before, not a silently narrower gate."""
    assert directory in _make_variable("LINT_PATHS"), (
        f"{directory}/ was outside the lint gate until 2026-08-28 "
        "(scripts/ in CI, tools/ everywhere). Do not put it back."
    )
