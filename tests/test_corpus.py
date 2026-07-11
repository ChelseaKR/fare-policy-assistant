"""Corpus identity and change tracking (R2-6)."""

from __future__ import annotations

import dataclasses

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
