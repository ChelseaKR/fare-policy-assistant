"""Guards for `evals.drift` — answer drift across two corpus versions (#216).

The three claims the tool makes are exercised against the **committed corpus
versions**, not against fixtures, because the claims are about this repository's
own retained history:

* a refresh that touches one document explains every answer that moved,
* comparing a version to itself costs nothing on the second side,
* an answer that moved with no cited document moving fails the gate.

Everything else here is a fixture test of one function at a time.
"""

from __future__ import annotations

import json

import pytest

from assistant import config, corpus
from assistant.models import Completion
from evals import drift
from tests.conftest import make_chunk

# Two adjacent committed corpus versions whose only difference is one Yolobus
# fare page. Named rather than discovered so a failure says which pair went
# missing instead of silently comparing some other pair.
ONE_PAGE_FROM = "35ec70d6359d"
ONE_PAGE_TO = "74b05330cb39"
ONE_PAGE_DOC = "yolobus-fares"


@pytest.fixture(scope="module")
def committed_versions() -> list[str]:
    versions = corpus.list_versions()
    for needed in (ONE_PAGE_FROM, ONE_PAGE_TO):
        assert needed in versions, (
            f"corpus version {needed} is no longer retained under "
            f"{config.VERSIONS_DIR}; this guard compares two real committed "
            "versions and cannot be satisfied by a fixture"
        )
    return versions


# ── sides ────────────────────────────────────────────────────────────────────


class TestSides:
    def test_a_side_with_no_chunks_is_refused_rather_than_compared(self):
        with pytest.raises(drift.DriftError, match="holds no chunks"):
            drift.build_side("empty", [], config.Config())

    def test_an_unarchived_version_is_an_error_not_a_fallback_to_live(self):
        """A side that quietly degraded to the live corpus would report agreement
        it never measured — the defect class this whole tool exists to surface."""
        with pytest.raises(drift.DriftError, match="not archived"):
            drift.load_side("no-such-version", config.Config())

    def test_live_is_the_working_tree_corpus_under_its_own_version_id(self):
        side = drift.load_side(drift.LIVE, config.Config())
        assert side.version == corpus.corpus_version(corpus.load_chunks())
        assert side.doc_ids

    def test_none_and_live_name_the_same_side(self):
        assert drift.load_side(None, config.Config()).version == (
            drift.load_side(drift.LIVE, config.Config()).version
        )

    def test_facts_are_derived_from_the_sides_own_chunks(self, committed_versions):
        """Scoring an old answer against the live `facts.jsonl` would be two rulers.

        The committed table describes the *live* corpus only, so an older side
        must derive its own or every fare-fact check on that side is measured
        against documents it does not have.
        """
        cfg = config.Config()
        old = drift.load_side(ONE_PAGE_FROM, cfg)
        live = drift.load_side(drift.LIVE, cfg)
        assert old.facts_by_doc, "no facts were derived, so a fare-fact check scores nothing"
        assert set(old.facts_by_doc) <= set(old.doc_ids)
        assert set(live.facts_by_doc) - set(old.facts_by_doc), (
            "both sides derived the same fact table, so this guard proves nothing "
            "about per-side derivation"
        )

    def test_doc_texts_join_every_chunk_of_a_document(self):
        side = drift.build_side(
            "v",
            [
                make_chunk(chunk_id="mst-fares#0", text="first"),
                make_chunk(chunk_id="mst-fares#1", text="second"),
            ],
            config.Config(),
        )
        assert side.doc_texts["mst-fares"] == "first\nsecond"

    def test_a_snapshot_file_is_a_usable_from_side(self, tmp_path):
        """The weekly refresh writes this before re-processing overwrites the
        corpus, so it is the only record of the pre-refresh version at the moment
        the refresh PR is built."""
        from tools.corpus_refresh_report import write_snapshot

        path = tmp_path / "old.json"
        chunks = corpus.load_chunks()
        version = write_snapshot(path, chunks)
        side = drift.side_from_snapshot(path, config.Config())
        assert side.version == version
        assert len(side.chunks) == len(chunks)

    def test_a_file_that_is_not_a_snapshot_is_refused(self, tmp_path):
        path = tmp_path / "not-a-snapshot.json"
        path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        with pytest.raises(drift.DriftError, match="not a corpus snapshot"):
            drift.side_from_snapshot(path, config.Config())


# ── the shared model ─────────────────────────────────────────────────────────


