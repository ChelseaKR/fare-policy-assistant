"""Ingest pipeline tests: fetch (mocked HTTP), process, and the CLI dispatch.

The fetch path is exercised against an in-memory transport so no real network
request is made; the process path runs end to end over a tiny temp corpus and is
read back with `load_chunks`. These cover the manifest-driven plumbing that the
section/table tests (test_ingest.py, test_pdf_ingest.py) do not.
"""

from __future__ import annotations

import httpx
import yaml

from assistant import config
from assistant.ingest import load_chunks, main, process_all

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
    monkeypatch.setattr(config, "FACTS_PATH", processed / "facts.jsonl")
    return raw, processed


_HTML_DOC = """<html><head><title>Fares</title></head><body><main>
<h1>Fares</h1>
<h2>Discount Eligibility</h2>
<p>Discount fare for riders 65 years and older, and individuals with
disabilities. Proof of age such as a Medicare card is required when boarding the
bus on any fixed-route service the district runs across the county.</p>
</main></body></html>"""


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
    raw.mkdir(parents=True)
    (raw / "mst-fares.html").write_text(_HTML_DOC, encoding="utf-8")
    (raw / "mst-fares.meta.yaml").write_text(
        yaml.safe_dump({"fetch_date": "2026-06-12"}), encoding="utf-8"
    )

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
    raw.mkdir(parents=True)
    (raw / "mst-fares.html").write_text(_HTML_DOC, encoding="utf-8")
    (raw / "mst-fares.meta.yaml").write_text(
        yaml.safe_dump({"fetch_date": "2026-06-12"}), encoding="utf-8"
    )

    process_all()

    live_chunks = load_chunks()
    version = corpus.corpus_version(live_chunks)
    archived = corpus.load_chunks(version)
    assert [c.chunk_id for c in archived] == [c.chunk_id for c in live_chunks]
    assert version in corpus.list_versions()
    snapshot = config.VERSIONS_DIR / version / "manifest.snapshot.yaml"
    assert "mst-fares" in snapshot.read_text(encoding="utf-8")


def test_process_all_skips_documents_without_a_snapshot(tmp_path, monkeypatch, capsys):
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
    process_all()  # no raw snapshot → skipped, not crashed
    assert "skip" in capsys.readouterr().err
    assert load_chunks() == []


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
    raw.mkdir(parents=True)
    (raw / "mst-fares.html").write_text(_HTML_DOC, encoding="utf-8")
    (raw / "mst-fares.meta.yaml").write_text(
        yaml.safe_dump({"fetch_date": "2026-06-12"}), encoding="utf-8"
    )
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
