"""Negative controls (issue #212).

The controls exist to answer "how much of this score is retrieval". The tests
here are mostly about the one way that question can be answered wrongly: a
control that silently does not apply, or an assertion that cannot fail. A
sabotage that no-ops reads as a pass, so each control is checked for having
actually changed what the assistant was given.
"""

from __future__ import annotations

import pytest

from assistant import config
from assistant.ingest import Chunk
from assistant.retrieve import Retriever, ScoredChunk
from evals import controls


def _chunk(doc_id: str, agency: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#0",
        doc_id=doc_id,
        agency=agency,
        agency_full=agency,
        doc_title="Fares",
        url=f"https://example.org/{doc_id}",
        fetch_date="2026-06-12",
        language="en",
        section="Fares",
        text=text,
    )


@pytest.fixture
def small_retriever():
    chunks = [
        _chunk("mst-fares", "MST", "The MST adult fare is $2.00 for a single ride."),
        _chunk("yolobus-fares", "Yolobus", "The Yolobus adult fare is $2.25 for a single ride."),
    ]
    return Retriever(chunks, config.RetrievalConfig())


def _arm(name: str, rows: dict[str, tuple[int, int]], cases: int = 10) -> controls.ArmResult:
    arm = controls.ArmResult(name, cases=cases, cases_passed=0)
    arm.checks = {check: [passed, total] for check, (passed, total) in rows.items()}
    return arm


class TestTheControlsActuallyApply:
    """A control that no-ops is worse than no control: it reports agreement it
    never measured."""

    def test_no_retrieval_hands_the_assistant_nothing(self, small_retriever):
        control = controls.NoRetrieval(small_retriever)
        assert small_retriever.search("What is the MST adult fare?")
        assert control.search("What is the MST adult fare?") == []

    def test_no_retrieval_is_never_confident(self, small_retriever):
        """Delegating confidence on an empty result set would be asking the real
        retriever to band scores that do not exist."""
        assert not controls.NoRetrieval(small_retriever).confident("q", [])

    def test_wrong_agency_returns_a_different_agency(self, small_retriever):
        control = controls.WrongAgency(small_retriever, ["MST", "Yolobus"])
        question = "What is the MST adult fare?"
        real = {sc.chunk.agency for sc in small_retriever.search(question)}
        swapped = {sc.chunk.agency for sc in control.search(question)}
        assert real == {"MST"}
        assert swapped == {"Yolobus"}

    def test_wrong_agency_is_deterministic(self, small_retriever):
        """A control that shuffles differently each run cannot be compared
        across runs, and reproducibility is this harness's whole claim."""
        control = controls.WrongAgency(small_retriever, ["MST", "Yolobus"])
        first = [sc.chunk.chunk_id for sc in control.search("MST adult fare")]
        second = [sc.chunk.chunk_id for sc in control.search("MST adult fare")]
        assert first == second

    def test_a_control_leaves_the_confidence_band_to_the_real_retriever(self, small_retriever):
        """The only variable is the passages. A control that also moved the
        decline threshold would be measuring two things at once."""
        control = controls.WrongAgency(small_retriever, ["MST", "Yolobus"])
        results = small_retriever.search("MST adult fare")
        assert control.confident("MST adult fare", results) == small_retriever.confident(
            "MST adult fare", results
        )
        assert control.cfg is small_retriever.cfg

    def test_the_base_control_refuses_to_be_used_without_a_search(self, small_retriever):
        with pytest.raises(NotImplementedError):
            controls._ControlRetriever(small_retriever).search("q")

    def test_the_stale_corpus_arm_is_absent_rather_than_silently_the_baseline(self, monkeypatch):
        """A repository retaining no earlier corpus version must lose the arm,
        not get a second copy of the baseline wearing its name."""
        monkeypatch.setattr(controls.corpus, "list_versions", lambda: [])
        harness = controls.Harness(
            cfg=config.Config(),
            model=controls.get_model("mock", "mock"),
            chunks=[_chunk("mst-fares", "MST", "The fare is $2.00.")],
            corpus_doc_ids={"mst-fares"},
            facts_by_doc={},
            doc_texts={"mst-fares": "The fare is $2.00."},
        )
        arms = controls.control_retrievers(harness)
        assert controls.STALE_CORPUS not in arms
        assert set(arms) == {controls.BASELINE, controls.NO_RETRIEVAL, controls.WRONG_AGENCY}


class TestArmResult:
    def test_a_check_the_arm_never_emitted_is_none_not_zero(self):
        """An absence rendered as a measurement is the defect class this whole
        module exists to catch; it must not appear in the module itself."""
        assert _arm("x", {}).rate("citation_present_and_resolvable") is None

    def test_a_rate_is_over_the_checks_that_were_emitted(self):
        assert _arm("x", {"c": (3, 4)}).rate("c") == 75.0

    def test_a_case_passes_only_when_every_check_passes(self):
        arm = controls.ArmResult("x")
        arm.record([("a", True), ("b", True)])
        arm.record([("a", True), ("b", False)])
        assert arm.cases == 2
        assert arm.cases_passed == 1