class _CountingModel:
    def __init__(self):
        self.seen: list[str] = []

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        self.seen.append(user)
        return Completion(text=f"answer for {user}", model="stub")


class TestSharedAnswerModel:
    def _model(self) -> tuple[drift.SharedAnswerModel, _CountingModel]:
        inner = _CountingModel()
        return drift.SharedAnswerModel(inner, provider="mock", model_id="mock"), inner

    def test_an_identical_rendered_prompt_is_answered_once_and_reused(self):
        shared, inner = self._model()
        shared.for_side("from")
        first = shared.complete(system="s", user="u", max_tokens=8, temperature=0.0)
        shared.for_side("to")
        again = shared.complete(system="s", user="u", max_tokens=8, temperature=0.0)
        assert again is first
        assert len(inner.seen) == 1
        assert shared.calls == {"from": 1, "to": 0}
        assert shared.reused == {"from": 0, "to": 1}

    def test_a_moved_prompt_is_a_real_call_on_the_second_side(self):
        shared, inner = self._model()
        shared.for_side("from")
        shared.complete(system="s", user="u", max_tokens=8, temperature=0.0)
        shared.for_side("to")
        shared.complete(system="s", user="u-moved", max_tokens=8, temperature=0.0)
        assert len(inner.seen) == 2
        assert shared.calls == {"from": 1, "to": 1}

    def test_the_temperature_and_token_budget_are_part_of_the_key(self):
        shared, inner = self._model()
        shared.for_side("from")
        shared.complete(system="s", user="u", max_tokens=8, temperature=0.0)
        shared.complete(system="s", user="u", max_tokens=8, temperature=0.7)
        shared.complete(system="s", user="u", max_tokens=9, temperature=0.0)
        assert len(inner.seen) == 3

    def test_the_offline_default_is_the_mock_backend(self):
        model = drift.build_model(live=False, cfg=config.Config())
        assert (model.provider, model.model_id) == ("mock", "mock")

    def test_live_uses_the_configured_provider(self):
        cfg = config.Config()
        model = drift.build_model(live=True, cfg=cfg)
        assert model.provider == cfg.models.provider
        assert model.model_id == cfg.models.answer_model


# ── classification ───────────────────────────────────────────────────────────


def test_a_removed_document_counts_as_a_document_that_moved():
    """An answer still citing a deleted page has had a source move under it just
    as surely as one whose text was edited."""
    moved = drift.changed_doc_ids({"added": ["a"], "removed": ["b"], "changed": ["c"]})
    assert moved == frozenset({"a", "b", "c"})


class _StubAnswer:
    """Enough of `AnswerResult` for `compare_case`, with a scripted answer per side."""

    def __init__(self, text: str, doc_ids: list[str]):
        from assistant.answer import Citation

        self.answer = text
        self.kind = "answered"
        self.citations = [
            Citation(doc_id=d, agency="MST", title="t", url="https://x/", fetch_date="2026-01-01")
            for d in doc_ids
        ]


def _compare_with(monkeypatch, scripted, moved, *, checks_pass):
    """Drive `compare_case` with scripted per-side answers and check verdicts."""
    from evals.checks import CheckResult

    calls = iter(scripted)
    monkeypatch.setattr(drift, "answer_question", lambda *a, **k: next(calls))
    verdicts = iter(checks_pass)
    monkeypatch.setattr(
        drift,
        "run_checks",
        lambda *a, **k: [CheckResult("stub_check", next(verdicts), "")],
    )
    side = drift.build_side("v", [make_chunk()], config.Config())
    model = drift.build_model(live=False, cfg=config.Config())
    return drift.compare_case(
        {"id": "c1", "suite": "s", "question": "q", "expected_behavior": "answer"},
        from_side=side,
        to_side=side,
        model=model,
        cfg=config.Config(),
        moved=frozenset(moved),
    )


