"""Retrieval-side parity diagnostic.

The value of `evals/spanish_retrieval_parity.py` is that it separates a
retrieval cause from a model cause when the bilingual parity gate fires, so
what is tested here is the separation itself: an asymmetry that costs the
Spanish side evidence must be reported, and a difference that costs it nothing
must not be — a diagnostic that cries wolf stops being read, which is how the
2026-09-04 gap sat unexplained for two weeks.

`main` builds the real retriever over the committed corpus. That is fast and
offline, but it is an analysis entry point rather than a unit, so the pure
functions are tested directly and `mirror_pairs` is exercised against the real
suites, where the pairing rule (Spanish case in the parity suite, mirror
resolvable anywhere) is the thing worth pinning.
"""

from __future__ import annotations

from evals.runner import PARITY_SUITE
from evals.spanish_retrieval_parity import (
    _fact_present,
    _question,
    _trigger,
    compare,
    findings,
    main,
    mirror_pairs,
    report,
)


def _row(**overrides) -> dict:
    """A pair with no asymmetry; override one field to make a finding."""
    base = {
        "id": "ml-000",
        "mirror": "ground-000",
        "expected": "answer",
        "es_scope": ["MST"],
        "en_scope": ["MST"],
        "es_reduced": False,
        "en_reduced": False,
        "es_child": False,
        "en_child": False,
        "es_facts": True,
        "en_facts": True,
        "evidence": 1.0,
        "es_top": 20.0,
        "en_top": 20.0,
    }
    return {**base, **overrides}


class TestFactPresent:
    def test_literal_fact_found(self):
        assert _fact_present("$2.00", ["La tarifa es $2.00.", "otro"])

    def test_regex_fact_found(self):
        assert _fact_present(r"re:\$\s?1[.,]00", ["los adultos mayores pagan $1,00"])

    def test_fact_absent(self):
        assert not _fact_present("$9.99", ["La tarifa es $2.00."])


class TestQuestion:
    def test_single_turn(self):
        assert _question({"question": "¿Cuánto cuesta?"}) == "¿Cuánto cuesta?"

    def test_multiturn_uses_last_turn(self):
        assert _question({"turns": ["hola", "¿Cuánto cuesta?"]}) == "¿Cuánto cuesta?"


class TestFindings:
    def test_clean_pair_has_no_findings(self):
        assert findings(_row()) == []

    def test_agency_scope_mismatch_is_reported(self):
        found = findings(_row(es_scope=[], en_scope=["SBMTD"]))
        assert len(found) == 1
        assert "unscoped" in found[0] and "SBMTD" in found[0]

    def test_augmentation_firing_for_english_only_is_reported(self):
        assert findings(_row(en_child=True, es_child=False))
        assert findings(_row(en_reduced=True, es_reduced=False))

    def test_augmentation_firing_for_spanish_only_is_not_a_finding(self):
        # The gate measures an equity gap against Spanish. Spanish reaching
        # *more* evidence than its mirror is not one, and reporting it would
        # send someone to "fix" a case that is not broken.
        assert findings(_row(es_child=True, en_child=False)) == []

    def test_facts_missing_on_both_sides_is_not_a_finding(self):
        # Neither side retrieves the fact: that is the corpus or the case,
        # not the Spanish path, and the parity gate would not read it as a
        # gap either because both mirrors fail together.
        assert findings(_row(es_facts=False, en_facts=False)) == []

    def test_facts_missing_only_in_spanish_is_reported(self):
        found = findings(_row(es_facts=False, en_facts=True))
        assert any("required facts absent" in item for item in found)

    def test_refusal_cases_are_never_flagged(self):
        # A refusal is supposed to come back thin on evidence.
        row = _row(expected="refuse_redirect", es_scope=[], en_facts=True, es_facts=False)
        assert findings(row) == []

    def test_unknown_trigger_is_not_a_finding(self):
        # `_trigger` returns None when retrieval has renamed the helper. An
        # unknown value must not be read as a defect.
        assert findings(_row(es_reduced=None, en_reduced=None)) == []


class TestReport:
    def test_clean_run_says_so_and_points_elsewhere(self):
        text = "\n".join(report([_row()]))
        assert "No retrieval-side asymmetry" in text
        assert "answer model" in text

    def test_flagged_pair_names_the_case_and_its_mirror(self):
        text = "\n".join(report([_row(es_scope=[], en_scope=["SBMTD"])]))
        assert "ml-000 (vs ground-000)" in text

    def test_mean_evidence_recall_is_reported(self):
        text = "\n".join(report([_row(evidence=1.0), _row(id="ml-001", evidence=0.5)]))
        assert "75.0%" in text


class TestTrigger:
    def test_reads_the_live_retrieval_gate(self):
        assert _trigger("_is_child_fare_query", "Does my 4-year-old ride free?") is True
        assert _trigger("_is_child_fare_query", "How much is a monthly pass?") is False

    def test_missing_gate_is_unknown_rather_than_false(self):
        # Retrieval may rename its private helpers. A diagnostic that hard-fails
        # the build on that, or silently reports "no", would be worse than one
        # that says it does not know.
        assert _trigger("_is_a_gate_that_does_not_exist", "cualquier pregunta") is None


class TestMain:
    def test_runs_over_the_committed_suites(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["spanish_retrieval_parity"])
        assert main() == 0
        out = capsys.readouterr().out
        assert "Mean mirror-evidence recall" in out

    def test_verbose_adds_raw_scores(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["spanish_retrieval_parity", "--verbose"])
        assert main() == 0
        assert "es_top=" in capsys.readouterr().out

    def test_no_pairs_is_an_error_not_an_empty_pass(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["spanish_retrieval_parity"])
        monkeypatch.setattr("evals.spanish_retrieval_parity.mirror_pairs", lambda: [])
        assert main() == 1
        assert "no Spanish/English mirror pairs" in capsys.readouterr().err


class TestMirrorPairs:
    def test_every_pair_is_spanish_against_a_non_spanish_mirror(self):
        pairs = mirror_pairs()
        assert pairs, "the committed suites must hold Spanish/English mirror pairs"
        for es_case, en_case in pairs:
            assert es_case["language"] == "es"
            assert es_case["suite"] == PARITY_SUITE
            assert en_case.get("language", "en") != "es"

    def test_compare_reports_both_sides_of_a_real_pair(self, retriever):
        es_case, en_case = mirror_pairs()[0]
        row = compare(retriever, es_case, en_case)
        assert row["id"] == es_case["id"] and row["mirror"] == en_case["id"]
        assert 0.0 <= row["evidence"] <= 1.0
