"""Corpus identity and change tracking (R2-6)."""

from __future__ import annotations

import dataclasses
import json
import subprocess

import pytest

from assistant import config
from assistant.corpus import corpus_summary, corpus_version, diff_corpus


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


def test_main_prints_summary_json(capsys):
    from assistant.corpus import main

    assert main() == 0
    import json

    out = json.loads(capsys.readouterr().out)
    assert "corpus_version" in out and out["chunks"] >= 1


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

        with pytest.raises(SystemExit):
            main(["bogus"])
