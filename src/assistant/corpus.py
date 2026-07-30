"""Corpus identity, change tracking, and long-term retention.

The corpus is dated, but until now it had no single, stable name a deployment
could approve and pin to. `corpus_version` is that name: a short, deterministic
hash over the committed chunk content and fetch dates. It changes if and only if
the policy text or its dates change, so an operator can approve version X, set
`FPA_PINNED_CORPUS_VERSION=X`, and be told when a deploy is serving something
else (persona research R2-6, P12/P21).

`diff_corpus` is the other half: given the previous and current chunk sets, it
names which documents were added, removed, or changed. The weekly
corpus-freshness automation is the intended caller, turning a snapshot drift into
a human-readable changelog entry.

`corpus/raw/` and `corpus/processed/` are overwritten in place on every
`make fetch` / `make ingest`; only git history remembers what came before.
EXP-05 closes that gap: `archive_version` retains a processed-only, immutable
copy of every distinct corpus content hash under `corpus/versions/<id>/`, so a
past eval run's exact corpus stays loadable by version id long after the
working snapshot has moved on, and `changelog` can build the full "what changed
when" history from what is actually retained rather than a hand-seeded log.

    uv run python -m assistant.corpus            # print the current corpus summary
    uv run python -m assistant.corpus versions    # list retained corpus versions
    uv run python -m assistant.corpus changelog   # print the full retained changelog

`version_history` walks git history to build the changelog the agency operator
console (EXP-09, `web/console.py`) reviews: every committed corpus snapshot,
named by the same `corpus_version` hash, with its full chunk set so any two
versions can be diffed without a live checkout. It shells out to `git` and reads
the repo's commit history, so it only runs in development/CI (`make history`),
never inside the deployed Lambda — the console reads the resulting
`corpus/version_history.json` instead.

    uv run python -m assistant.corpus            # print the current corpus summary
    uv run python -m assistant.corpus history     # print the git-backed version history
"""

from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml

from assistant import config
from assistant.identity import content_version
from assistant.ingest import Chunk, load_manifest
from assistant.ingest import load_chunks as _load_current_chunks

# Manifest fields that identify a document (as opposed to crawl-policy knobs
# like user_agent/crawl_delay_seconds, which are not part of the corpus's
# retained identity).
_MANIFEST_DOC_FIELDS = ("id", "agency", "agency_full", "title", "url", "format", "language")
_LEGACY_ARCHIVE_FILES = frozenset({"chunks.jsonl", "manifest.snapshot.yaml", "version.json"})
_PROCESS_ARCHIVE_LOCK = threading.Lock()


class CorpusArchiveError(ValueError):
    """A legacy corpus archive is incomplete, corrupt, or conflicting."""


