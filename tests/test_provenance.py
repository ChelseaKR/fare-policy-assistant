"""evals/provenance.py: the published-artifact-vs-HEAD drift check.

Covers the pure read/render/compare functions and the acknowledgement escape
hatch. `check_all()`'s file-reading defaults are exercised implicitly by the
tests below (all inputs are injected), so no test depends on the actual
committed EVALS.md / baseline.json / golden.jsonl contents.
"""

from __future__ import annotations

import json

import pytest

from evals import provenance


def test_render_and_read_evals_md_round_trip():
    payload = {"run_id": "r1", "corpus_version": "abc123", "prompt_versions": {"system": "v1"}}
    text = "# Report\n\nsome content\n\n" + provenance.render_evals_md_block(payload)
    assert provenance.read_evals_md(text) == payload


def test_read_evals_md_missing_block_is_none():
    assert provenance.read_evals_md("# Report\n\nno provenance here\n") is None


def test_read_baseline_reads_the_provenance_key():
    baseline = {"suites": {}, "provenance": {"corpus_version": "abc"}}
    assert provenance.read_baseline(baseline) == {"corpus_version": "abc"}
    assert provenance.read_baseline({"suites": {}}) is None


def test_read_golden_reads_the_comment_line():
    payload = {"corpus_version": "abc", "prompt_versions": {"system": "v1"}}
    text = f"# provenance: {json.dumps(payload)}\n" + '{"case_id": "x"}\n'
    assert provenance.read_golden(text) == payload


def test_read_golden_missing_line_is_none():
    assert provenance.read_golden('{"case_id": "x"}\n') is None


def test_compare_matches_when_everything_agrees():
    head_prompts = {"system": "v6", "answer_user": "v3"}
    declared = {
        "corpus_version": "cv1",
        "pipeline_version": "pv1",
        "prompt_versions": head_prompts,
    }
    assert provenance._compare("EVALS.md", declared, head_prompts, "cv1", "pv1") == []


def test_compare_flags_corpus_and_prompt_drift():
    head_prompts = {"system": "v7"}
    declared = {
        "corpus_version": "old-cv",
        "pipeline_version": "pv1",
        "prompt_versions": {"system": "v6"},
    }
    mismatches = provenance._compare("EVALS.md", declared, head_prompts, "new-cv", "pv1")
    fields = {m.field for m in mismatches}
    assert fields == {"corpus_version", "prompt_versions.system"}


def test_compare_flags_pipeline_drift_when_corpus_and_prompts_still_match():
    """The #192 shape: retrieval moved, nothing else did.

    On 2026-09-04 `src/assistant/retrieve.py` gained 330 lines thirty-six
    minutes after a `golden.jsonl` recording was taken. Corpus and both answer
    prompts still matched HEAD, so every field the gate compared agreed and it
    reported green on a recording that no longer described the running system.
    """
    head_prompts = {"system": "v22", "answer_user": "v7"}
    declared = {
        "corpus_version": "10deac978967",
        "pipeline_version": "old-pipeline",
        "prompt_versions": head_prompts,
    }
    mismatches = provenance._compare(
        "golden.jsonl", declared, head_prompts, "10deac978967", "new-pipeline"
    )
    assert [m.field for m in mismatches] == ["pipeline_version"]
    assert mismatches[0].declared == "old-pipeline"
    assert mismatches[0].expected == "new-pipeline"


def test_compare_treats_an_absent_pipeline_version_as_a_mismatch():
    """ "I cannot tell" must not score the same as "it matches"."""
    head_prompts = {"system": "v1"}
    declared = {"corpus_version": "cv1", "prompt_versions": head_prompts}
    mismatches = provenance._compare("EVALS.md", declared, head_prompts, "cv1", "pv1")
    assert [m.field for m in mismatches] == ["pipeline_version"]
    assert mismatches[0].declared is None


def test_compare_none_declared_is_a_single_mismatch():
    mismatches = provenance._compare("baseline.json", None, {"system": "v1"}, "cv", "pv")
    assert len(mismatches) == 1
    assert mismatches[0].field == "provenance"


