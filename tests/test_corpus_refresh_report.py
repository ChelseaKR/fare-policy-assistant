"""Freshness loop: version-keyed diff, changelog, and staleness lint (FIX-09)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from assistant.corpus import corpus_version
from tools import corpus_refresh_report as crr


@pytest.fixture
def old_snapshot(tmp_path, chunks):
    path = tmp_path / "old.json"
    crr.write_snapshot(path, chunks)
    return path


def test_identical_chunks_report_no_change(monkeypatch, tmp_path, chunks, old_snapshot):
    # load_chunks() (the "new" corpus) returns the same chunks → version unchanged.
    monkeypatch.setattr(crr, "load_chunks", lambda: chunks)
    changed = crr.run_report(old_snapshot, tmp_path / "pr.md")
    assert changed is False
    # No PR body is written when nothing changed.
    assert not (tmp_path / "pr.md").exists()


def test_changed_chunk_lists_doc_and_renders_changelog(monkeypatch, tmp_path, chunks, old_snapshot):
    # Edit one document's text so its version and doc-hash both move.
    new_chunks = [dataclasses.replace(c) for c in chunks]
    new_chunks[0] = dataclasses.replace(
        new_chunks[0], text=new_chunks[0].text + " Fares increase to $2.50 on 2027-01-01."
    )
    assert corpus_version(new_chunks) != corpus_version(chunks)
    monkeypatch.setattr(crr, "load_chunks", lambda: new_chunks)

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Corpus changelog\n\nintro\n\n## oldver (2026-06-17)\n\nInitial.\n")
    monkeypatch.setattr(crr.config, "CORPUS_DIR", tmp_path)

    out = tmp_path / "pr.md"
    changed = crr.run_report(old_snapshot, out, day="2026-07-02")
    assert changed is True

    # The changed doc is named in the PR body and the changelog was appended.
    body = out.read_text()
    assert "mst-fares" in body
    changelog_text = changelog.read_text()
    assert f"## {corpus_version(new_chunks)} (2026-07-02)" in changelog_text
    assert "Changed" in changelog_text
    # Newest-first: the new entry precedes the prior one.
    assert changelog_text.index("2026-07-02") < changelog_text.index("oldver")


def test_lint_flags_removed_required_fact_and_respects_scope():
    suites = [
        {
            "suite": "groundedness",
            "cases": [
                {
                    "id": "g-1",
                    "agency_scope": "MST",
                    "required_facts": ["$2.00", "re:\\$\\s?9\\.99"],
                },
            ],
        }
    ]
    # MST chunk has $2.00 but not $9.99; a Yolobus chunk carries $9.99 but is out
    # of scope, so the re: fact must still be flagged as missing.
    chunks = [
        _chunk("mst-fares#0", "MST", "Single ride regular fare is $2.00."),
        _chunk("yolobus-fares#0", "Yolobus", "Express fare $9.99."),
    ]
    viol = crr.lint_stale_cases(suites, chunks)
    assert len(viol) == 1
    assert viol[0]["id"] == "g-1"
    assert viol[0]["missing_required_facts"] == ["re:\\$\\s?9\\.99"]


def test_lint_passes_when_regex_fact_present_in_scope():
    suites = [
        {
            "suite": "s",
            "cases": [{"id": "ok", "agency_scope": "MST", "required_facts": ["re:\\$\\s?2\\.00"]}],
        }
    ]
    chunks = [_chunk("mst-fares#0", "MST", "Cash single ride is $ 2.00 flat.")]
    assert crr.lint_stale_cases(suites, chunks) == []


def test_lint_flags_forbidden_content_now_present():
    suites = [
        {
            "suite": "s",
            "cases": [{"id": "f-1", "agency_scope": "SBMTD", "forbidden_content": ["free ride"]}],
        }
    ]
    chunks = [_chunk("sbmtd#0", "SBMTD", "Everyone gets a free ride on Sundays.")]
    viol = crr.lint_stale_cases(suites, chunks)
    assert len(viol) == 1
    assert viol[0]["present_forbidden_content"] == ["free ride"]


def test_snapshot_roundtrip_preserves_version_and_chunks(tmp_path, chunks):
    path = tmp_path / "snap.json"
    version = crr.write_snapshot(path, chunks)
    assert version == corpus_version(chunks)
    loaded_version, loaded = crr.load_snapshot(path)
    assert loaded_version == version
    assert [dataclasses.asdict(c) for c in loaded] == [dataclasses.asdict(c) for c in chunks]
    # The snapshot is valid JSON with the documented shape.
    payload = json.loads(path.read_text())
    assert payload["corpus_version"] == version


def _chunk(chunk_id, agency, text):
    from assistant.ingest import Chunk

    doc_id = chunk_id.split("#")[0]
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        agency=agency,
        agency_full=agency,
        doc_title="Fares",
        url="https://example.org/",
        fetch_date="2026-06-12",
        language="en",
        section="Fares",
        text=text,
    )