def _digest(parts: list[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def corpus_version(chunks: list[Chunk] | None = None) -> str:
    """A short, stable id for the corpus content. Order-independent: chunks are
    sorted, so a reprocess that only reorders does not change the version."""
    chunks = chunks if chunks is not None else load_chunks()
    parts: list[str] = []
    for c in sorted(chunks, key=lambda c: c.chunk_id):
        parts += [c.chunk_id, c.fetch_date, c.text]
    return _digest(parts)[:12]


def corpus_summary(chunks: list[Chunk] | None = None) -> dict:
    chunks = chunks if chunks is not None else load_chunks()
    return {
        "corpus_version": corpus_version(chunks),
        "content_version": content_version(chunks),
        "as_of": max((c.fetch_date for c in chunks), default=""),
        "agencies": sorted({c.agency for c in chunks}),
        "documents": len({c.doc_id for c in chunks}),
        "chunks": len(chunks),
    }


def _doc_hashes(chunks: list[Chunk]) -> dict[str, str]:
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    out: dict[str, str] = {}
    for doc_id, cs in by_doc.items():
        parts: list[str] = []
        for c in sorted(cs, key=lambda c: c.chunk_id):
            parts += [c.text, c.fetch_date]
        out[doc_id] = _digest(parts)[:12]
    return out


def diff_corpus(old: list[Chunk], new: list[Chunk]) -> dict:
    """Document-level changes between two corpus snapshots: added, removed, and
    changed doc-ids (a doc changed when its text or fetch date moved)."""
    o, n = _doc_hashes(old), _doc_hashes(new)
    return {
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "changed": sorted(d for d in set(o) & set(n) if o[d] != n[d]),
    }


# ── longitudinal retention (EXP-05) ─────────────────────────────────────────


def load_chunks(version: str | None = None) -> list[Chunk]:
    """The live processed corpus (`version=None`), or a retained past version's
    exact chunk set. Past versions are read from
    `corpus/versions/<version>/chunks.jsonl`, written once by `archive_version`
    when that `corpus_version` was first produced. Raises `FileNotFoundError`
    with the list of known versions if `version` was never archived."""
    if version is None:
        return _load_current_chunks()
    path = config.VERSIONS_DIR / version / "chunks.jsonl"
    if not path.exists():
        known = ", ".join(list_versions()) or "(none)"
        raise FileNotFoundError(
            f"corpus version {version!r} is not archived under {config.VERSIONS_DIR} "
            f"(known versions: {known})"
        )
    return _load_current_chunks(path)


def list_versions() -> list[str]:
    """Retained corpus versions, oldest first by `archived_at` (falling back to
    the version id itself for any archive whose `version.json` is missing or
    unreadable, so a hand-placed archive still sorts deterministically)."""
    if not config.VERSIONS_DIR.exists():
        return []
    entries: list[tuple[str, str]] = []
    for d in config.VERSIONS_DIR.iterdir():
        if d.name.startswith(".") or d.is_symlink() or not d.is_dir():
            continue
        archived_at = ""
        meta_path = d / "version.json"
        if meta_path.exists():
            try:
                archived_at = json.loads(meta_path.read_text(encoding="utf-8")).get(
                    "archived_at", ""
                )
            except (json.JSONDecodeError, OSError):
                pass
        entries.append((archived_at or d.name, d.name))
    entries.sort()
    return [name for _, name in entries]


def _legacy_chunks_bytes(chunks: list[Chunk]) -> bytes:
    return "".join(
        json.dumps(dataclasses.asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks
    ).encode("utf-8")


def _legacy_manifest_snapshot(manifest: Mapping[str, object]) -> dict[str, object]:
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusArchiveError("legacy archive manifest must contain documents")
    normalized: list[dict[str, object]] = []
    for index, document in enumerate(documents):
        if not isinstance(document, Mapping):
            raise CorpusArchiveError(f"legacy archive manifest document {index} must be a mapping")
        normalized.append({key: document.get(key) for key in _MANIFEST_DOC_FIELDS})
    return {"documents": normalized}


def _validate_legacy_manifest(
    manifest_snapshot: Mapping[str, object],
    chunks: list[Chunk],
) -> None:
    documents = manifest_snapshot.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusArchiveError("legacy archive manifest must contain documents")

    chunks_by_document: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(chunk.doc_id, []).append(chunk)
    manifest_ids: set[str] = set()
    for index, document in enumerate(documents):
        if not isinstance(document, Mapping):
            raise CorpusArchiveError(f"legacy archive manifest document {index} must be a mapping")
        doc_id = document.get("id")
        if not isinstance(doc_id, str) or not doc_id:
            raise CorpusArchiveError(f"legacy archive manifest document {index} has no valid id")
        if doc_id in manifest_ids:
            raise CorpusArchiveError(
                f"legacy archive manifest contains duplicate document id: {doc_id}"
            )
        manifest_ids.add(doc_id)
        document_chunks = chunks_by_document.get(doc_id)
        if not document_chunks:
            raise CorpusArchiveError(f"legacy archive manifest document {doc_id!r} has no chunks")
        expected = {
            "agency": document.get("agency"),
            "agency_full": document.get("agency_full"),
            "doc_title": document.get("title"),
            "url": document.get("url"),
            "language": document.get("language"),
        }
        for field, value in expected.items():
            if not isinstance(value, str) or not value:
                raise CorpusArchiveError(
                    f"legacy archive manifest document {doc_id!r} has no valid {field}"
                )
            if any(getattr(chunk, field) != value for chunk in document_chunks):
                raise CorpusArchiveError(
                    f"legacy archive manifest document {doc_id!r} conflicts with {field}"
                )
    unexpected = sorted(set(chunks_by_document) - manifest_ids)
    if unexpected:
        raise CorpusArchiveError(
            "legacy archive chunks are absent from the manifest: " + ", ".join(unexpected)
        )


def _read_regular_file(path: Path, context: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CorpusArchiveError(f"{context} is missing or is not a regular file")
    return path.read_bytes()


def _load_json_mapping(path: Path, context: str) -> Mapping[str, object]:
    try:
        payload = json.loads(_read_regular_file(path, context))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorpusArchiveError(f"{context} is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise CorpusArchiveError(f"{context} must be a JSON object")
    return payload


def _load_legacy_chunks(path: Path) -> list[Chunk]:
    try:
        text = _read_regular_file(path, "legacy archive chunks").decode("utf-8")
        return [Chunk(**json.loads(line)) for line in text.splitlines()]
    except (UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise CorpusArchiveError("legacy archive chunks are malformed or unreadable") from exc


def _validate_legacy_archive(
    path: Path,
    *,
    expected_version: str,
    expected_chunks: list[Chunk],
    expected_manifest: Mapping[str, object],
    require_version_directory_name: bool = True,
) -> None:
    """Validate every compatibility-archive artifact against the intended input."""
    if path.is_symlink() or not path.is_dir():
        raise CorpusArchiveError("legacy corpus archive is not a regular directory")
    entries = {entry.name for entry in path.iterdir()}
    if entries != _LEGACY_ARCHIVE_FILES:
        missing = sorted(_LEGACY_ARCHIVE_FILES - entries)
        unexpected = sorted(entries - _LEGACY_ARCHIVE_FILES)
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise CorpusArchiveError(
            "legacy corpus archive has an invalid artifact set (" + "; ".join(details) + ")"
        )
    if require_version_directory_name and path.name != expected_version:
        raise CorpusArchiveError("legacy corpus archive directory does not match its version")

    archived_chunks = _load_legacy_chunks(path / "chunks.jsonl")
    if archived_chunks != expected_chunks:
        raise CorpusArchiveError("legacy archive chunks conflict with the requested corpus")
    if corpus_version(archived_chunks) != expected_version:
        raise CorpusArchiveError("legacy archive chunks do not match corpus_version")

    manifest_bytes = _read_regular_file(
        path / "manifest.snapshot.yaml",
        "legacy archive manifest",
    )
    try:
        archived_manifest = yaml.safe_load(manifest_bytes)
    except yaml.YAMLError as exc:
        raise CorpusArchiveError("legacy archive manifest is invalid YAML") from exc
    if not isinstance(archived_manifest, Mapping):
        raise CorpusArchiveError("legacy archive manifest must be a mapping")
    if archived_manifest != expected_manifest:
        raise CorpusArchiveError("legacy archive manifest conflicts with the requested corpus")
    _validate_legacy_manifest(archived_manifest, archived_chunks)

    metadata = _load_json_mapping(path / "version.json", "legacy archive metadata")
    allowed_fields = {
        "corpus_version",
        "content_version",
        "as_of",
        "agencies",
        "documents",
        "chunks",
        "archived_at",
    }
    if not set(metadata).issubset(allowed_fields):
        raise CorpusArchiveError("legacy archive metadata has unexpected fields")
    required_fields = allowed_fields - {"content_version"}
    if not required_fields.issubset(metadata):
        raise CorpusArchiveError("legacy archive metadata is incomplete")

    expected_summary = corpus_summary(archived_chunks)
    for key in ("corpus_version", "as_of", "agencies", "documents", "chunks"):
        if metadata.get(key) != expected_summary[key]:
            raise CorpusArchiveError(f"legacy archive metadata field {key} does not match")
    if (
        "content_version" in metadata
        and metadata["content_version"] != expected_summary["content_version"]
    ):
        raise CorpusArchiveError("legacy archive metadata field content_version does not match")
    archived_at = metadata.get("archived_at")
    if not isinstance(archived_at, str):
        raise CorpusArchiveError("legacy archive archived_at must be an ISO timestamp")
    try:
        parsed_archived_at = datetime.fromisoformat(archived_at)
    except ValueError as exc:
        raise CorpusArchiveError("legacy archive archived_at must be an ISO timestamp") from exc
    if parsed_archived_at.tzinfo is None:
        raise CorpusArchiveError("legacy archive archived_at must include a timezone")


def _write_legacy_file(path: Path, content: bytes) -> None:
    """Durably create one staged compatibility artifact; also a test seam."""
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _legacy_archive_root_lock(root: Path):
    """Serialize cooperative compatibility-archive writers."""
    lock_path = root / ".archive.lock"
    with _PROCESS_ARCHIVE_LOCK:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _write_staged_legacy_archive(
    stage: Path,
    chunks: list[Chunk],
    manifest_snapshot: Mapping[str, object],
    *,
    archived_at: str,
) -> None:
    _write_legacy_file(stage / "chunks.jsonl", _legacy_chunks_bytes(chunks))
    _write_legacy_file(
        stage / "manifest.snapshot.yaml",
        yaml.safe_dump(dict(manifest_snapshot), sort_keys=False).encode("utf-8"),
    )
    summary = corpus_summary(chunks)
    summary["archived_at"] = archived_at
    _write_legacy_file(
        stage / "version.json",
        (json.dumps(summary, indent=2) + "\n").encode("utf-8"),
    )
    _fsync_directory(stage)


def archive_version(chunks: list[Chunk] | None = None, manifest: dict | None = None) -> str:
    """Atomically retain this corpus under ``corpus/versions/<legacy-id>/``.

    Publication is immutable and fail-closed: a complete archive is built and
    validated in a hidden same-filesystem stage before one atomic rename.
    Existing archives are never modified. Pre-layered archives whose valid
    ``version.json`` predates ``content_version`` remain accepted.
    """
    chunks = list(chunks if chunks is not None else _load_current_chunks())
    version = corpus_version(chunks)
    source_manifest = manifest if manifest is not None else _safe_load_manifest()
    if not isinstance(source_manifest, Mapping):
        raise CorpusArchiveError("legacy archive manifest must be a mapping")
    manifest_snapshot = _legacy_manifest_snapshot(source_manifest)
    _validate_legacy_manifest(manifest_snapshot, chunks)

    root = config.VERSIONS_DIR
    root.mkdir(parents=True, exist_ok=True)
    final = root / version
    with _legacy_archive_root_lock(root):
        if final.exists() or final.is_symlink():
            _validate_legacy_archive(
                final,
                expected_version=version,
                expected_chunks=chunks,
                expected_manifest=manifest_snapshot,
            )
            return version

    stage = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=root))
    try:
        _write_staged_legacy_archive(
            stage,
            chunks,
            manifest_snapshot,
            archived_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _validate_legacy_archive(
            stage,
            expected_version=version,
            expected_chunks=chunks,
            expected_manifest=manifest_snapshot,
            require_version_directory_name=False,
        )
        with _legacy_archive_root_lock(root):
            if final.exists() or final.is_symlink():
                _validate_legacy_archive(
                    final,
                    expected_version=version,
                    expected_chunks=chunks,
                    expected_manifest=manifest_snapshot,
                )
                return version
            os.rename(stage, final)
            _fsync_directory(root)
        _validate_legacy_archive(
            final,
            expected_version=version,
            expected_chunks=chunks,
            expected_manifest=manifest_snapshot,
        )
        return version
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _safe_load_manifest() -> dict:
    try:
        return load_manifest()
    except (OSError, yaml.YAMLError):
        return {}


def changelog(versions: list[str] | None = None) -> list[dict]:
    """The full retained history: a `diff_corpus` entry between each retained
    version and the one before it, oldest first. Chains across
    `list_versions()` (or an explicit ordered subset) so "what changed when" is
    generated from the retained chunk sets, not hand-seeded. The oldest version
    has no predecessor and starts the chain rather than appearing in it."""
    versions = versions if versions is not None else list_versions()
    entries: list[dict] = []
    for prev, cur in zip(versions, versions[1:], strict=False):
        old_chunks = load_chunks(prev)
        new_chunks = load_chunks(cur)
        entries.append(
            {
                "from_version": prev,
                "to_version": cur,
                "as_of": corpus_summary(new_chunks)["as_of"],
                **diff_corpus(old_chunks, new_chunks),
            }
        )
    return entries


def _run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=config.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _chunks_from_jsonl_text(text: str) -> list[Chunk]:
    return [Chunk(**json.loads(line)) for line in text.splitlines() if line.strip()]


def version_history(limit: int = 20) -> list[dict]:
    """Return committed corpus versions, newest first, for the operator console."""
    rel_path = str(config.CHUNKS_PATH.relative_to(config.REPO_ROOT))
    log = _run_git(["log", f"-{limit}", "--format=%H\t%cI", "--", rel_path])
    versions: list[dict] = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, committed_at = line.split("\t", 1)
        try:
            chunks = _chunks_from_jsonl_text(_run_git(["show", f"{sha}:{rel_path}"]))
        except (RuntimeError, TypeError, KeyError):
            continue
        versions.append(
            {
                "commit": sha[:12],
                "committed_at": committed_at,
                "corpus_version": corpus_version(chunks),
                "agencies": sorted({c.agency for c in chunks}),
                "documents": len({c.doc_id for c in chunks}),
                "chunks": [dataclasses.asdict(c) for c in chunks],
            }
        )
    return versions


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "summary"
    payload: object
    if cmd == "history":
        payload = {"generated_at": datetime.now(UTC).isoformat(), "versions": version_history()}
    elif cmd == "summary":
        payload = corpus_summary()
    elif cmd == "versions":
        payload = list_versions()
    elif cmd == "snapshots":
        from assistant.snapshots import list_snapshots

        payload = list_snapshots()
    elif cmd == "changelog":
        payload = changelog()
    else:
        print(
            f"unknown command: {cmd} (expected summary|versions|snapshots|changelog|history)",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