def test_load_acknowledgements_requires_a_reason(tmp_path):
    ack_path = tmp_path / "stale_acknowledged.json"
    ack_path.write_text(json.dumps({"acknowledged": [{"artifact": "EVALS.md", "field": "x"}]}))
    with pytest.raises(SystemExit):
        provenance.load_acknowledgements(ack_path)


def test_load_acknowledgements_accepts_a_documented_reason(tmp_path):
    ack_path = tmp_path / "stale_acknowledged.json"
    ack_path.write_text(
        json.dumps(
            {
                "acknowledged": [
                    {"artifact": "EVALS.md", "field": "corpus_version", "reason": "documented gap"}
                ]
            }
        )
    )
    acked = provenance.load_acknowledgements(ack_path)
    assert acked == {("EVALS.md", "corpus_version")}


def test_load_acknowledgements_missing_file_is_empty(tmp_path):
    assert provenance.load_acknowledgements(tmp_path / "does-not-exist.json") == set()


def _fixed_prompts(version: str):
    return lambda names=provenance.ALL_PROMPTS: dict.fromkeys(names, version)


def test_check_all_clean_when_all_three_artifacts_match_head(monkeypatch):
    monkeypatch.setattr(provenance, "head_prompt_versions", _fixed_prompts("v1"))
    monkeypatch.setattr(provenance, "head_corpus_version", lambda: "cv1")
    monkeypatch.setattr(provenance, "head_pipeline_version", lambda: "pv1")
    all_prompts = dict.fromkeys(provenance.ALL_PROMPTS, "v1")
    answer_prompts = {k: "v1" for k in provenance.ANSWER_PROMPTS}
    evals_md = "x\n" + provenance.render_evals_md_block(
        {
            "run_id": "r",
            "corpus_version": "cv1",
            "pipeline_version": "pv1",
            "prompt_versions": all_prompts,
        }
    )
    baseline = {
        "provenance": {
            "corpus_version": "cv1",
            "pipeline_version": "pv1",
            "prompt_versions": all_prompts,
        }
    }
    golden = "# provenance: " + json.dumps(
        {"corpus_version": "cv1", "pipeline_version": "pv1", "prompt_versions": answer_prompts}
    )
    result = provenance.check_all(
        acknowledged=set(), evals_md=evals_md, baseline=baseline, golden=golden
    )
    assert result == {"failures": [], "acknowledged": []}


def test_check_all_reports_unacknowledged_drift_as_a_failure(monkeypatch):
    monkeypatch.setattr(provenance, "head_prompt_versions", _fixed_prompts("v2"))
    monkeypatch.setattr(provenance, "head_corpus_version", lambda: "cv2")
    monkeypatch.setattr(provenance, "head_pipeline_version", lambda: "pv2")
    stale_prompts = dict.fromkeys(provenance.ALL_PROMPTS, "v1")
    evals_md = "x\n" + provenance.render_evals_md_block(
        {"run_id": "r", "corpus_version": "cv1", "prompt_versions": stale_prompts}
    )
    baseline = {"provenance": {"corpus_version": "cv1", "prompt_versions": stale_prompts}}
    golden = "# provenance: " + json.dumps(
        {"corpus_version": "cv1", "prompt_versions": {k: "v1" for k in provenance.ANSWER_PROMPTS}}
    )
    result = provenance.check_all(
        acknowledged=set(), evals_md=evals_md, baseline=baseline, golden=golden
    )
    assert result["failures"], "drift on all three artifacts should be reported"
    assert result["acknowledged"] == []


