"""Ingest pipeline tests: fetch (mocked HTTP), process, and the CLI dispatch.

The fetch path is exercised against an in-memory transport so no real network
request is made; the process path runs end to end over a tiny temp corpus and is
read back with `load_chunks`. These cover the manifest-driven plumbing that the
section/table tests (test_ingest.py, test_pdf_ingest.py) do not.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
import yaml

from assistant import config
from assistant.ingest import load_chunks, main, process_all
from assistant.snapshots import SnapshotArchiveError

# Capture the genuine client class before any test patches the (shared) httpx
# module, so the mock factory can build a real client with a MockTransport
# without recursing into itself.
_REAL_CLIENT = httpx.Client


def _mock_client(handler):
    return lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)


def _point_config_at(tmp_path, monkeypatch, manifest: dict):
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(config, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(config, "RAW_DIR", raw)
    monkeypatch.setattr(config, "PROCESSED_DIR", processed)
    monkeypatch.setattr(config, "CHUNKS_PATH", processed / "chunks.jsonl")
    # process_all() archives into VERSIONS_DIR (EXP-05); keep that under
    # tmp_path too so tests never write into the repo's real corpus/versions/.
    monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(config, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(config, "FACTS_PATH", processed / "facts.jsonl")
    return raw, processed


_HTML_DOC = """<html><head><title>Fares</title></head><body><main>
<h1>Fares</h1>
<h2>Discount Eligibility</h2>
<p>Discount fare for riders 65 years and older, and individuals with
disabilities. Proof of age such as a Medicare card is required when boarding the
bus on any fixed-route service the district runs across the county.</p>
</main></body></html>"""


def _write_html_snapshot(
    raw,
    *,
    doc_id: str = "mst-fares",
    url: str = "https://mst.org/fares/",
    fetch_date: str = "2026-06-12",
) -> None:
    content = _HTML_DOC.encode()
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"{doc_id}.html").write_bytes(content)
    receipt = {
        "doc_id": doc_id,
        "url": url,
        "final_url": url,
        "fetch_date": fetch_date,
        "http_status": 200,
        "format": "html",
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump(receipt, sort_keys=False),
        encoding="utf-8",
    )


# ── fetch (mocked transport) ─────────────────────────────────────────────────


def test_fetch_all_writes_snapshot_and_meta(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 7,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            },
            {
                "id": "mst-fares-2",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "More",
                "url": "https://mst.org/fares/more/",
                "language": "en",
            },
        ],
    }
    raw, _ = _point_config_at(tmp_path, monkeypatch, manifest)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_HTML_DOC.encode(), headers={"content-type": "text/html"}
        )

    monkeypatch.setattr("assistant.ingest.httpx.Client", _mock_client(handler))
    slept: list[float] = []
    monkeypatch.setattr("assistant.ingest.time.sleep", lambda s: slept.append(s))

    from assistant import ingest

    ingest.fetch_all()

    assert (raw / "mst-fares.html").exists()
    meta = yaml.safe_load((raw / "mst-fares.meta.yaml").read_text())
    assert meta["http_status"] == 200 and meta["format"] == "html"
    assert len(meta["sha256"]) == 64
    # Two docs on the same host → the crawl delay slept once between them.
    assert slept, "expected a crawl-delay sleep between same-host requests"


def test_fetch_all_pdf_is_sniffed_from_content_type(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "policy",
                "agency": "MST",
                "agency_full": "MST",
                "title": "Policy",
                "url": "https://mst.org/p.pdf",
                "language": "en",
            }
        ],
    }
    raw, _ = _point_config_at(tmp_path, monkeypatch, manifest)

    def handler(request):
        return httpx.Response(
            200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"}
        )

    monkeypatch.setattr("assistant.ingest.httpx.Client", _mock_client(handler))
    from assistant import ingest

    ingest.fetch_all()
    assert (raw / "policy.pdf").exists()


def test_fetch_all_records_failures_and_exits_nonzero(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "gone",
                "agency": "MST",
                "agency_full": "MST",
                "title": "Gone",
                "url": "https://mst.org/404",
                "language": "en",
            }
        ],
    }
    _point_config_at(tmp_path, monkeypatch, manifest)

    def handler(request):
        return httpx.Response(404, content=b"not found")

    monkeypatch.setattr("assistant.ingest.httpx.Client", _mock_client(handler))
    import pytest

    from assistant import ingest

    with pytest.raises(SystemExit):
        ingest.fetch_all()


def test_fetch_all_only_filter_skips_unselected_docs(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "keep",
                "agency": "MST",
                "agency_full": "MST",
                "title": "K",
                "url": "https://mst.org/keep",
                "language": "en",
            },
            {
                "id": "skip",
                "agency": "MST",
                "agency_full": "MST",
                "title": "S",
                "url": "https://mst.org/skip",
                "language": "en",
            },
        ],
    }
    raw, _ = _point_config_at(tmp_path, monkeypatch, manifest)
    monkeypatch.setattr(
        "assistant.ingest.httpx.Client",
        _mock_client(
            lambda r: httpx.Response(
                200, content=_HTML_DOC.encode(), headers={"content-type": "text/html"}
            )
        ),
    )
    from assistant import ingest

    ingest.fetch_all(only={"keep"})
    assert (raw / "keep.html").exists()
    assert not (raw / "skip.html").exists()


# ── process + load round trip ────────────────────────────────────────────────


def test_process_all_round_trips_through_load_chunks(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, processed = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)

    process_all()

    # A processed markdown view and the chunk index were both written.
    assert (processed / "mst-fares.md").exists()
    chunks = load_chunks()
    assert chunks, "process_all produced at least one chunk"
    c = chunks[0]
    assert c.agency == "MST" and c.doc_id == "mst-fares"
    assert c.fetch_date == "2026-06-12"
    assert "65 years and older" in " ".join(ch.text for ch in chunks)


def test_process_all_archives_the_corpus_version(tmp_path, monkeypatch):
    """EXP-05: process_all() retains this content under corpus/versions/<id>/,
    not just corpus/processed/, so a later re-ingest cannot erase it."""
    from assistant import corpus

    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, _ = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)

    process_all()

    live_chunks = load_chunks()
    version = corpus.corpus_version(live_chunks)
    archived = corpus.load_chunks(version)
    assert [c.chunk_id for c in archived] == [c.chunk_id for c in live_chunks]
    assert version in corpus.list_versions()
    snapshot = config.VERSIONS_DIR / version / "manifest.snapshot.yaml"
    assert "mst-fares" in snapshot.read_text(encoding="utf-8")
    from assistant.snapshots import list_snapshots, load_snapshot_chunks

    snapshots = list_snapshots()
    assert len(snapshots) == 1
    assert [c.chunk_id for c in load_snapshot_chunks(snapshots[0])] == [
        c.chunk_id for c in live_chunks
    ]


def test_process_all_fails_if_any_manifest_document_has_no_snapshot(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "missing",
                "agency": "MST",
                "agency_full": "MST",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    _, processed = _point_config_at(tmp_path, monkeypatch, manifest)
    processed.mkdir(parents=True)
    with pytest.raises(SnapshotArchiveError, match="metadata for missing"):
        process_all()
    assert not config.CHUNKS_PATH.exists()


def test_process_all_archives_the_exact_bytes_used_to_derive_chunks(
    tmp_path,
    monkeypatch,
):
    from assistant import ingest
    from assistant.snapshots import list_snapshots

    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, _ = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)
    original_bytes = (raw / "mst-fares.html").read_bytes()
    replacement_bytes = original_bytes.replace(b"65 years", b"99 years")
    original_sections_from_html = ingest.sections_from_html

    def replace_working_source_after_parse(html: str):
        sections = original_sections_from_html(html)
        (raw / "mst-fares.html").write_bytes(replacement_bytes)
        replacement_receipt = {
            "doc_id": "mst-fares",
            "url": "https://mst.org/fares/",
            "final_url": "https://mst.org/fares/",
            "fetch_date": "2026-06-12",
            "http_status": 200,
            "format": "html",
            "sha256": hashlib.sha256(replacement_bytes).hexdigest(),
            "bytes": len(replacement_bytes),
        }
        (raw / "mst-fares.meta.yaml").write_text(
            yaml.safe_dump(replacement_receipt, sort_keys=False),
            encoding="utf-8",
        )
        return sections

    monkeypatch.setattr(
        ingest,
        "sections_from_html",
        replace_working_source_after_parse,
    )
    process_all()

    live_text = " ".join(chunk.text for chunk in load_chunks())
    assert "65 years" in live_text
    assert "99 years" not in live_text
    [snapshot_id] = list_snapshots()
    archived_raw = (config.SNAPSHOTS_DIR / snapshot_id / "raw" / "mst-fares.html").read_bytes()
    assert archived_raw == original_bytes
    assert (raw / "mst-fares.html").read_bytes() == replacement_bytes


def test_archive_failure_leaves_prior_live_chunks_untouched(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, processed = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)
    processed.mkdir(parents=True)
    original = b'{"prior":"serving"}\n'
    config.CHUNKS_PATH.write_bytes(original)

    def fail_archive(*args, **kwargs):
        raise RuntimeError("injected archive failure")

    monkeypatch.setattr("assistant.snapshots.archive_snapshot", fail_archive)
    with pytest.raises(RuntimeError, match="injected archive failure"):
        process_all()
    assert config.CHUNKS_PATH.read_bytes() == original


def test_legacy_archive_failure_leaves_prior_live_chunks_untouched(
    tmp_path,
    monkeypatch,
):
    manifest = {
        "user_agent": "test-agent/0.1",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "Monterey-Salinas Transit",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, processed = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)
    processed.mkdir(parents=True)
    original = b'{"prior":"serving"}\n'
    config.CHUNKS_PATH.write_bytes(original)

    def fail_legacy_archive(*args, **kwargs):
        raise RuntimeError("injected legacy archive failure")

    monkeypatch.setattr("assistant.corpus.archive_version", fail_legacy_archive)
    with pytest.raises(RuntimeError, match="injected legacy archive failure"):
        process_all()
    assert config.CHUNKS_PATH.read_bytes() == original


# ── CLI dispatch ─────────────────────────────────────────────────────────────


def test_main_process_dispatch(tmp_path, monkeypatch):
    manifest = {
        "user_agent": "x",
        "crawl_delay_seconds": 0,
        "documents": [
            {
                "id": "mst-fares",
                "agency": "MST",
                "agency_full": "MST",
                "title": "Fares",
                "url": "https://mst.org/fares/",
                "language": "en",
            }
        ],
    }
    raw, processed = _point_config_at(tmp_path, monkeypatch, manifest)
    _write_html_snapshot(raw)
    monkeypatch.setattr("sys.argv", ["ingest", "process"])
    main()
    assert (processed / "chunks.jsonl").exists()


def test_main_unknown_command_exits():
    import pytest

    monkeypatch_argv = ["ingest", "frobnicate"]
    import sys

    old = sys.argv
    sys.argv = monkeypatch_argv
    try:
        with pytest.raises(SystemExit, match="unknown command"):
            main()
    finally:
        sys.argv = old