class TestDirectionAssertions:
    def _healthy(self, **overrides) -> dict[str, controls.ArmResult]:
        arms = {
            controls.BASELINE: _arm(
                controls.BASELINE,
                {
                    "citation_present_and_resolvable": (997, 1000),
                    "correct_agency_cited": (981, 1000),
                },
            ),
            controls.NO_RETRIEVAL: _arm(
                controls.NO_RETRIEVAL, {"citation_present_and_resolvable": (0, 1000)}
            ),
            controls.WRONG_AGENCY: _arm(controls.WRONG_AGENCY, {"correct_agency_cited": (0, 1000)}),
            controls.STALE_CORPUS: _arm(
                controls.STALE_CORPUS, {"citation_present_and_resolvable": (521, 1000)}
            ),
        }
        arms.update(overrides)
        return arms

    def test_the_measured_shape_holds(self):
        assert controls.violations(self._healthy()) == []

    def test_a_planted_always_pass_check_trips_the_gate(self):
        """The scenario #212 asks for: a check that cannot fail. Wire
        `citation_present_and_resolvable` to pass unconditionally and the
        no-retrieval arm reports 100% for a citation it could not have had."""
        planted = self._healthy(
            **{
                controls.NO_RETRIEVAL: _arm(
                    controls.NO_RETRIEVAL, {"citation_present_and_resolvable": (1000, 1000)}
                )
            }
        )
        problems = controls.violations(planted)
        assert any("no_retrieval" in p and "ceiling" in p for p in problems)

    def test_a_wrong_agency_arm_that_still_cites_the_right_agency_trips_the_gate(self):
        planted = self._healthy(
            **{
                controls.WRONG_AGENCY: _arm(
                    controls.WRONG_AGENCY, {"correct_agency_cited": (900, 1000)}
                )
            }
        )
        assert any("wrong_agency" in p for p in controls.violations(planted))

    def test_a_stale_corpus_that_scores_like_the_current_one_trips_the_gate(self):
        planted = self._healthy(
            **{
                controls.STALE_CORPUS: _arm(
                    controls.STALE_CORPUS, {"citation_present_and_resolvable": (990, 1000)}
                )
            }
        )
        assert any("stale_corpus" in p for p in controls.violations(planted))

    def test_a_control_that_never_emitted_its_check_is_a_failure_not_a_pass(self):
        """The sabotage-that-no-ops case. An arm reporting nothing for the check
        it was built to move did not run as a control."""
        planted = self._healthy(**{controls.NO_RETRIEVAL: _arm(controls.NO_RETRIEVAL, {})})
        assert any("was not actually applied" in p for p in controls.violations(planted))

    def test_a_weak_baseline_cannot_be_told_apart_from_a_control(self):
        weak = self._healthy(
            **{
                controls.BASELINE: _arm(
                    controls.BASELINE,
                    {
                        "citation_present_and_resolvable": (500, 1000),
                        "correct_agency_cited": (981, 1000),
                    },
                )
            }
        )
        assert any("under its 90.0% floor" in p for p in controls.violations(weak))

    def test_a_baseline_missing_a_floor_check_is_a_failure(self):
        arms = self._healthy(
            **{
                controls.BASELINE: _arm(
                    controls.BASELINE, {"citation_present_and_resolvable": (997, 1000)}
                )
            }
        )
        assert any("prove nothing about it" in p for p in controls.violations(arms))

    def test_no_baseline_means_no_control_can_be_read(self):
        arms = self._healthy()
        del arms[controls.BASELINE]
        assert controls.violations(arms) == [
            "no baseline arm was run, so no control can be interpreted"
        ]

    def test_the_case_pass_rate_is_not_asserted_on(self):
        """Offline, the no-retrieval control scores HIGHER than the baseline
        because every refusal case passes. An overall-rate assertion would have
        shipped green and measured nothing."""
        arms = self._healthy()
        arms[controls.NO_RETRIEVAL].cases_passed = 10_000
        assert controls.violations(arms) == []


class TestRendering:
    def test_the_table_shows_a_missing_measurement_as_a_dash(self):
        rendered = controls.render(
            {
                controls.BASELINE: _arm(
                    controls.BASELINE,
                    {
                        "citation_present_and_resolvable": (997, 1000),
                        "correct_agency_cited": (981, 1000),
                    },
                ),
                controls.NO_RETRIEVAL: _arm(
                    controls.NO_RETRIEVAL, {"citation_present_and_resolvable": (0, 1000)}
                ),
            }
        )
        assert "99.7%" in rendered and "0.0%" in rendered
        assert "--" in rendered  # no_retrieval never reaches correct_agency_cited
        assert "never asserted on" in rendered


class TestCli:
    def test_a_sample_run_reports_without_gating(self, capsys):
        """`--limit` slices a population the floors were never measured against,
        so it reports and exits 0. The gate is the full run."""
        assert controls.main(["--limit", "3"]) == 0
        out = capsys.readouterr().out
        assert "assertions reported, not enforced" in out

    def test_the_full_run_holds_every_direction_assertion(self, capsys):
        """The gate itself, on the committed corpus. This is what `make
        controls` runs."""
        assert controls.main([]) == 0
        assert "direction assertion(s) hold" in capsys.readouterr().out


class TestScoredChunkPlumbing:
    def test_a_control_returns_scored_chunks_the_answer_path_understands(self, small_retriever):
        control = controls.WrongAgency(small_retriever, ["MST", "Yolobus"])
        results = control.search("MST adult fare")
        assert results and all(isinstance(sc, ScoredChunk) for sc in results)
