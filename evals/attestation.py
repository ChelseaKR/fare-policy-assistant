"""Deterministic, secret-free identities for evaluation evidence.

This module deliberately keeps evaluation identity separate from deployment
identity.  A release descriptor says what the rider service is; an evaluation
attestation says exactly what was evaluated, by which version of the harness,
against which complete cases and auxiliary evidence.

The evaluator source tree is intentionally exhaustive rather than hand-picked:
every ``*.py`` file recursively below ``evals/`` and ``src/assistant/`` is
receipted.  Prompt and answer-contract bytes are already covered by
``ConfigIdentity``; corpus bytes are covered by ``SnapshotIdentity``.

Legacy GTFS snapshots predate transactional ZIP receipts.  They can therefore
be identified only as exact extracted inputs (``legacy_extracted_only``), never as
proof of an upstream ZIP digest.  Missing configured inputs are represented
explicitly as ``unavailable``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from assistant.identity import SnapshotIdentity
from assistant.release_identity import (
    ConfigIdentity,
    ReleaseIdentityError,
    build_release_identity,
)

CASE_SEMANTICS_SCHEMA = "fare-assistant.eval-case-semantics.v1"
SUITE_SCHEMA = "fare-assistant.eval-suite.v1"
FACTS_SCHEMA = "fare-assistant.facts-eval-input.v1"
GTFS_LEGACY_INPUT_SCHEMA = "fare-assistant.gtfs-legacy-eval-input.v1"
EVALUATOR_SCHEMA = "fare-assistant.eval-evaluator.v1"
PROTOCOL_SCHEMA = "fare-assistant.eval-protocol.v1"
CONTEXT_SCHEMA = "fare-assistant.eval-context.v1"
ATTESTATION_SCHEMA = "fare-assistant.eval-attestation.v1"

# These are the only raw GTFS files read by the current eval path:
# assistant.gtfs.parse_fares reads the two fare tables and
# assistant.fare_table.load_rider_categories reads rider_categories.txt. Hash
# both fare schemas when both exist, even though the v2 parser takes precedence,
# so a malformed mixed legacy snapshot cannot hide a behavior-relevant input.
GTFS_LEGACY_CONSUMED_MEMBERS = (
    "fare_attributes.txt",
    "fare_products.txt",
    "rider_categories.txt",
)

# See the module docstring: this is an exhaustive tree rule, not a curated list.
EVALUATOR_SOURCE_TREES = (
    "evals",
    "src/assistant",
)

_SCHEMA = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+\.v[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GIT_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_LEGACY_CORPUS_VERSION = re.compile(r"^[0-9a-f]{12}$")
_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# An agency identifier is a path segment *and* the join key onto the prose
# corpus, and two of the corpus's eighteen agency names contain a space
# ("AC Transit", "Marin Transit"). Only that character was added for issue #141;
# the segment still starts and ends alphanumeric, so it cannot be "." or ".."
# and cannot carry leading or trailing whitespace, and it still admits no path
# separator. `assistant.gtfs._AGENCY` enforces the same shape on the write side.
_SAFE_AGENCY_PART = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]*[A-Za-z0-9])?$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:"
    r"api[_-]?key|secret|password|passwd|credential|authorization|cookie|"
    r"session|private[_-]?key|access[_-]?key|security[_-]?token|"
    r"environment|environ|env|headers?"
    r")(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:"
    r"\bBearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bsk-ant-[A-Za-z0-9_-]+"
    r")"
)
_SUBJECT_FIELDS = frozenset(
    {
        "source_state",
        "head_revision",
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "descriptor_verified",
    }
)
_PROMOTION_FIELDS = frozenset(
    {
        "eligible",
        "live",
        "uncached",
        "judges_ran",
        "gates_passed",
        "reasons",
        "evaluated_at",
    }
)


class EvalAttestationError(ValueError):
    """An eval identity or attestation input is incomplete or unsafe."""


def _canonical_value(
    value: object,
    context: str = "payload",
    active: set[int] | None = None,
) -> object:
    """Return a plain, recursively key-sorted JSON value.

    Container cycles, non-string object keys, non-finite floats, and values
    with no unambiguous JSON representation fail closed.
    """

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise EvalAttestationError(f"{context} contains a non-finite float")
        return value

    seen = active if active is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise EvalAttestationError(f"{context} contains a container cycle")
        seen.add(marker)
        try:
            if any(not isinstance(key, str) for key in value):
                raise EvalAttestationError(f"{context} object keys must be strings")
            return {
                key: _canonical_value(value[key], f"{context}.{key}", seen) for key in sorted(value)
            }
        finally:
            seen.remove(marker)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        marker = id(value)
        if marker in seen:
            raise EvalAttestationError(f"{context} contains a container cycle")
        seen.add(marker)
        try:
            return [
                _canonical_value(item, f"{context}[{index}]", seen)
                for index, item in enumerate(value)
            ]
        finally:
            seen.remove(marker)

    raise EvalAttestationError(
        f"{context} contains unsupported JSON value type {type(value).__name__}"
    )


def _canonical_json(payload: object) -> bytes:
    normalized = _canonical_value(payload)
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # defensive: normalization owns validation
        raise EvalAttestationError("payload is not canonical-JSON compatible") from exc


def canonical_digest(schema: str, payload: object) -> str:
    """Return SHA-256 of ``ASCII schema + NUL + canonical JSON payload``."""

    if not isinstance(schema, str) or not _SCHEMA.fullmatch(schema):
        raise EvalAttestationError(
            "schema must be a canonical lowercase, versioned identifier ending in .vN"
        )
    framed = schema.encode("ascii") + b"\0" + _canonical_json(payload)
    return hashlib.sha256(framed).hexdigest()


def _reject_dot_segments(path: Path, context: str) -> None:
    if any(part in {".", ".."} for part in path.parts):
        raise EvalAttestationError(f"{context} must not contain dot path segments")


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _regular_directory(path: Path, context: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EvalAttestationError(f"{context} is missing or unreadable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EvalAttestationError(f"{context} must be a real directory, not a symlink: {path}")


def _anchored_file(path: Path, root: Path | None) -> tuple[Path, Path | None]:
    _reject_dot_segments(path, "file path")
    if root is None:
        return _absolute_lexical(path), None

    _reject_dot_segments(root, "root path")
    root_path = _absolute_lexical(root)
    _regular_directory(root_path, "root")
    candidate = path if path.is_absolute() else root_path / path
    candidate = _absolute_lexical(candidate)
    try:
        relative = candidate.relative_to(root_path)
    except ValueError as exc:
        raise EvalAttestationError(f"file escapes its declared root: {candidate}") from exc
    if relative == Path("."):
        raise EvalAttestationError("file path resolves to its directory root, not a regular file")

    current = root_path
    for part in relative.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise EvalAttestationError(f"file parent is missing or unreadable: {current}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise EvalAttestationError(f"file parent must be a real directory: {current}")
    return candidate, root_path


def file_receipt(path: Path | str, *, root: Path | str | None = None) -> dict[str, object]:
    """Hash the exact bytes of one regular file without following symlinks."""

    candidate, _ = _anchored_file(
        Path(path),
        Path(root) if root is not None else None,
    )
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise EvalAttestationError(
            f"file is missing or is not a readable regular file: {candidate}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise EvalAttestationError(f"file is not a regular file: {candidate}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise EvalAttestationError(f"file could not be opened safely: {candidate}") from exc

    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise EvalAttestationError(f"opened path is not a regular file: {candidate}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    except OSError as exc:
        raise EvalAttestationError(f"file could not be read completely: {candidate}") from exc
    finally:
        os.close(descriptor)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EvalAttestationError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise EvalAttestationError(f"{context} object keys must be strings")
    return value


def _exact_fields(
    value: object,
    expected: frozenset[str],
    context: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    actual = set(mapping)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise EvalAttestationError(f"{context} has an invalid field set ({'; '.join(details)})")
    return mapping


def _trimmed_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvalAttestationError(f"{context} must be a non-empty, trimmed string")
    return value


def _sha256(value: object, context: str) -> str:
    digest = _trimmed_string(value, context)
    if not _SHA256.fullmatch(digest):
        raise EvalAttestationError(f"{context} must be a 64-character lowercase SHA-256")
    return digest


def case_semantics_version(case: Mapping[str, object]) -> str:
    """Hash every field of one complete, post-flatten evaluation case."""

    normalized = _canonical_value(_mapping(case, "case"), "case")
    return canonical_digest(CASE_SEMANTICS_SCHEMA, normalized)


def case_manifest(cases: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    """Receipt an ordered sequence of complete, post-flatten cases."""

    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes, bytearray, memoryview)):
        raise EvalAttestationError("cases must be an ordered sequence")
    if not cases:
        raise EvalAttestationError("cases must not be empty")

    manifest: list[dict[str, str]] = []
    identifiers: set[str] = set()
    for index, case in enumerate(cases):
        mapping = _mapping(case, f"cases[{index}]")
        identifier = _trimmed_string(mapping.get("id"), f"cases[{index}].id")
        if not _CASE_ID.fullmatch(identifier):
            raise EvalAttestationError(
                f"cases[{index}].id must be a 1-128 character safe identifier"
            )
        if identifier in identifiers:
            raise EvalAttestationError(f"duplicate case id: {identifier}")
        identifiers.add(identifier)
        manifest.append(
            {
                "case_id": identifier,
                "case_semantics_version": case_semantics_version(mapping),
            }
        )
    return manifest


def _case_manifest_record(
    value: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        raise EvalAttestationError("case_manifest must be an ordered sequence")
    if not value:
        raise EvalAttestationError("case_manifest must not be empty")

    normalized: list[dict[str, str]] = []
    identifiers: set[str] = set()
    expected_fields = frozenset({"case_id", "case_semantics_version"})
    for index, entry in enumerate(value):
        record = _exact_fields(entry, expected_fields, f"case_manifest[{index}]")
        identifier = _trimmed_string(
            record["case_id"],
            f"case_manifest[{index}].case_id",
        )
        if not _CASE_ID.fullmatch(identifier):
            raise EvalAttestationError(
                f"case_manifest[{index}].case_id must be a 1-128 character safe identifier"
            )
        if identifier in identifiers:
            raise EvalAttestationError(f"duplicate case id in case_manifest: {identifier}")
        identifiers.add(identifier)
        normalized.append(
            {
                "case_id": identifier,
                "case_semantics_version": _sha256(
                    record["case_semantics_version"],
                    f"case_manifest[{index}].case_semantics_version",
                ),
            }
        )
    return normalized


def _suite_version_from_manifest(manifest: Sequence[Mapping[str, object]]) -> str:
    return canonical_digest(SUITE_SCHEMA, {"case_manifest": manifest})


def suite_version(cases: Sequence[Mapping[str, object]]) -> str:
    """Hash the ordered manifest of complete, post-flatten cases."""

    return _suite_version_from_manifest(case_manifest(cases))


def facts_identity(path: Path | str) -> dict[str, object]:
    """Return a schema-framed identity over the exact ``facts.jsonl`` bytes."""

    selected = Path(path)
    receipt = file_receipt(selected, root=selected.parent)
    version = canonical_digest(FACTS_SCHEMA, {"receipt": receipt})
    return {
        "schema": FACTS_SCHEMA,
        "facts_version": version,
        "receipt": receipt,
    }


def _safe_agency(value: object, context: str) -> str:
    agency = _trimmed_string(value, context)
    if agency in {".", ".."} or not _SAFE_AGENCY_PART.fullmatch(agency):
        raise EvalAttestationError(
            f"{context} must be one safe path segment containing letters, digits, "
            "spaces, '.', '_', or '-'"
        )
    return agency


def _configured_feeds(
    manifest: Mapping[str, object] | Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    raw: object
    if isinstance(manifest, Mapping):
        if "gtfs_feeds" not in manifest:
            raise EvalAttestationError("manifest is missing gtfs_feeds")
        raw = manifest["gtfs_feeds"]
    else:
        raw = manifest
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray, memoryview)):
        raise EvalAttestationError("gtfs_feeds must be an array")

    feeds: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, feed in enumerate(raw):
        mapping = _mapping(feed, f"gtfs_feeds[{index}]")
        agency = _safe_agency(mapping.get("agency"), f"gtfs_feeds[{index}].agency")
        if agency in seen:
            raise EvalAttestationError(f"duplicate configured GTFS agency: {agency}")
        seen.add(agency)
        normalized = _canonical_value(mapping, f"gtfs_feeds[{index}]")
        assert isinstance(normalized, dict)
        feeds.append(normalized)
    return sorted(feeds, key=lambda feed: str(feed["agency"]))


def gtfs_legacy_input_identity(
    manifest: Mapping[str, object] | Sequence[Mapping[str, object]],
    gtfs_root: Path | str,
) -> dict[str, object]:
    """Identify exact legacy extracted GTFS inputs without claiming a ZIP SHA."""

    feeds = _configured_feeds(manifest)
    root = _absolute_lexical(Path(gtfs_root))
    _reject_dot_segments(Path(gtfs_root), "GTFS root")
    if root.exists() or root.is_symlink():
        _regular_directory(root, "GTFS root")

    agencies: list[dict[str, object]] = []
    for feed in feeds:
        agency = str(feed["agency"])
        agency_dir = root / agency
        if agency_dir.exists() or agency_dir.is_symlink():
            _regular_directory(agency_dir, f"GTFS agency directory {agency}")

        files: list[dict[str, object]] = []
        if agency_dir.is_dir():
            for member in GTFS_LEGACY_CONSUMED_MEMBERS:
                candidate = agency_dir / member
                if not candidate.exists() and not candidate.is_symlink():
                    continue
                receipt = file_receipt(candidate, root=root)
                files.append({"path": f"{agency}/{member}", **receipt})
        agencies.append(
            {
                "agency": agency,
                "state": "legacy_extracted_only" if files else "unavailable",
                "files": files,
            }
        )

    payload = {
        "gtfs_feeds": feeds,
        "consumed_members": list(GTFS_LEGACY_CONSUMED_MEMBERS),
        "agencies": agencies,
    }
    version = canonical_digest(GTFS_LEGACY_INPUT_SCHEMA, payload)
    return {
        "schema": GTFS_LEGACY_INPUT_SCHEMA,
        "gtfs_input_version": version,
        "consumed_members": list(GTFS_LEGACY_CONSUMED_MEMBERS),
        "agencies": agencies,
    }


def _safe_source_tree(value: object, context: str) -> str:
    tree = _trimmed_string(value, context)
    path = Path(tree)
    _reject_dot_segments(path, context)
    if path.is_absolute() or not path.parts:
        raise EvalAttestationError(f"{context} must be a safe repository-relative path")
    if any(not _SAFE_PATH_PART.fullmatch(part) for part in path.parts):
        raise EvalAttestationError(f"{context} contains an unsafe path segment")
    return path.as_posix()


def evaluator_identity(
    repo_root: Path | str,
    *,
    source_trees: Sequence[str] = EVALUATOR_SOURCE_TREES,
) -> dict[str, object]:
    """Receipt every Python source in the documented evaluator source trees."""

    root = _absolute_lexical(Path(repo_root))
    _reject_dot_segments(Path(repo_root), "repository root")
    _regular_directory(root, "repository root")
    if not isinstance(source_trees, Sequence) or isinstance(
        source_trees, (str, bytes, bytearray, memoryview)
    ):
        raise EvalAttestationError("source_trees must be an ordered sequence")

    trees = [
        _safe_source_tree(value, f"source_trees[{index}]")
        for index, value in enumerate(source_trees)
    ]
    if len(trees) != len(set(trees)):
        raise EvalAttestationError("source_trees must not contain duplicates")
    trees.sort()

    files: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for tree in trees:
        tree_path = root / Path(tree)
        _regular_directory(tree_path, f"evaluator source tree {tree}")
        for current, directory_names, file_names in os.walk(
            tree_path, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                child = current_path / name
                if child.is_symlink():
                    raise EvalAttestationError(
                        f"evaluator source tree contains a directory symlink: {child}"
                    )
                if name != "__pycache__":
                    kept_directories.append(name)
            directory_names[:] = kept_directories

            for name in sorted(file_names):
                if not name.endswith(".py"):
                    continue
                candidate = current_path / name
                relative = candidate.relative_to(root).as_posix()
                if relative in seen_paths:
                    raise EvalAttestationError(f"evaluator source trees overlap at {relative}")
                seen_paths.add(relative)
                receipt = file_receipt(candidate, root=root)
                files.append({"path": relative, **receipt})

    files.sort(key=lambda receipt: str(receipt["path"]))
    if not files:
        raise EvalAttestationError("evaluator source trees contain no Python files")
    payload = {
        "source_trees": trees,
        "file_rule": "recursive-*.py-excluding-__pycache__",
        "files": files,
    }
    version = canonical_digest(EVALUATOR_SCHEMA, payload)
    return {
        "schema": EVALUATOR_SCHEMA,
        "evaluator_version": version,
        **payload,
    }


def _git(repo_root: Path, *arguments: str) -> str:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "LC_ALL": "C",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvalAttestationError(
            f"could not inspect Git source state with: git {' '.join(arguments)}"
        ) from exc
    return result.stdout


def git_source_status(repo_root: Path | str) -> dict[str, object]:
    """Return clean/dirty Git semantics without asserting a false revision."""

    root = _absolute_lexical(Path(repo_root))
    _reject_dot_segments(Path(repo_root), "repository root")
    _regular_directory(root, "repository root")
    head = _git(root, "rev-parse", "--verify", "HEAD").strip()
    if not _GIT_REVISION.fullmatch(head):
        raise EvalAttestationError("Git HEAD is not a full lowercase object ID")
    dirty = bool(_git(root, "status", "--porcelain=v1", "--untracked-files=normal"))
    return {
        "source_state": "dirty" if dirty else "clean",
        "head_revision": head,
        "source_revision": None if dirty else head,
    }


def _reject_sensitive(value: object, context: str = "protocol") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvalAttestationError(f"{context} object keys must be strings")
            if _SENSITIVE_KEY.search(key) or key.lower() in {
                "token",
                "tokens",
                "auth",
                "authentication",
            }:
                raise EvalAttestationError(f"{context} contains forbidden sensitive field {key!r}")
            _reject_sensitive(item, f"{context}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        for index, item in enumerate(value):
            _reject_sensitive(item, f"{context}[{index}]")
    elif isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        raise EvalAttestationError(f"{context} appears to contain secret material")


def _protocol_record(protocol: Mapping[str, object]) -> dict[str, object]:
    mapping = _mapping(protocol, "protocol")
    if not mapping:
        raise EvalAttestationError("protocol must not be empty")
    if "protocol_version" in mapping:
        raise EvalAttestationError("protocol_version is computed, not caller-supplied")
    _reject_sensitive(mapping)
    normalized = _canonical_value(mapping, "protocol")
    assert isinstance(normalized, dict)
    version = canonical_digest(PROTOCOL_SCHEMA, normalized)
    return {**normalized, "protocol_version": version}


def _subject_record(
    subject: Mapping[str, object],
    *,
    config_identity: ConfigIdentity | None,
    snapshot_identity: SnapshotIdentity | None,
) -> dict[str, object]:
    raw = _exact_fields(subject, _SUBJECT_FIELDS, "subject")
    state = raw["source_state"]
    if state not in {"clean", "dirty"}:
        raise EvalAttestationError("subject.source_state must be 'clean' or 'dirty'")

    head = _trimmed_string(raw["head_revision"], "subject.head_revision")
    if not _GIT_REVISION.fullmatch(head):
        raise EvalAttestationError("subject.head_revision must be a full lowercase Git object ID")
    source = raw["source_revision"]
    if state == "clean":
        if source != head:
            raise EvalAttestationError(
                "a clean subject.source_revision must equal subject.head_revision"
            )
    elif source is not None:
        raise EvalAttestationError("a dirty subject.source_revision must be null")

    config_version = _sha256(raw["config_version"], "subject.config_version")
    content_version = _sha256(raw["content_version"], "subject.content_version")
    snapshot_version = _sha256(raw["snapshot_version"], "subject.snapshot_version")
    release_raw = raw["release_version"]
    if state == "dirty":
        if release_raw is not None:
            raise EvalAttestationError("a dirty subject.release_version must be null")
        release_version = None
    else:
        release_version = _sha256(release_raw, "subject.release_version")
    corpus_version = _trimmed_string(raw["corpus_version"], "subject.corpus_version")
    if not _LEGACY_CORPUS_VERSION.fullmatch(corpus_version):
        raise EvalAttestationError(
            "subject.corpus_version must be a 12-character lowercase compatibility digest"
        )
    if type(raw["descriptor_verified"]) is not bool:
        raise EvalAttestationError("subject.descriptor_verified must be a boolean")

    if config_identity is not None:
        if not isinstance(config_identity, ConfigIdentity):
            raise EvalAttestationError("config_identity must be a validated ConfigIdentity")
        if config_version != config_identity.config_version:
            raise EvalAttestationError("subject.config_version does not match config_identity")
    if snapshot_identity is not None:
        if not isinstance(snapshot_identity, SnapshotIdentity):
            raise EvalAttestationError("snapshot_identity must be a validated SnapshotIdentity")
        if (
            content_version != snapshot_identity.content_version
            or snapshot_version != snapshot_identity.snapshot_version
        ):
            raise EvalAttestationError("subject evidence versions do not match snapshot_identity")

    # A verified descriptor must name the deterministic release tuple.  Dirty
    # worktrees use HEAD only for this baseline check; they still never gain a
    # source_revision or promotion eligibility.
    if raw["descriptor_verified"]:
        if state != "clean":
            raise EvalAttestationError("a dirty subject cannot verify a release descriptor")
        assert release_version is not None
        if len(head) != 40:
            raise EvalAttestationError(
                "verified release descriptors currently require a SHA-1 Git object ID"
            )
        try:
            expected_release = build_release_identity(
                head,
                config_version,
                content_version=content_version,
                snapshot_version=snapshot_version,
            ).release_version
        except ReleaseIdentityError as exc:
            raise EvalAttestationError("subject release tuple is invalid") from exc
        if not hmac.compare_digest(release_version, expected_release):
            raise EvalAttestationError(
                "subject.release_version does not match the verified release tuple"
            )

    return {
        "source_state": state,
        "head_revision": head,
        "source_revision": source,
        "config_version": config_version,
        "content_version": content_version,
        "snapshot_version": snapshot_version,
        "release_version": release_version,
        "corpus_version": corpus_version,
        "descriptor_verified": raw["descriptor_verified"],
    }


def _utc_timestamp(value: object, context: str) -> str:
    timestamp = _trimmed_string(value, context)
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvalAttestationError(f"{context} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise EvalAttestationError(f"{context} must include an explicit UTC offset")
    return timestamp


def _promotion_record(
    promotion: Mapping[str, object],
    subject: Mapping[str, object],
) -> dict[str, object]:
    raw = _exact_fields(promotion, _PROMOTION_FIELDS, "promotion")
    flags = ("eligible", "live", "uncached", "judges_ran", "gates_passed")
    for field in flags:
        if type(raw[field]) is not bool:
            raise EvalAttestationError(f"promotion.{field} must be a boolean")

    reasons_value = raw["reasons"]
    if not isinstance(reasons_value, Sequence) or isinstance(
        reasons_value, (str, bytes, bytearray, memoryview)
    ):
        raise EvalAttestationError("promotion.reasons must be an array")
    reasons = [
        _trimmed_string(reason, f"promotion.reasons[{index}]")
        for index, reason in enumerate(reasons_value)
    ]
    if len(reasons) != len(set(reasons)):
        raise EvalAttestationError("promotion.reasons must not contain duplicates")
    reasons.sort()

    eligible = bool(raw["eligible"])
    prerequisites = (
        subject["source_state"] == "clean",
        bool(subject["descriptor_verified"]),
        bool(raw["live"]),
        bool(raw["uncached"]),
        bool(raw["judges_ran"]),
        bool(raw["gates_passed"]),
    )
    if eligible and not all(prerequisites):
        raise EvalAttestationError(
            "promotion.eligible cannot be true unless source, descriptor, live, "
            "uncached, judge, and gate prerequisites all pass"
        )
    if eligible and reasons:
        raise EvalAttestationError("an eligible promotion must not carry rejection reasons")
    if not eligible and not reasons:
        raise EvalAttestationError("an ineligible promotion must explain at least one reason")

    return {
        "eligible": eligible,
        "live": raw["live"],
        "uncached": raw["uncached"],
        "judges_ran": raw["judges_ran"],
        "gates_passed": raw["gates_passed"],
        "reasons": reasons,
        "evaluated_at": _utc_timestamp(raw["evaluated_at"], "promotion.evaluated_at"),
    }


def build_attestation(
    *,
    subject: Mapping[str, object],
    suite_version: str,
    case_manifest: Sequence[Mapping[str, object]],
    facts_version: str,
    gtfs_input_version: str,
    protocol: Mapping[str, object],
    promotion: Mapping[str, object],
    config_identity: ConfigIdentity | None = None,
    snapshot_identity: SnapshotIdentity | None = None,
) -> dict[str, object]:
    """Build a sanitized evaluation attestation.

    ``context_version`` is stable across wall-clock time and run outcomes, so
    cache keys can bind to it.  ``attestation_version`` additionally covers the
    complete promotion result and evaluation timestamp.  Only public digests
    from the validated identity objects are serialized; their payloads,
    observations, environment, and credentials are never copied.
    """

    normalized_subject = _subject_record(
        subject,
        config_identity=config_identity,
        snapshot_identity=snapshot_identity,
    )
    normalized_case_manifest = _case_manifest_record(case_manifest)
    normalized_suite_version = _sha256(suite_version, "suite_version")
    expected_suite_version = _suite_version_from_manifest(normalized_case_manifest)
    if not hmac.compare_digest(normalized_suite_version, expected_suite_version):
        raise EvalAttestationError("suite_version does not match the exact ordered case_manifest")
    evidence = {
        "suite_version": normalized_suite_version,
        "case_count": len(normalized_case_manifest),
        "case_manifest": normalized_case_manifest,
        "facts_version": _sha256(facts_version, "facts_version"),
        "gtfs_input_version": _sha256(gtfs_input_version, "gtfs_input_version"),
    }
    normalized_protocol = _protocol_record(protocol)
    normalized_promotion = _promotion_record(promotion, normalized_subject)

    context = {
        "subject": normalized_subject,
        "evidence": evidence,
        "protocol": normalized_protocol,
    }
    context_version = canonical_digest(CONTEXT_SCHEMA, context)
    attestation_without_version = {
        "attestation_schema": ATTESTATION_SCHEMA,
        **context,
        "promotion": normalized_promotion,
        "context_version": context_version,
    }
    attestation_version = canonical_digest(
        ATTESTATION_SCHEMA,
        attestation_without_version,
    )
    return {
        **attestation_without_version,
        "attestation_version": attestation_version,
    }
