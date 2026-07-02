"""Provenance gate tests (evals/provenance.py).

The gate is the mechanism that stops a stale EVALS.md / baseline.json /
golden.jsonl from silently describing a different system than HEAD. These
tests pin the three artifact readers, the mismatch detector, and the loud
file-based acknowledgement escape — all with injected inputs so nothing here
depends on the committed artifacts (which the gate itself checks in CI).
"""

from __future__ import annotations

import json

import pytest

from evals import provenance


def _payload(corpus="c1", system="v6", answer="v3", judge_g="v1", judge_h="v2", run_id="r1"):
    return {
        "run_id": run_id,
        "corpus_version": corpus,
        "prompt_versions": {
            "system": system,
            "answer_user": answer,
            "judge_groundedness": judge_g,
            "judge_helpfulness": judge_h,
        },
    }


# ── readers ──────────────────────────────────────────────────────────────────


class TestReadEvalsMd:
    def test_extracts_block_from_report_text(self):
        payload = _payload()
        text = "# Report\n\nbody\n\n" + provenance.render_evals_md_block(payload) + "\n"
        assert provenance.read_evals_md(text) == payload

    def test_returns_none_when_absent(self):
        assert provenance.read_evals_md("# Report with no provenance block\n") is None

    def test_ignores_trailing_content_after_block(self):
        payload = _payload(corpus="cX")
        block = provenance.render_evals_md_block(payload)
        assert provenance.read_evals_md(f"intro\n{block}\ntrailing prose") == payload


class TestReadBaseline:
    def test_reads_top_level_provenance_object(self):
        payload = _payload()
        assert provenance.read_baseline({"from_run": "x", "provenance": payload}) == payload

    def test_returns_none_when_missing(self):
        assert provenance.read_baseline({"from_run": "x", "suites": {}}) is None


class TestReadGolden:
    def test_reads_provenance_comment_line(self):
        payload = _payload()
        line = "# provenance: " + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        text = "# header\n# more header\n" + line + '\n{"id": "a"}\n'
        assert provenance.read_golden(text) == payload

    def test_returns_none_when_no_provenance_line(self):
        text = "# header only\n{\"id\": \"a\"}\n"
        assert provenance.read_golden(text) is None

    def test_tolerates_leading_and_inner_whitespace(self):
        payload = _payload()
        line = "#   provenance:   " + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assert provenance.read_golden("noise\n" + line + "\n") == payload


# ── _compare ─────────────────────────────────────────────────────────────────


class TestCompare:
    def _expected(self):
        return {
            "system": "v6",
            "answer_user": "v3",
            "judge_groundedness": "v1",
            "judge_helpfulness": "v2",
        }, "c1"

    def test_no_mismatch_when_everything_agrees(self):
        prompts, corpus = self._expected()
        assert provenance._compare("EVALS.md", _payload(), prompts, corpus) == []

    def test_flags_a_missing_block(self):
        prompts, corpus = self._expected()
        out = provenance._compare("baseline.json", None, prompts, corpus)
        assert len(out) == 1 and out[0].field == "provenance" and out[0].declared is None

    def test_flags_corpus_drift(self):
        prompts, corpus = self._expected()
        out = provenance._compare("EVALS.md", _payload(corpus="OLD"), prompts, corpus)
        fields = {m.field for m in out}
        assert fields == {"corpus_version"}
        assert out[0].declared == "OLD" and out[0].expected == "c1"

    def test_flags_each_drifted_prompt(self):
        prompts, corpus = self._expected()
        declared = _payload(system="v5", judge_h="v1")
        out = provenance._compare("EVALS.md", declared, prompts, corpus)
        fields = {m.field for m in out}
        assert fields == {"prompt_versions.system", "prompt_versions.judge_helpfulness"}

    def test_flags_prompt_absent_from_declared_block(self):
        prompts, corpus = self._expected()
        declared = {"corpus_version": "c1", "prompt_versions": {"system": "v6"}}
        out = provenance._compare("golden.jsonl", declared, prompts, corpus)
        assert {m.field for m in out} == {
            "prompt_versions.answer_user",
            "prompt_versions.judge_groundedness",
            "prompt_versions.judge_helpfulness",
        }


# ── check_all + acknowledgements ─────────────────────────────────────────────


