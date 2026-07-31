"""Schema-2 source-complete corpus snapshot archives."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from assistant import config, snapshots
from assistant.ingest import Chunk
from assistant.snapshots import (
    ARCHIVE_SCHEMA,
    EVIDENCE_SCHEMA,
    SnapshotArchiveError,
    archive_snapshot,
    capture_source_material,
    collect_snapshot_material,
    list_snapshots,
    load_snapshot_chunks,
    validate_snapshot_archive,
)
from tests.conftest import make_chunk

_ARCHIVED_AT = "2026-07-30T12:34:56+00:00"
_BASELINE_SNAPSHOT = "b4834677a41abee7c6d1933ffd1f337e22fcadbc5f88442ccfc2b7ee3a84d59e"


def _manifest_document(chunk: Chunk, *, explicit_format: str | None = None) -> dict:
    document = {
        "id": chunk.doc_id,
        "agency": chunk.agency,
        "agency_full": chunk.agency_full,
        "title": chunk.doc_title,
        "url": chunk.url,
        "language": chunk.language,
    }
    if explicit_format is not None:
        document["format"] = explicit_format
    return document


def _metadata(chunk: Chunk, raw: bytes, *, effective_format: str = "html") -> dict:
    return {
        "doc_id": chunk.doc_id,
        "url": chunk.url,
        "final_url": chunk.url,
        "fetch_date": chunk.fetch_date,
        "http_status": 200,
        "format": effective_format,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _write_source(
    raw_dir: Path,
    chunk: Chunk,
    raw: bytes,
    *,
    effective_format: str = "html",
) -> bytes:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{chunk.doc_id}.{effective_format}").write_bytes(raw)
    metadata_bytes = yaml.safe_dump(
        _metadata(chunk, raw, effective_format=effective_format),
        sort_keys=False,
    ).encode("utf-8")
    (raw_dir / f"{chunk.doc_id}.meta.yaml").write_bytes(metadata_bytes)
    return metadata_bytes


def _rewrite_metadata(raw_dir: Path, source_doc_id: str, **changes: object) -> None:
    path = raw_dir / f"{source_doc_id}.meta.yaml"
    metadata = yaml.safe_load(path.read_bytes())
    metadata.update(changes)
    path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")


@pytest.fixture
def snapshot_case(tmp_path):
    mst = make_chunk(
        chunk_id="mst-fares#0",
        section="MST fares",
        text="MST's published single-ride fare is $2.00.",
    )
    sbmtd = make_chunk(
        chunk_id="sbmtd-fares-passes#0",
        doc_id="sbmtd-fares-passes",
        agency="SBMTD",
        agency_full="Santa Barbara Metropolitan Transit District",
        doc_title="Fares & Passes",
        url="https://sbmtd.gov/fares-passes/",
        section="Cash fares",
        text="SBMTD's published cash fare is $2.50.",
    )
    # Deliberately not doc-id sorted: chunk order affects retrieval tie-breaking
    # and must survive the archive round trip exactly.
    chunks = [sbmtd, mst]
    manifest = {
        "documents": [
            _manifest_document(sbmtd),
            _manifest_document(mst),
        ]
    }
    raw_dir = tmp_path / "raw"
    source_bytes = {
        mst.doc_id: b"<html><main>MST fare policy</main></html>",
        sbmtd.doc_id: b"<html><main>SBMTD fare policy</main></html>",
    }
    metadata_bytes = {
        chunk.doc_id: _write_source(raw_dir, chunk, source_bytes[chunk.doc_id]) for chunk in chunks
    }
    return {
        "chunks": chunks,
        "manifest": manifest,
        "raw_dir": raw_dir,
        "snapshots_dir": tmp_path / "snapshots",
        "source_bytes": source_bytes,
        "metadata_bytes": metadata_bytes,
    }


def _archive(case, *, archived_at: str = _ARCHIVED_AT):
    return archive_snapshot(
        case["chunks"],
        case["manifest"],
        raw_dir=case["raw_dir"],
        snapshots_dir=case["snapshots_dir"],
        archived_at=archived_at,
    )


def _visible_entries(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if not path.name.startswith("."))


def test_committed_baseline_snapshot_revalidates_after_git_checkout():
    identity = validate_snapshot_archive(config.SNAPSHOTS_DIR / _BASELINE_SNAPSHOT)

    assert identity.snapshot_version == _BASELINE_SNAPSHOT
    assert (
        identity.content_version
        == "58deec7dfdd250d2890c03510230c70618fb34e56d6b982ca2487af69379aa95"
    )


def test_archive_round_trip_retains_artifacts_raw_receipts_and_chunk_order(snapshot_case):
    identity = _archive(snapshot_case)
    archive = snapshot_case["snapshots_dir"] / identity.snapshot_version

    assert archive.name == identity.snapshot_version
    assert len(archive.name) == 64
    assert validate_snapshot_archive(archive) == identity
    assert (
        load_snapshot_chunks(identity.snapshot_version, snapshot_case["snapshots_dir"])
        == (snapshot_case["chunks"])
    )
    assert list_snapshots(snapshot_case["snapshots_dir"]) == [identity.snapshot_version]

    version = json.loads((archive / "version.json").read_text(encoding="utf-8"))
    assert version["archive_schema"] == ARCHIVE_SCHEMA
    assert version["scope"] == "web_policy"
    assert version["content_version"] == identity.content_version
    assert version["snapshot_version"] == identity.snapshot_version
    assert version["content_version_short"] == identity.content_version[:12]
    assert version["snapshot_version_short"] == identity.snapshot_version[:12]
    assert version["archived_at"] == _ARCHIVED_AT
    assert version["documents"] == 2
    assert version["chunks"] == 2
    assert version["evidence"] == "raw_and_receipts_verified"

    expected_artifacts = {
        "chunks.jsonl",
        "manifest.snapshot.yaml",
        "source-evidence.json",
        "raw/mst-fares.html",
        "raw/mst-fares.meta.yaml",
        "raw/sbmtd-fares-passes.html",
        "raw/sbmtd-fares-passes.meta.yaml",
    }
    assert set(version["artifacts"]) == expected_artifacts
    for relative, receipt in version["artifacts"].items():
        artifact = (archive / relative).read_bytes()
        assert receipt == {
            "sha256": hashlib.sha256(artifact).hexdigest(),
            "bytes": len(artifact),
        }

    for doc_id, expected in snapshot_case["source_bytes"].items():
        assert (archive / "raw" / f"{doc_id}.html").read_bytes() == expected
    for doc_id, expected in snapshot_case["metadata_bytes"].items():
        assert (archive / "raw" / f"{doc_id}.meta.yaml").read_bytes() == expected

    chunk_rows = [
        json.loads(line)
        for line in (archive / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["chunk_id"] for row in chunk_rows] == [
        chunk.chunk_id for chunk in snapshot_case["chunks"]
    ]

    manifest = yaml.safe_load((archive / "manifest.snapshot.yaml").read_bytes())
    assert [document["id"] for document in manifest["documents"]] == [
        "mst-fares",
        "sbmtd-fares-passes",
    ]
    assert all(document["format"] == "html" for document in manifest["documents"])

    evidence = json.loads((archive / "source-evidence.json").read_text(encoding="utf-8"))
    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert [row["doc_id"] for row in evidence["observations"]] == [
        "mst-fares",
        "sbmtd-fares-passes",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda manifest, chunks: manifest["documents"].append(
                _manifest_document(
                    make_chunk(
                        chunk_id="extra-policy#0",
                        doc_id="extra-policy",
                        agency="Extra",
                        agency_full="Extra Transit",
                        doc_title="Extra policy",
                        url="https://example.org/extra/",
                    )
                )
            ),
            "zero chunks",
        ),
        (
            lambda manifest, chunks: manifest["documents"].pop(),
            "chunks absent from manifest",
        ),
        (
            lambda manifest, chunks: manifest["documents"][0].update(
                {"title": "A conflicting title"}
            ),
            "does not match chunk field doc_title",
        ),
        (
            lambda manifest, chunks: manifest["documents"].append(dict(manifest["documents"][0])),
            "duplicate document id",
        ),
    ],
)
def test_manifest_mismatches_fail_before_any_archive_is_visible(
    snapshot_case,
    mutation,
    message,
):
    mutation(snapshot_case["manifest"], snapshot_case["chunks"])

    with pytest.raises(SnapshotArchiveError, match=message):
        _archive(snapshot_case)

    assert _visible_entries(snapshot_case["snapshots_dir"]) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"doc_id": "other-policy"}, "doc_id does not match manifest"),
        ({"url": "https://example.org/other/"}, "requested URL does not match manifest"),
        ({"fetch_date": "2026-07-30"}, "fetch date does not match chunks"),
        ({"sha256": "0" * 64}, "sha256 does not match"),
        ({"bytes": 999_999}, "bytes does not match"),
    ],
)
def test_receipt_mismatches_fail_before_any_archive_is_visible(
    snapshot_case,
    changes,
    message,
):
    doc_id = snapshot_case["chunks"][0].doc_id
    _rewrite_metadata(snapshot_case["raw_dir"], doc_id, **changes)

    with pytest.raises(SnapshotArchiveError, match=message):
        _archive(snapshot_case)

    assert _visible_entries(snapshot_case["snapshots_dir"]) == []


def test_missing_receipt_format_fails_closed(snapshot_case):
    doc_id = snapshot_case["chunks"][0].doc_id
    path = snapshot_case["raw_dir"] / f"{doc_id}.meta.yaml"
    metadata = yaml.safe_load(path.read_bytes())
    del metadata["format"]
    path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    with pytest.raises(SnapshotArchiveError, match="unsupported or missing format"):
        _archive(snapshot_case)

    assert _visible_entries(snapshot_case["snapshots_dir"]) == []


def test_manifest_and_receipt_format_disagreement_fails_closed(snapshot_case):
    snapshot_case["manifest"]["documents"][0]["format"] = "pdf"

    with pytest.raises(SnapshotArchiveError, match="formats disagree"):
        _archive(snapshot_case)

    assert _visible_entries(snapshot_case["snapshots_dir"]) == []


def test_both_raw_formats_for_one_document_are_rejected_as_ambiguous(snapshot_case):
    doc_id = snapshot_case["chunks"][0].doc_id
    (snapshot_case["raw_dir"] / f"{doc_id}.pdf").write_bytes(b"%PDF-1.7 stale snapshot")

    with pytest.raises(SnapshotArchiveError, match="format is ambiguous"):
        _archive(snapshot_case)

    assert _visible_entries(snapshot_case["snapshots_dir"]) == []


def test_rearchiving_identical_snapshot_preserves_first_archive(snapshot_case):
    first = _archive(snapshot_case)
    archive = snapshot_case["snapshots_dir"] / first.snapshot_version
    before = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    }

    second = _archive(snapshot_case, archived_at="2099-01-01T00:00:00+00:00")
    after = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    }

    assert second == first
    assert after == before
    version = json.loads((archive / "version.json").read_text(encoding="utf-8"))
    assert version["archived_at"] == _ARCHIVED_AT


def test_captured_sources_bind_archive_to_bytes_used_before_working_tree_changes(
    snapshot_case,
):
    captured = capture_source_material(
        snapshot_case["manifest"],
        snapshot_case["raw_dir"],
    )
    original = {source.observation.doc_id: source.raw_bytes for source in captured}
    changed_doc = snapshot_case["chunks"][0]
    replacement = b"<html><main>concurrent replacement</main></html>"
    _write_source(
        snapshot_case["raw_dir"],
        changed_doc,
        replacement,
    )

    identity = archive_snapshot(
        snapshot_case["chunks"],
        snapshot_case["manifest"],
        raw_dir=snapshot_case["raw_dir"],
        snapshots_dir=snapshot_case["snapshots_dir"],
        archived_at=_ARCHIVED_AT,
        sources=captured,
    )
    archive = snapshot_case["snapshots_dir"] / identity.snapshot_version

    assert (archive / "raw" / f"{changed_doc.doc_id}.html").read_bytes() == original[
        changed_doc.doc_id
    ]
    assert (snapshot_case["raw_dir"] / f"{changed_doc.doc_id}.html").read_bytes() == replacement
    assert validate_snapshot_archive(archive) == identity


def test_concurrent_identical_writers_converge_on_one_verified_archive(
    snapshot_case,
    monkeypatch,
):
    original = snapshots._write_staged_archive
    staged = threading.Barrier(2)

    def synchronize_after_staging(*args, **kwargs):
        original(*args, **kwargs)
        staged.wait(timeout=5)

    monkeypatch.setattr(snapshots, "_write_staged_archive", synchronize_after_staging)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_archive, snapshot_case) for _ in range(2)]
        identities = [future.result(timeout=10) for future in futures]

    assert identities[0] == identities[1]
    assert _visible_entries(snapshot_case["snapshots_dir"]) == [identities[0].snapshot_version]
    assert (
        validate_snapshot_archive(snapshot_case["snapshots_dir"] / identities[0].snapshot_version)
        == identities[0]
    )


def test_existing_corrupt_destination_is_rejected_and_never_overwritten(snapshot_case):
    material = collect_snapshot_material(
        snapshot_case["chunks"],
        snapshot_case["manifest"],
        snapshot_case["raw_dir"],
    )
    final = snapshot_case["snapshots_dir"] / material.identity.snapshot_version
    final.mkdir(parents=True)
    sentinel = b'{"archive_schema": 2, "sentinel": "do not overwrite"}\n'
    (final / "version.json").write_bytes(sentinel)

    with pytest.raises(SnapshotArchiveError):
        _archive(snapshot_case)

    assert (final / "version.json").read_bytes() == sentinel
    assert list(final.iterdir()) == [final / "version.json"]


@pytest.mark.parametrize("artifact", ["raw", "metadata", "chunks", "evidence", "version"])
def test_any_published_artifact_tampering_is_detected(snapshot_case, artifact):
    identity = _archive(snapshot_case)
    archive = snapshot_case["snapshots_dir"] / identity.snapshot_version
    doc_id = snapshot_case["chunks"][0].doc_id

    if artifact == "raw":
        path = archive / "raw" / f"{doc_id}.html"
        path.write_bytes(path.read_bytes() + b"tampered")
    elif artifact == "metadata":
        path = archive / "raw" / f"{doc_id}.meta.yaml"
        metadata = yaml.safe_load(path.read_bytes())
        metadata["sha256"] = "0" * 64
        path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    elif artifact == "chunks":
        path = archive / "chunks.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["text"] += " Tampered."
        path.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )
    elif artifact == "evidence":
        path = archive / "source-evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        evidence["observations"][0]["final_url"] = "https://example.org/tampered/"
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    else:
        path = archive / "version.json"
        version = json.loads(path.read_text(encoding="utf-8"))
        version["artifacts"]["chunks.jsonl"]["bytes"] += 1
        path.write_text(json.dumps(version, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SnapshotArchiveError):
        validate_snapshot_archive(archive)
    with pytest.raises(SnapshotArchiveError):
        load_snapshot_chunks(identity.snapshot_version, snapshot_case["snapshots_dir"])


def test_truncated_chunks_raise_the_public_archive_error(snapshot_case):
    identity = _archive(snapshot_case)
    archive = snapshot_case["snapshots_dir"] / identity.snapshot_version
    (archive / "chunks.jsonl").write_text('{"truncated":', encoding="utf-8")

    with pytest.raises(SnapshotArchiveError, match="chunks are malformed"):
        validate_snapshot_archive(archive)


def test_added_symbolic_link_directory_is_detected_as_tampering(
    snapshot_case,
    tmp_path,
):
    identity = _archive(snapshot_case)
    archive = snapshot_case["snapshots_dir"] / identity.snapshot_version
    outside = tmp_path / "outside"
    outside.mkdir()
    (archive / "linked-artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SnapshotArchiveError, match="symbolic link"):
        validate_snapshot_archive(archive)


def test_listing_ignores_hidden_staging_directories(snapshot_case):
    root = snapshot_case["snapshots_dir"]
    hidden = root / ".abandoned-stage"
    hidden.mkdir(parents=True)
    (hidden / "garbage").write_text("incomplete", encoding="utf-8")
    (root / "not-a-snapshot").mkdir()

    identity = _archive(snapshot_case)

    assert list_snapshots(root) == [identity.snapshot_version]
    assert hidden.exists()


@pytest.mark.parametrize("fail_on_write", [1, 3, 8])
def test_staged_write_failure_cleans_up_and_publishes_nothing(
    snapshot_case,
    monkeypatch,
    fail_on_write,
):
    original = snapshots._write_file
    calls = 0

    def fail_at_selected_write(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_on_write:
            raise OSError("injected staged write failure")
        original(path, content)

    monkeypatch.setattr(snapshots, "_write_file", fail_at_selected_write)

    with pytest.raises(OSError, match="injected staged write failure"):
        _archive(snapshot_case)

    root = snapshot_case["snapshots_dir"]
    assert all(path.name == ".archive.lock" for path in root.iterdir())
    assert _visible_entries(root) == []
