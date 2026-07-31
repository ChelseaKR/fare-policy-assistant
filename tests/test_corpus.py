"""Corpus identity, change tracking (R2-6), and longitudinal retention (EXP-05)."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from assistant import config
from assistant import corpus as corpus_module
from assistant.corpus import (
    CorpusArchiveError,
    archive_version,
    changelog,
    corpus_summary,
    corpus_version,
    diff_corpus,
)
from assistant.corpus import load_chunks as corpus_load_chunks
from assistant.identity import content_version


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
    assert s["content_version"] == content_version(chunks)
    assert len(s["content_version"]) == 64
    assert "MST" in s["agencies"] and "Yolobus" in s["agencies"]
    assert s["documents"] >= 1
    assert s["chunks"] == len(chunks)


def test_date_only_change_moves_legacy_version_but_not_content_identity(chunks):
    changed = [dataclasses.replace(chunk, fetch_date="2027-01-01") for chunk in chunks]

    assert corpus_version(changed) != corpus_version(chunks)
    assert content_version(changed) == content_version(chunks)


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


def test_archive_version_accepts_complete_pre_content_identity_metadata(
    chunks,
    versions_dir,
):
    version = archive_version(chunks, _manifest_for(chunks))
    metadata_path = versions_dir / version / "version.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    del metadata["content_version"]
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    legacy_bytes = metadata_path.read_bytes()

    assert archive_version(chunks, _manifest_for(chunks)) == version
    assert metadata_path.read_bytes() == legacy_bytes


def test_archive_version_rejects_partial_destination_without_repairing_it(
    chunks,
    versions_dir,
):
    version = corpus_version(chunks)
    destination = versions_dir / version
    destination.mkdir(parents=True)
    chunks_bytes = "".join(
        json.dumps(dataclasses.asdict(chunk), ensure_ascii=False) + "\n" for chunk in chunks
    ).encode()
    (destination / "chunks.jsonl").write_bytes(chunks_bytes)

    with pytest.raises(CorpusArchiveError, match="invalid artifact set"):
        archive_version(chunks, _manifest_for(chunks))

    assert list(destination.iterdir()) == [destination / "chunks.jsonl"]
    assert (destination / "chunks.jsonl").read_bytes() == chunks_bytes


def test_archive_version_rejects_corrupt_destination_without_overwriting_it(
    chunks,
    versions_dir,
):
    version = archive_version(chunks, _manifest_for(chunks))
    destination = versions_dir / version
    metadata_path = destination / "version.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["chunks"] += 1
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}

    with pytest.raises(CorpusArchiveError, match="field chunks does not match"):
        archive_version(chunks, _manifest_for(chunks))

    assert {
        path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()
    } == before


def test_archive_version_rejects_symbolic_link_artifact(
    chunks,
    versions_dir,
):
    version = archive_version(chunks, _manifest_for(chunks))
    chunks_path = versions_dir / version / "chunks.jsonl"
    outside = versions_dir.parent / "outside-chunks.jsonl"
    outside.write_bytes(chunks_path.read_bytes())
    chunks_path.unlink()
    chunks_path.symlink_to(outside)

    with pytest.raises(CorpusArchiveError, match="not a regular file"):
        archive_version(chunks, _manifest_for(chunks))

    assert chunks_path.is_symlink()
    assert outside.exists()


def test_archive_version_rejects_same_legacy_id_with_conflicting_chunk_metadata(
    chunks,
    versions_dir,
):
    version = archive_version(chunks, _manifest_for(chunks))
    destination = versions_dir / version
    before = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
    changed_document = chunks[0].doc_id
    conflicting = [
        dataclasses.replace(
            chunk,
            agency_full="A conflicting agency name omitted from the legacy digest",
        )
        if chunk.doc_id == changed_document
        else chunk
        for chunk in chunks
    ]
    assert corpus_version(conflicting) == version

    with pytest.raises(CorpusArchiveError, match="chunks conflict"):
        archive_version(conflicting, _manifest_for(conflicting))

    assert {
        path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()
    } == before


@pytest.mark.parametrize("fail_on_write", [1, 2, 3])
def test_archive_version_staged_write_failure_publishes_nothing(
    chunks,
    versions_dir,
    monkeypatch,
    fail_on_write,
):
    original = corpus_module._write_legacy_file
    writes = 0

    def fail_selected_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == fail_on_write:
            raise OSError("injected compatibility archive write failure")
        original(path, content)

    monkeypatch.setattr(corpus_module, "_write_legacy_file", fail_selected_write)

    with pytest.raises(OSError, match="injected compatibility archive write failure"):
        archive_version(chunks, _manifest_for(chunks))

    assert all(path.name == ".archive.lock" for path in versions_dir.iterdir())


def test_archive_version_concurrent_identical_writers_publish_one_archive(
    chunks,
    versions_dir,
    monkeypatch,
):
    original = corpus_module._write_staged_legacy_archive
    staged = threading.Barrier(2)

    def synchronize_after_staging(*args, **kwargs):
        original(*args, **kwargs)
        staged.wait(timeout=5)

    monkeypatch.setattr(
        corpus_module,
        "_write_staged_legacy_archive",
        synchronize_after_staging,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(archive_version, chunks, _manifest_for(chunks)) for _ in range(2)
        ]
        versions = [future.result(timeout=10) for future in futures]

    assert versions[0] == versions[1]
    visible = [path for path in versions_dir.iterdir() if not path.name.startswith(".")]
    assert visible == [versions_dir / versions[0]]
    before = (visible[0] / "version.json").read_bytes()
    assert archive_version(chunks, _manifest_for(chunks)) == versions[0]
    assert (visible[0] / "version.json").read_bytes() == before


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


def test_list_versions_ignores_hidden_stages_and_directory_symlinks(
    chunks,
    versions_dir,
):
    from assistant.corpus import list_versions

    version = archive_version(chunks, _manifest_for(chunks))
    hidden_stage = versions_dir / ".in-progress-stage"
    hidden_stage.mkdir()
    (hidden_stage / "version.json").write_text(
        '{"archived_at":"1900-01-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    external = versions_dir.parent / "external-version"
    external.mkdir()
    (versions_dir / "linked-version").symlink_to(external, target_is_directory=True)

    assert list_versions() == [version]


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


def test_main_snapshots_dispatch(tmp_path, monkeypatch, capsys):
    from assistant.corpus import main

    monkeypatch.setattr(config, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr("sys.argv", ["corpus", "snapshots"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == []


class TestVersionHistory:
    """EXP-09: the git-backed changelog the operator console reads."""

    @pytest.fixture
    def git_repo(self, tmp_path, monkeypatch, chunks):
        repo = tmp_path / "repo"
        processed = repo / "corpus" / "processed"
        processed.mkdir(parents=True)
        chunks_path = processed / "chunks.jsonl"

        def commit(text: str) -> None:
            with chunks_path.open("w", encoding="utf-8") as f:
                for c in chunks:
                    if text is not None:
                        c = dataclasses.replace(c, text=c.text + text)
                    f.write(json.dumps(dataclasses.asdict(c)) + "\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "corpus"],
                cwd=repo,
                check=True,
            )

        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        commit(None)
        commit(" amended for v2")

        monkeypatch.setattr(config, "REPO_ROOT", repo)
        monkeypatch.setattr(config, "CHUNKS_PATH", chunks_path)
        return repo

    def test_history_returns_newest_first_with_full_chunks(self, git_repo):
        from assistant.corpus import version_history

        versions = version_history()
        assert len(versions) == 2
        assert versions[0]["chunks"]  # newest commit first
        assert len(versions[0]["corpus_version"]) == 12
        assert versions[0]["corpus_version"] != versions[1]["corpus_version"]

    def test_history_versions_are_diffable(self, git_repo):
        from assistant.corpus import version_history
        from assistant.ingest import Chunk

        versions = version_history()
        old_chunks = [Chunk(**c) for c in versions[1]["chunks"]]
        new_chunks = [Chunk(**c) for c in versions[0]["chunks"]]
        d = diff_corpus(old_chunks, new_chunks)
        assert d["changed"]

    def test_main_history_command_prints_json(self, git_repo, capsys):
        from assistant.corpus import main

        assert main(["history"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert "generated_at" in out
        assert len(out["versions"]) == 2

    def test_unknown_command_errors(self):
        from assistant.corpus import main

        assert main(["bogus"]) == 2