def _matching_inputs(monkeypatch, corpus="c1"):
    """Pin HEAD versions and return artifact inputs that all agree with them."""
    prompts = {
        "system": "v6",
        "answer_user": "v3",
        "judge_groundedness": "v1",
        "judge_helpfulness": "v2",
    }
    monkeypatch.setattr(provenance, "head_prompt_versions", lambda names=None: dict(prompts))
    monkeypatch.setattr(provenance, "head_corpus_version", lambda: corpus)
    evals_md = provenance.render_evals_md_block(_payload(corpus=corpus))
    baseline = {"provenance": _payload(corpus=corpus)}
    golden = "# provenance: " + json.dumps(
        {"corpus_version": corpus, "prompt_versions": {"system": "v6", "answer_user": "v3"}},
        sort_keys=True,
    )
    return evals_md, baseline, golden


class TestCheckAll:
    def test_green_when_all_match_head(self, monkeypatch):
        evals_md, baseline, golden = _matching_inputs(monkeypatch)
        result = provenance.check_all(
            acknowledged=set(), evals_md=evals_md, baseline=baseline, golden=golden
        )
        assert result["failures"] == [] and result["acknowledged"] == []

    def test_unacknowledged_corpus_drift_is_a_failure(self, monkeypatch):
        evals_md, baseline, golden = _matching_inputs(monkeypatch)
        monkeypatch.setattr(provenance, "head_corpus_version", lambda: "NEW")
        result = provenance.check_all(
            acknowledged=set(), evals_md=evals_md, baseline=baseline, golden=golden
        )
        # All three artifacts now declare the old corpus → three failures.
        assert {m.artifact for m in result["failures"]} == {
            "EVALS.md",
            "baseline.json",
            "golden.jsonl",
        }
        assert all(m.field == "corpus_version" for m in result["failures"])

    def test_acknowledgement_downgrades_failure_to_warning(self, monkeypatch):
        evals_md, baseline, golden = _matching_inputs(monkeypatch)
        monkeypatch.setattr(provenance, "head_corpus_version", lambda: "NEW")
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


# ── load_acknowledgements ────────────────────────────────────────────────────


class TestLoadAcknowledgements:
    def test_missing_file_is_empty_set(self, tmp_path):
        assert provenance.load_acknowledgements(tmp_path / "nope.json") == set()

    def test_parses_documented_entries(self, tmp_path):
        p = tmp_path / "ack.json"
        p.write_text(
            json.dumps(
                {
                    "acknowledged": [
                        {"artifact": "EVALS.md", "field": "corpus_version",
                         "reason": "regen gated"},
                        {"artifact": "baseline.json", "field": "prompt_versions.system",
                         "reason": "awaiting live eval"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert provenance.load_acknowledgements(p) == {
            ("EVALS.md", "corpus_version"),
            ("baseline.json", "prompt_versions.system"),
        }

    def test_reasonless_acknowledgement_is_rejected(self, tmp_path):
        p = tmp_path / "ack.json"
        p.write_text(
            json.dumps({"acknowledged": [{"artifact": "EVALS.md", "field": "corpus_version"}]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            provenance.load_acknowledgements(p)


# ── main (the CLI exit-code contract) ────────────────────────────────────────


class TestMain:
    def test_exits_zero_and_prints_match_when_green(self, monkeypatch, capsys):
        monkeypatch.setattr(provenance, "check_all",
                            lambda: {"failures": [], "acknowledged": []})
        monkeypatch.setattr(provenance, "head_corpus_version", lambda: "c1")
        assert provenance.main() == 0
        assert "match HEAD" in capsys.readouterr().out

    def test_exits_one_on_unacknowledged_drift(self, monkeypatch, capsys):
        drift = provenance.Mismatch("EVALS.md", "corpus_version", "OLD", "NEW")
        monkeypatch.setattr(provenance, "check_all",
                            lambda: {"failures": [drift], "acknowledged": []})
        assert provenance.main() == 1
        assert "PROVENANCE DRIFT" in capsys.readouterr().err

    def test_acknowledged_drift_still_exits_zero_but_warns(self, monkeypatch, capsys):
        warn = provenance.Mismatch("baseline.json", "corpus_version", "OLD", "NEW")
        monkeypatch.setattr(provenance, "check_all",
                            lambda: {"failures": [], "acknowledged": [warn]})
        monkeypatch.setattr(provenance, "head_corpus_version", lambda: "NEW")
        assert provenance.main() == 0
        assert "ACKNOWLEDGED" in capsys.readouterr().err

    def test_fmt_is_human_readable(self):
        m = provenance.Mismatch("EVALS.md", "corpus_version", "OLD", "NEW")
        s = provenance._fmt(m)
        assert "EVALS.md" in s and "OLD" in s and "NEW" in s
