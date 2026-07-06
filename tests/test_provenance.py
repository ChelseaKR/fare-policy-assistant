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
    declared = {"corpus_version": "cv1", "prompt_versions": head_prompts}
    assert provenance._compare("EVALS.md", declared, head_prompts, "cv1") == []


def test_compare_flags_corpus_and_prompt_drift():
    head_prompts = {"system": "v7"}
    declared = {"corpus_version": "old-cv", "prompt_versions": {"system": "v6"}}
    mismatches = provenance._compare("EVALS.md", declared, head_prompts, "new-cv")
    fields = {m.field for m in mismatches}
    assert fields == {"corpus_version", "prompt_versions.system"}


def test_compare_none_declared_is_a_single_mismatch():
    mismatches = provenance._compare("baseline.json", None, {"system": "v1"}, "cv")
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
    all_prompts = dict.fromkeys(provenance.ALL_PROMPTS, "v1")
    answer_prompts = {k: "v1" for k in provenance.ANSWER_PROMPTS}
    evals_md = "x\n" + provenance.render_evals_md_block(
        {"run_id": "r", "corpus_version": "cv1", "prompt_versions": all_prompts}
    )
    baseline = {"provenance": {"corpus_version": "cv1", "prompt_versions": all_prompts}}
    golden = "# provenance: " + json.dumps(
        {"corpus_version": "cv1", "prompt_versions": answer_prompts}
    )
    result = provenance.check_all(
        acknowledged=set(), evals_md=evals_md, baseline=baseline, golden=golden
    )
    assert result == {"failures": [], "acknowledged": []}


def test_check_all_reports_unacknowledged_drift_as_a_failure(monkeypatch):
    monkeypatch.setattr(provenance, "head_prompt_versions", _fixed_prompts("v2"))
    monkeypatch.setattr(provenance, "head_corpus_version", lambda: "cv2")
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
    all_prompts = dict.fromkeys(provenance.ALL_PROMPTS, "v1")
    answer_prompts = {k: "v1" for k in provenance.ANSWER_PROMPTS}
    evals_md = "x\n" + provenance.render_evals_md_block(
        {"run_id": "r", "corpus_version": "cv-old", "prompt_versions": all_prompts}
    )
    baseline = {"provenance": {"corpus_version": "cv-old", "prompt_versions": all_prompts}}
    golden = "# provenance: " + json.dumps(
        {"corpus_version": "cv-old", "prompt_versions": answer_prompts}
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