class TestClassification:
    def test_identical_answers_are_unchanged(self, monkeypatch):
        row = _compare_with(
            monkeypatch,
            [_StubAnswer("same", ["mst-fares"]), _StubAnswer("same", ["mst-fares"])],
            moved={"mst-fares"},
            checks_pass=[True, True],
        )
        assert row.classification == drift.UNCHANGED
        assert not row.newly_failing

    def test_a_changed_answer_citing_a_moved_document_is_expected(self, monkeypatch):
        row = _compare_with(
            monkeypatch,
            [_StubAnswer("before", ["mst-fares"]), _StubAnswer("after", ["mst-fares"])],
            moved={"mst-fares"},
            checks_pass=[True, True],
        )
        assert row.classification == drift.CHANGED_EXPECTED
        assert row.explained_by == ["mst-fares"]

    def test_a_changed_answer_citing_nothing_that_moved_is_the_finding(self, monkeypatch):
        row = _compare_with(
            monkeypatch,
            [_StubAnswer("before", ["mst-fares"]), _StubAnswer("after", ["sacrt-fares"])],
            moved={"yolobus-fares"},
            checks_pass=[True, True],
        )
        assert row.classification == drift.CHANGED_UNEXPECTED
        assert row.explained_by == []
        assert row.cited_from == ["mst-fares"]
        assert row.cited_to == ["sacrt-fares"]

    def test_an_unchanged_answer_can_still_be_newly_failing(self, monkeypatch):
        """The reason `newly_failing` is a flag and not a fourth bucket.

        A byte-identical answer whose cited document was removed fails
        `citation_present_and_resolvable` on the new side over the same text. A
        four-way partition would have filed this as `unchanged` and hidden it.
        """
        row = _compare_with(
            monkeypatch,
            [_StubAnswer("same", ["gone-doc"]), _StubAnswer("same", ["gone-doc"])],
            moved={"gone-doc"},
            checks_pass=[True, False],
        )
        assert row.classification == drift.UNCHANGED
        assert row.newly_failing
        assert row.failed_checks_to == ["stub_check"]

    def test_a_case_already_failing_before_is_not_newly_failing(self, monkeypatch):
        row = _compare_with(
            monkeypatch,
            [_StubAnswer("a", ["mst-fares"]), _StubAnswer("b", ["mst-fares"])],
            moved={"mst-fares"},
            checks_pass=[False, False],
        )
        assert not row.newly_failing

    def test_a_multi_turn_case_is_compared_on_its_last_turn(self, monkeypatch):
        seen: list[str] = []

        def _answer(question, **kwargs):
            seen.append(question)
            return _StubAnswer("x", ["mst-fares"])

        from evals.checks import CheckResult

        monkeypatch.setattr(drift, "answer_question", _answer)
        monkeypatch.setattr(drift, "run_checks", lambda *a, **k: [CheckResult("c", True, "")])
        side = drift.build_side("v", [make_chunk()], config.Config())
        drift.compare_case(
            {"id": "c", "suite": "s", "turns": ["first", "second"], "expected_behavior": "answer"},
            from_side=side,
            to_side=side,
            model=drift.build_model(live=False, cfg=config.Config()),
            cfg=config.Config(),
            moved=frozenset(),
        )
        assert seen == ["second", "second"]


# ── report and gate ──────────────────────────────────────────────────────────


def _report(rows, **kw) -> drift.DriftReport:
    defaults = {
        "from_version": "aaa",
        "to_version": "bbb",
        "corpus_diff": {"added": [], "removed": [], "changed": ["mst-fares"]},
        "cases": rows,
        "model_calls": {"from": 3, "to": 1},
        "reused": {"from": 0, "to": 2},
    }
    defaults.update(kw)
    return drift.DriftReport(**defaults)


def _row(**kw) -> drift.CaseDrift:
    defaults = {
        "case_id": "c1",
        "suite": "s",
        "question": "q",
        "classification": drift.UNCHANGED,
    }
    defaults.update(kw)
    return drift.CaseDrift(**defaults)


