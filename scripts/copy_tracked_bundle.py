"""Copy an explicit set of tracked regular files into a prepared bundle.

Deployment bundles must not depend on whatever happens to be present in a
developer's checkout.  In particular, recursively copying a source directory
can silently include ignored bytecode, local credentials, editor artifacts, or
other files that are not part of the reviewed Git revision.

This copier reads the repository index with ``git ls-files --stage -z`` and
copies only requested tracked trees and exact tracked files.  The worktree must
be clean, every requested scope must resolve to at least one index entry, and
selected entries must be ordinary files both in Git and on disk.  Symlinks,
submodules, unresolved index stages, missing paths, and unsafe destination
paths fail closed.  Worktree bytes must exactly match their indexed blobs; the
immutable blobs are then written so a later checkout mutation cannot enter the
artifact between validation and copy.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REGULAR_GIT_MODES = frozenset({"100644", "100755"})


class BundleCopyError(ValueError):
    """The reviewed bundle selection cannot be copied safely."""


@dataclass(frozen=True)
class IndexEntry:
    mode: str
    object_id: str
    stage: int
    path: PurePosixPath


def _git(repo_root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repo_root), *args],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise BundleCopyError(f"could not execute git: {exc}") from exc
    if result.returncode != 0:
        detail = os.fsdecode(result.stderr).strip() or f"exit status {result.returncode}"
        raise BundleCopyError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _repo_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise BundleCopyError(f"repository root is unavailable: {path}") from exc
    if not root.is_dir():
        raise BundleCopyError(f"repository root is not a directory: {root}")

    reported = os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()
    try:
        top_level = Path(reported).resolve(strict=True)
    except OSError as exc:
        raise BundleCopyError(f"Git reported an unavailable repository root: {reported}") from exc
    if top_level != root:
        raise BundleCopyError(
            f"--repo-root must be the Git worktree root ({top_level}), not {root}"
        )
    return root


def _scope(value: str, *, option: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise BundleCopyError(f"{option} must be a non-empty repository-relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BundleCopyError(f"unsafe {option} path: {value!r}")
    return path


def _index_entries(repo_root: Path) -> list[IndexEntry]:
    raw = _git(repo_root, "ls-files", "--stage", "-z")
    entries: list[IndexEntry] = []
    seen: set[PurePosixPath] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_id, raw_stage = metadata.decode("ascii").split()
            stage = int(raw_stage)
        except (UnicodeDecodeError, ValueError) as exc:
            raise BundleCopyError("git ls-files returned an invalid index record") from exc
        path_text = os.fsdecode(raw_path)
        path = _scope(path_text, option="tracked")
        if path in seen:
            raise BundleCopyError(f"index contains multiple stages for selected path: {path}")
        seen.add(path)
        entries.append(IndexEntry(mode=mode, object_id=object_id, stage=stage, path=path))
    return entries


def _assert_clean(repo_root: Path) -> None:
    status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        "-z",
    )
    if status:
        raise BundleCopyError("Git worktree is not clean; refusing an unreviewed bundle")


def _selected_entries(
    entries: Sequence[IndexEntry],
    *,
    trees: Sequence[PurePosixPath],
    files: Sequence[PurePosixPath],
) -> list[IndexEntry]:
    by_path = {entry.path: entry for entry in entries}
    selected: dict[PurePosixPath, IndexEntry] = {}

    for file_path in files:
        entry = by_path.get(file_path)
        if entry is None:
            raise BundleCopyError(f"exact tracked file is missing: {file_path}")
        selected[entry.path] = entry

    for tree in trees:
        prefix = f"{tree.as_posix()}/"
        matches = [entry for entry in entries if entry.path.as_posix().startswith(prefix)]
        if not matches:
            raise BundleCopyError(f"tracked tree contains no files: {tree}")
        selected.update((entry.path, entry) for entry in matches)

    if not selected:
        raise BundleCopyError("at least one --tree or --file selection is required")
    return [selected[path] for path in sorted(selected, key=PurePosixPath.as_posix)]


def _assert_regular_source(repo_root: Path, entry: IndexEntry) -> Path:
    if entry.stage != 0:
        raise BundleCopyError(f"tracked path has an unresolved index stage: {entry.path}")
    if entry.mode not in REGULAR_GIT_MODES:
        raise BundleCopyError(
            f"tracked path is not a regular file (mode {entry.mode}): {entry.path}"
        )

    current = repo_root
    for index, part in enumerate(entry.path.parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise BundleCopyError(f"tracked path is unavailable: {entry.path}") from exc
        if stat.S_ISLNK(mode):
            raise BundleCopyError(f"tracked path traverses a symlink: {entry.path}")
        if index < len(entry.path.parts) - 1 and not stat.S_ISDIR(mode):
            raise BundleCopyError(f"tracked path has a non-directory ancestor: {entry.path}")
        if index == len(entry.path.parts) - 1 and not stat.S_ISREG(mode):
            raise BundleCopyError(f"tracked path is not a regular worktree file: {entry.path}")
    return current


def _reviewed_blob(repo_root: Path, entry: IndexEntry, source: Path) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as stream:
            worktree_bytes = stream.read()
    except OSError as exc:
        raise BundleCopyError(f"could not read tracked regular file: {entry.path}") from exc

    blob_bytes = _git(repo_root, "cat-file", "blob", entry.object_id)
    if worktree_bytes != blob_bytes:
        raise BundleCopyError(f"worktree bytes differ from the reviewed Git blob: {entry.path}")
    return blob_bytes


def _assert_destination_root(destination: Path) -> Path:
    destination = Path(os.path.abspath(destination))
    try:
        mode = destination.lstat().st_mode
    except OSError as exc:
        raise BundleCopyError(f"bundle destination is unavailable: {destination}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise BundleCopyError(f"bundle destination must be a real directory: {destination}")
    return destination


def _assert_safe_target(destination: Path, relative: PurePosixPath) -> Path:
    current = destination
    for part in relative.parts[:-1]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BundleCopyError(f"bundle destination path is unavailable: {relative}") from exc
        if stat.S_ISLNK(mode):
            raise BundleCopyError(f"bundle destination traverses a symlink: {relative}")
        if not stat.S_ISDIR(mode):
            raise BundleCopyError(f"bundle destination has a non-directory ancestor: {relative}")

    target = destination.joinpath(*relative.parts)
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    except OSError as exc:
        raise BundleCopyError(f"bundle destination path is unavailable: {relative}") from exc
    raise BundleCopyError(f"bundle destination already contains selected path: {relative}")


def copy_tracked_bundle(
    repo_root: Path,
    destination: Path,
    *,
    trees: Sequence[str] = (),
    files: Sequence[str] = (),
) -> list[PurePosixPath]:
    """Copy selected tracked files and return their repository-relative paths."""
    root = _repo_root(repo_root)
    tree_paths = [_scope(value, option="--tree") for value in trees]
    file_paths = [_scope(value, option="--file") for value in files]
    _assert_clean(root)
    entries = _selected_entries(_index_entries(root), trees=tree_paths, files=file_paths)
    bundle_root = _assert_destination_root(destination)

    reviewed: list[tuple[IndexEntry, bytes]] = []
    for entry in entries:
        source = _assert_regular_source(root, entry)
        reviewed.append((entry, _reviewed_blob(root, entry, source)))
    _assert_clean(root)
    targets: list[tuple[IndexEntry, bytes, Path]] = [
        (entry, blob, _assert_safe_target(bundle_root, entry.path)) for entry, blob in reviewed
    ]

    for entry, blob, target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as stream:
                stream.write(blob)
        except OSError as exc:
            raise BundleCopyError(f"could not create bundle file: {entry.path}") from exc
        target.chmod(0o755 if entry.mode == "100755" else 0o644)
    return [entry.path for entry in entries]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path, help="Git worktree root")
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="existing prepared bundle root",
    )
    parser.add_argument(
        "--tree",
        action="append",
        default=[],
        help="tracked repository-relative directory to copy recursively (repeatable)",
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="exact tracked repository-relative file to copy (repeatable)",
    )
    args = parser.parse_args()
    try:
        copied = copy_tracked_bundle(
            args.repo_root,
            args.destination,
            trees=args.tree,
            files=args.file,
        )
    except (BundleCopyError, OSError) as exc:
        parser.exit(2, f"tracked bundle copy failed: {exc}\n")
    print(f"copied {len(copied)} tracked first-party file(s)")


if __name__ == "__main__":
    main()
