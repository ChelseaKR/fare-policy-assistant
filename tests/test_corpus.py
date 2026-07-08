"""Corpus identity, change tracking (R2-6), and longitudinal retention (EXP-05)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from assistant import config
from assistant.corpus import (
    archive_version,
    changelog,
    corpus_summary,
    corpus_version,
    diff_corpus,
)
from assistant.corpus import load_chunks as corpus_load_chunks


def test_version_is_deterministic_and_order_independent(chunks):
    v1 = corpus_version(chunks)
    v2 = corpus_version(list(reversed(chunks)))
    assert v1 == v2
    assert len(v1) == 12


def test_version_changes_when_text_changes(chunks):
    before = corpus_version(chunks)
    chunks[0].text = chunks[0].text + " New sentence added to the policy."
    assert corpus_version(chunks) != before


def test_version_changes_when_fetch_date_changes(chunks):
    before = corpus_version(chunks)
    chunks[0].fetch_date = "2027-01-01"
    assert corpus_version(chunks) != before


def test_summary_reports_agencies_and_counts(chunks):
    s = corpus_summary(chunks)
    assert s["corpus_version"] == corpus_version(chunks)
    assert "MST" in s["agencies"] and "Yolobus" in s["agencies"]
    assert s["documents"] >= 1
    assert s["chunks"] == len(chunks)


def test_main_prints_summary_json(capsys, monkeypatch):
    from assistant.corpus import main

    monkeypatch.setattr("sys.argv", ["corpus", "summary"])
    assert main() == 0
    out = json.loads(capsys.readouterr().out)
    assert "corpus_version" in out and out["chunks"] >= 1


def test_main_defaults_to_summary_with_no_args(capsys, monkeypatch):
    from assistant.corpus import main

    monkeypatch.setattr("sys.argv", ["corpus"])
    assert main() == 0
    out = json.loads(capsys.readouterr().out)
    assert "corpus_version" in out


def test_main_unknown_command_returns_error(monkeypatch):
    from assistant.corpus import main

    monkeypatch.setattr("sys.argv", ["corpus", "frobnicate"])
    assert main() == 2


def test_diff_detects_added_removed_changed(chunks):
    old = list(chunks)
    # Remove one document entirely.
    removed_doc = old[-1].doc_id
    new = [c for c in old if c.doc_id != removed_doc]
    # Change the text of the first document (a distinct copy, so old is untouched).
    changed_doc = new[0].doc_id
    new[0] = dataclasses.replace(new[0], text=new[0].text + " amended")
    d = diff_corpus(old, new)
    assert removed_doc in d["removed"]
    assert changed_doc in d["changed"]
    assert d["added"] == []


# ── longitudinal retention (EXP-05) ─────────────────────────────────────────


@pytest.fixture
def versions_dir(tmp_path, monkeypatch):
    d = tmp_path / "versions"
    monkeypatch.setattr(config, "VERSIONS_DIR", d)
    return d


def _manifest_for(chunks):
    seen = {}
    for c in chunks:
        seen.setdefault(
            c.doc_id,
            {
                "id": c.doc_id,
                "agency": c.agency,
                "agency_full": c.agency_full,
                "title": c.doc_title,
                "url": c.url,
                "language": c.language,
            },
        )
    return {"documents": list(seen.values())}


def test_archive_version_writes_chunks_manifest_and_meta(chunks, versions_dir):
    version = archive_version(chunks, _manifest_for(chunks))
    assert version == corpus_version(chunks)
    version_dir = versions_dir / version
    assert (version_dir / "chunks.jsonl").exists()
    assert (version_dir / "manifest.snapshot.yaml").exists()
    meta = json.loads((version_dir / "version.json").read_text(encoding="utf-8"))
    assert meta["corpus_version"] == version
    assert "archived_at" in meta


def test_archive_version_is_idempotent(chunks, versions_dir):
    v1 = archive_version(chunks, _manifest_for(chunks))
    meta_path = versions_dir / v1 / "version.json"
    first_write = meta_path.read_text(encoding="utf-8")
    v2 = archive_version(chunks, _manifest_for(chunks))
    assert v1 == v2
    # Re-archiving identical content does not touch the original archived_at.
    assert meta_path.read_text(encoding="utf-8") == first_write


def test_archive_version_does_not_overwrite_prior_content(chunks, versions_dir):
    """Distinct content hashes get distinct, permanent directories: this is the
    guarantee EXP-05 exists for (stop overwriting corpus history in place)."""
    v_old = archive_version(chunks, _manifest_for(chunks))
    changed = list(chunks)
    changed[0] = dataclasses.replace(changed[0], text=changed[0].text + " A new sentence.")
    v_new = archive_version(changed, _manifest_for(changed))
    assert v_old != v_new
    # The old version's chunks are untouched by the new archive call.
    assert corpus_load_chunks(v_old)[0].text == chunks[0].text


def test_load_chunks_version_round_trips(chunks, versions_dir):
    version = archive_version(chunks, _manifest_for(chunks))
    loaded = corpus_load_chunks(version)
    assert sorted(c.chunk_id for c in loaded) == sorted(c.chunk_id for c in chunks)


def test_load_chunks_unknown_version_raises(versions_dir):
    with pytest.raises(FileNotFoundError, match="not archived"):
        corpus_load_chunks("no-such-version")


def test_list_versions_empty_when_none_archived(versions_dir):
    from assistant.corpus import list_versions

    assert list_versions() == []


def test_list_versions_reflects_archives(chunks, versions_dir):
    from assistant.corpus import list_versions

    v1 = archive_version(chunks, _manifest_for(chunks))
    assert list_versions() == [v1]
    changed = list(chunks)
    changed[0] = dataclasses.replace(changed[0], text=changed[0].text + " Another change.")
    v2 = archive_version(changed, _manifest_for(changed))
    assert set(list_versions()) == {v1, v2}


def test_changelog_chains_diffs_across_versions(chunks, versions_dir):
    v1 = archive_version(chunks, _manifest_for(chunks))
    removed_doc = chunks[-1].doc_id
    trimmed = [c for c in chunks if c.doc_id != removed_doc]
    v2 = archive_version(trimmed, _manifest_for(trimmed))

    entries = changelog([v1, v2])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["from_version"] == v1
    assert entry["to_version"] == v2
    assert removed_doc in entry["removed"]


def test_changelog_empty_for_single_version(chunks, versions_dir):
    v1 = archive_version(chunks, _manifest_for(chunks))
    assert changelog([v1]) == []


def test_main_versions_and_changelog_dispatch(chunks, versions_dir, monkeypatch, capsys):
    from assistant.corpus import main

    archive_version(chunks, _manifest_for(chunks))
    monkeypatch.setattr("sys.argv", ["corpus", "versions"])
    assert main() == 0
    versions_out = json.loads(capsys.readouterr().out)
    assert len(versions_out) == 1

    monkeypatch.setattr("sys.argv", ["corpus", "changelog"])
    assert main() == 0
    changelog_out = json.loads(capsys.readouterr().out)
    assert changelog_out == []