class TestReport:
    def test_counts_partition_the_cases_and_flag_newly_failing_separately(self):
        report = _report(
            [
                _row(),
                _row(classification=drift.CHANGED_EXPECTED),
                _row(classification=drift.CHANGED_UNEXPECTED),
                _row(newly_failing=True),
            ]
        )
        counts = report.counts
        assert counts[drift.UNCHANGED] == 2
        assert counts[drift.CHANGED_EXPECTED] == 1
        assert counts[drift.CHANGED_UNEXPECTED] == 1
        assert counts["newly_failing"] == 1
        assert sum(counts[c] for c in drift.CLASSIFICATIONS) == len(report.cases)

    def test_an_unexpected_change_is_rendered_with_both_answers(self):
        report = _report(
            [
                _row(
                    classification=drift.CHANGED_UNEXPECTED,
                    cited_from=["mst-fares"],
                    cited_to=["sacrt-fares"],
                    answer_from="the old answer",
                    answer_to="the new answer",
                )
            ]
        )
        markdown = drift.render_markdown(report, max_unexpected=0)
        assert "the old answer" in markdown
        assert "the new answer" in markdown
        assert "`mst-fares`" in markdown and "`sacrt-fares`" in markdown

    def test_a_clean_report_says_so_rather_than_rendering_an_empty_section(self):
        markdown = drift.render_markdown(_report([_row()]), max_unexpected=0)
        assert "Every answer that moved cites a document that moved" in markdown

    def test_the_cost_of_the_comparison_is_in_the_report(self):
        markdown = drift.render_markdown(_report([_row()]), max_unexpected=0)
        assert "Model calls" in markdown and "reused" in markdown

    def test_a_diff_that_moved_nothing_says_so(self):
        report = _report([_row()], corpus_diff={"added": [], "removed": [], "changed": []})
        assert "No document was added, removed, or changed." in drift.render_markdown(
            report, max_unexpected=0
        )

    def test_newly_failing_rows_are_listed_with_the_checks_they_failed(self):
        report = _report([_row(newly_failing=True, failed_checks_to=["citation_present"])])
        assert "citation_present" in drift.render_markdown(report, max_unexpected=0)

    def test_expected_changes_name_the_document_that_explains_them(self):
        report = _report(
            [_row(classification=drift.CHANGED_EXPECTED, explained_by=["yolobus-fares"])]
        )
        assert "`yolobus-fares`" in drift.render_markdown(report, max_unexpected=0)

    def test_a_long_answer_is_excerpted_and_says_that_it_was(self):
        report = _report(
            [_row(classification=drift.CHANGED_UNEXPECTED, answer_from="x" * 900, answer_to="y")]
        )
        assert "truncated, 900 chars total" in drift.render_markdown(report, max_unexpected=0)

    def test_an_empty_answer_renders_as_empty_rather_than_as_nothing(self):
        report = _report(
            [_row(classification=drift.CHANGED_UNEXPECTED, answer_from="", answer_to="y")]
        )
        assert "(empty)" in drift.render_markdown(report, max_unexpected=0)

    def test_the_json_report_carries_both_versions_and_the_cost(self):
        payload = _report([_row()]).to_json_dict()
        assert payload["from_version"] == "aaa" and payload["to_version"] == "bbb"
        assert payload["model_calls"] == {"from": 3, "to": 1}
        assert payload["reused"] == {"from": 0, "to": 2}
        assert payload["counts"][drift.UNCHANGED] == 1
        assert payload["cases"][0]["case_id"] == "c1"


class TestGate:
    def test_the_gate_fires_above_the_ceiling(self):
        report = _report([_row(classification=drift.CHANGED_UNEXPECTED)] * 3)
        problems = drift.gate_problems(report, max_unexpected=2)
        assert problems and "above the ceiling of 2" in problems[0]

    def test_the_gate_is_silent_at_the_ceiling(self):
        report = _report([_row(classification=drift.CHANGED_UNEXPECTED)] * 2)
        assert drift.gate_problems(report, max_unexpected=2) == []

    def test_newly_failing_alone_does_not_stop_a_refresh(self):
        """A refresh that really changes a fare *should* move eval outcomes; the
        smoke suite on the refresh PR is what scores that. This gate is only
        about answers that moved with no source behind them."""
        report = _report([_row(newly_failing=True)])
        assert drift.gate_problems(report, max_unexpected=0) == []


def test_a_run_with_no_cases_is_an_error_not_an_empty_pass():
    side = drift.build_side("v", [make_chunk()], config.Config())
    with pytest.raises(drift.DriftError, match="nothing to compare"):
        drift.run_drift(
            [],
            from_side=side,
            to_side=side,
            model=drift.build_model(live=False, cfg=config.Config()),
            cfg=config.Config(),
        )


# ── the three claims, against the committed corpus ───────────────────────────