def test_check_all_downgrades_acknowledged_mismatches_to_warnings(monkeypatch):
    monkeypatch.setattr(provenance, "head_prompt_versions", _fixed_prompts("v1"))
    monkeypatch.setattr(provenance, "head_corpus_version", lambda: "cv-new")
    monkeypatch.setattr(provenance, "head_pipeline_version", lambda: "pv1")
    all_prompts = dict.fromkeys(provenance.ALL_PROMPTS, "v1")
    answer_prompts = {k: "v1" for k in provenance.ANSWER_PROMPTS}
    evals_md = "x\n" + provenance.render_evals_md_block(
        {
            "run_id": "r",
            "corpus_version": "cv-old",
            "pipeline_version": "pv1",
            "prompt_versions": all_prompts,
        }
    )
    baseline = {
        "provenance": {
            "corpus_version": "cv-old",
            "pipeline_version": "pv1",
            "prompt_versions": all_prompts,
        }
    }
    golden = "# provenance: " + json.dumps(
        {"corpus_version": "cv-old", "pipeline_version": "pv1", "prompt_versions": answer_prompts}
    )
    acked = {
        ("EVALS.md", "corpus_version"),
        ("baseline.json", "corpus_version"),
        ("golden.jsonl", "corpus_version"),
    }
    result = provenance.check_all(
        acknowledged=acked, evals_md=evals_md, baseline=baseline, golden=golden
    )
    assert result["failures"] == []
    assert len(result["acknowledged"]) == 3


# ── head_pipeline_version ────────────────────────────────────────────────────


def _pipeline_fixture(tmp_path, monkeypatch, files: dict[str, bytes]) -> None:
    """Point PIPELINE_SOURCES at files under `tmp_path` instead of the repo."""
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    monkeypatch.setattr(provenance.config, "REPO_ROOT", tmp_path)


def test_pipeline_version_is_stable_across_calls(tmp_path, monkeypatch):
    _pipeline_fixture(tmp_path, monkeypatch, {"a.py": b"one\n", "b.py": b"two\n"})
    sources = ("a.py", "b.py")
    first = provenance.head_pipeline_version(sources)
    assert first == provenance.head_pipeline_version(sources)
    assert len(first) == 12


def test_pipeline_version_changes_when_a_pipeline_source_changes(tmp_path, monkeypatch):
    _pipeline_fixture(tmp_path, monkeypatch, {"a.py": b"one\n", "b.py": b"two\n"})
    sources = ("a.py", "b.py")
    before = provenance.head_pipeline_version(sources)
    (tmp_path / "b.py").write_bytes(b"two\nthree\n")
    assert provenance.head_pipeline_version(sources) != before


def test_pipeline_version_changes_when_a_line_moves_between_the_two_sources(tmp_path, monkeypatch):
    """The digest binds each file's bytes to that file's own path.

    Without the path in the hash, cutting a line out of `retrieve.py` and
    pasting it into `answer.py` would leave the concatenated bytes — and so the
    digest — unchanged, which is a real refactor that really does move answers.
    """
    _pipeline_fixture(tmp_path, monkeypatch, {"a.py": b"shared\n", "b.py": b""})
    sources = ("a.py", "b.py")
    before = provenance.head_pipeline_version(sources)
    (tmp_path / "a.py").write_bytes(b"")
    (tmp_path / "b.py").write_bytes(b"shared\n")
    assert provenance.head_pipeline_version(sources) != before


def test_pipeline_version_refuses_a_source_that_does_not_exist(tmp_path, monkeypatch):
    """A digest over a file that is not there is not a digest.

    Skipping a missing path would let a rename quietly empty the hash input and
    turn this check into one that cannot fail — the defect it was added to
    remove.
    """
    _pipeline_fixture(tmp_path, monkeypatch, {"a.py": b"one\n"})
    with pytest.raises(SystemExit):
        provenance.head_pipeline_version(("a.py", "gone.py"))


def test_declared_pipeline_sources_all_exist_in_this_checkout():
    """PIPELINE_SOURCES is a live pointer, not a comment.

    This is the test that fails if `assistant/answer.py` or
    `assistant/retrieve.py` is renamed without updating the tuple, rather than
    letting the rename be discovered by a silently weaker gate.
    """
    for rel in provenance.PIPELINE_SOURCES:
        assert (provenance.config.REPO_ROOT / rel).is_file(), rel


def test_provenance_block_declares_the_pipeline_version():
    block = provenance.provenance_block("run-1")
    assert block["pipeline_version"] == provenance.head_pipeline_version()
    assert block["pipeline_version"]
