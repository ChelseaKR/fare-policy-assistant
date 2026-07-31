from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from assistant import config

COPIER = config.REPO_ROOT / "scripts" / "copy_tracked_bundle.py"
DEPLOY = config.REPO_ROOT / "infra" / "deploy.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path) -> None:
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Bundle Test",
        "-c",
        "user.email=bundle-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _run(
    repo: Path,
    destination: Path,
    *selection: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(COPIER),
            "--repo-root",
            str(repo),
            "--destination",
            str(destination),
            *selection,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_copies_only_selected_tracked_files_and_omits_ignored_sentinel(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/assistant").mkdir(parents=True)
    (repo / "src/assistant/__init__.py").write_text("", encoding="utf-8")
    (repo / "src/assistant/answer.py").write_text("ANSWER = 42\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs/answer-contract.schema.json").write_text("{}\n", encoding="utf-8")
    (repo / "docs/operator-notes.txt").write_text("not runtime\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "src/assistant/ignored-sentinel.py\n",
        encoding="utf-8",
    )
    _commit(repo)
    (repo / "src/assistant/ignored-sentinel.py").write_text(
        "SHOULD_NEVER_SHIP = True\n",
        encoding="utf-8",
    )

    destination = tmp_path / "bundle"
    destination.mkdir()
    result = _run(
        repo,
        destination,
        "--tree",
        "src/assistant",
        "--file",
        "docs/answer-contract.schema.json",
    )

    assert result.returncode == 0, result.stderr
    assert (destination / "src/assistant/__init__.py").is_file()
    assert (destination / "src/assistant/answer.py").read_text(encoding="utf-8") == "ANSWER = 42\n"
    assert (destination / "docs/answer-contract.schema.json").read_text(encoding="utf-8") == "{}\n"
    assert not (destination / "src/assistant/ignored-sentinel.py").exists()
    assert not (destination / "docs/operator-notes.txt").exists()
    assert not (destination / ".gitignore").exists()


def test_missing_scope_fails_before_copying_any_selected_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "web").mkdir()
    (repo / "web/handler.py").write_text("handler = object()\n", encoding="utf-8")
    _commit(repo)
    destination = tmp_path / "bundle"
    destination.mkdir()

    result = _run(
        repo,
        destination,
        "--file",
        "web/handler.py",
        "--tree",
        "prompts",
    )

    assert result.returncode == 2
    assert "tracked tree contains no files: prompts" in result.stderr
    assert list(destination.iterdir()) == []


def test_tracked_symlink_in_selected_tree_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src/assistant").mkdir(parents=True)
    (repo / "src/assistant/answer.py").write_text("SAFE = True\n", encoding="utf-8")
    (repo / "outside.txt").write_text("local-only secret\n", encoding="utf-8")
    (repo / "src/assistant/unsafe.py").symlink_to("../../outside.txt")
    _commit(repo)
    destination = tmp_path / "bundle"
    destination.mkdir()

    result = _run(repo, destination, "--tree", "src/assistant")

    assert result.returncode == 2
    assert "not a regular file (mode 120000)" in result.stderr
    assert list(destination.iterdir()) == []


def test_destination_symlink_cannot_escape_bundle_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "web").mkdir()
    (repo / "web/handler.py").write_text("handler = object()\n", encoding="utf-8")
    _commit(repo)
    destination = tmp_path / "bundle"
    outside = tmp_path / "outside"
    destination.mkdir()
    outside.mkdir()
    (destination / "web").symlink_to(outside, target_is_directory=True)

    result = _run(repo, destination, "--file", "web/handler.py")

    assert result.returncode == 2
    assert "bundle destination traverses a symlink: web/handler.py" in result.stderr
    assert list(outside.iterdir()) == []


def test_deploy_selects_only_the_reviewed_first_party_runtime_inputs() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert "uv run python scripts/copy_tracked_bundle.py" in deploy
    assert 'cp -R "$ROOT/src/assistant"' not in deploy
    assert 'cp -R "$ROOT/prompts"' not in deploy
    for tree in ("src/assistant", "prompts"):
        assert f"--tree {tree}" in deploy
    for file_path in (
        "corpus/processed/chunks.jsonl",
        "docs/answer-contract.schema.json",
        "web/__init__.py",
        "web/handler.py",
        "web/index.html",
        "web/offline.py",
        "web/guide.py",
        "web/embed.py",
        "web/csp.py",
    ):
        assert f"--file {file_path}" in deploy