class TestAgainstTheCommittedCorpus:
    def test_a_one_document_refresh_explains_every_answer_that_moved(self, committed_versions):
        cfg = config.Config()
        report = drift.run_drift(
            drift._select_cases(0, None),
            from_side=drift.load_side(ONE_PAGE_FROM, cfg),
            to_side=drift.load_side(ONE_PAGE_TO, cfg),
            model=drift.build_model(live=False, cfg=cfg),
            cfg=cfg,
        )
        assert report.corpus_diff["changed"] == [ONE_PAGE_DOC]
        assert not report.corpus_diff["added"] and not report.corpus_diff["removed"]
        changed = report.of(drift.CHANGED_EXPECTED)
        assert changed, "the refresh moved no answer at all; the comparison proves nothing"
        for row in changed:
            assert row.explained_by == [ONE_PAGE_DOC]
        assert report.counts[drift.CHANGED_UNEXPECTED] == 0

    def test_comparing_a_version_to_itself_costs_nothing_on_the_second_side(
        self, committed_versions
    ):
        cfg = config.Config()
        report = drift.run_drift(
            drift._select_cases(0, None),
            from_side=drift.load_side(ONE_PAGE_TO, cfg),
            to_side=drift.load_side(ONE_PAGE_TO, cfg),
            model=drift.build_model(live=False, cfg=cfg),
            cfg=cfg,
        )
        assert report.counts[drift.UNCHANGED] == len(report.cases)
        assert report.counts[drift.CHANGED_EXPECTED] == 0
        assert report.counts[drift.CHANGED_UNEXPECTED] == 0
        assert report.model_calls["to"] == 0
        assert report.model_calls["from"] > 0, (
            "the first side made no model calls either, so 'zero on the second' "
            "would be true of a comparison that ran nothing"
        )


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCli:
    def test_a_clean_comparison_exits_zero_and_writes_both_reports(
        self, tmp_path, committed_versions, capsys
    ):
        md, js = tmp_path / "d.md", tmp_path / "d.json"
        code = drift.main(
            [
                "--from",
                ONE_PAGE_FROM,
                "--to",
                ONE_PAGE_TO,
                "--out",
                str(md),
                "--json",
                str(js),
            ]
        )
        assert code == 0
        assert "## Answer drift" in md.read_text(encoding="utf-8")
        payload = json.loads(js.read_text(encoding="utf-8"))
        assert payload["from_version"] == ONE_PAGE_FROM
        assert payload["to_version"] == ONE_PAGE_TO
        assert "## Answer drift" in capsys.readouterr().out

    def test_an_unarchived_version_exits_one_with_the_reason(self, capsys):
        assert drift.main(["--from", "not-a-version"]) == 1
        assert "not archived" in capsys.readouterr().err

    def test_a_sample_reports_the_ceiling_rather_than_enforcing_it(
        self, committed_versions, capsys
    ):
        """The ceiling is a statement about the whole case set; a slice can breach
        or clear it for reasons that have nothing to do with the corpus change."""
        code = drift.main(
            ["--from", ONE_PAGE_FROM, "--to", ONE_PAGE_TO, "--limit", "5", "--max-unexpected", "0"]
        )
        assert code == 0
        assert "reported, not enforced" in capsys.readouterr().out

    def test_the_gate_fails_the_run_when_answers_moved_with_no_source(
        self, monkeypatch, committed_versions, capsys
    ):
        real = drift.run_drift

        def _with_an_unexpected_change(*args, **kwargs):
            report = real(*args, **kwargs)
            report.cases[0].classification = drift.CHANGED_UNEXPECTED
            return report

        monkeypatch.setattr(drift, "run_drift", _with_an_unexpected_change)
        code = drift.main(["--from", ONE_PAGE_FROM, "--to", ONE_PAGE_TO, "--suite", "refusal"])
        # `--suite` is a slice, so the ceiling is reported rather than enforced.
        assert code == 0
        assert "changed with no cited document moving" in capsys.readouterr().out

        monkeypatch.setattr(drift, "run_drift", _with_an_unexpected_change)
        assert drift.main(["--from", ONE_PAGE_FROM, "--to", ONE_PAGE_TO]) == 1
        assert "DRIFT GATE FAILED" in capsys.readouterr().err

    def test_a_snapshot_is_accepted_as_the_from_side(self, tmp_path, capsys):
        from tools.corpus_refresh_report import write_snapshot

        path = tmp_path / "old.json"
        version = write_snapshot(path, corpus.load_chunks())
        assert drift.main(["--from-snapshot", str(path), "--limit", "3"]) == 0
        assert version in capsys.readouterr().out

    def test_selecting_one_suite_selects_only_that_suite(self):
        cases = drift._select_cases(0, "refusal")
        assert cases and {c["suite"] for c in cases} == {"refusal"}
