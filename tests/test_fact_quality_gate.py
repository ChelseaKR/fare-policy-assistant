"""The fare-fact corpus gate must be able to fail, and the committed corpus
must pass it.

A gate over a data file is worth exactly as much as its ability to go red, so
each property below is proved by planting the defect it exists to catch. The
committed-corpus assertions are separate from the planted ones on purpose: the
first says the repository is clean today, the others say the gate would notice
if it stopped being.
"""

import json

import pytest

from assistant import config, ingest
from assistant.facts import label_defect, load_facts, refusal_reason
from tools import check_fact_quality


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A throwaway corpus directory the gate reads instead of the real one."""
    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(config, "FACTS_PATH", processed / "facts.jsonl")
    monkeypatch.setattr(config, "FACT_QUALITY_PIN_PATH", tmp_path / "fact-quality-pin.json")
    return tmp_path


def _chunk():
    return ingest.Chunk(
        chunk_id="mst-fares#0",
        doc_id="mst-fares",
        agency="MST",
        agency_full="Monterey-Salinas Transit",
        doc_title="Fares",
        url="https://mst.org/fares/",
        fetch_date="2026-06-12",
        language="en",
        section="Fares",
        # One clean pairing and one prose fragment, so the run produces both a
        # published row and a refused one.
        text="Monthly GoPass (31 Days)\n$70.00\nCapped at $20.00 per week, or\n",
    )


def _row(**overrides):
    row = {
        "agency": "MST",
        "doc_id": "mst-fares",
        "chunk_id": "mst-fares#1",
        "program": "Monthly GoPass (31 Days)",
        "rider_class": "Regular Fixed Route",
        "price": 70.0,
        "currency": "USD",
        "age_min": None,
        "age_max": None,
        "confidence": "parsed",
    }
    row.update(overrides)
    return row


def _write(corpus, facts, refusals=(), ceiling=0):
    config.FACTS_PATH.write_text("".join(json.dumps(f) + "\n" for f in facts), encoding="utf-8")
    config.facts_refused_path().write_text(
        "".join(json.dumps(r) + "\n" for r in refusals), encoding="utf-8"
    )
    config.FACT_QUALITY_PIN_PATH.write_text(
        json.dumps({"max_refused_rows": ceiling}), encoding="utf-8"
    )


class TestTheCommittedCorpus:
    def test_no_published_row_violates_the_label_contract(self):
        offenders = [
            (f.agency, f.chunk_id, f.price, f.program, f.rider_class)
            for f in load_facts(config.FACTS_PATH)
            if refusal_reason(f) is not None
        ]
        assert offenders == []

    def test_the_refusal_record_exists(self):
        # A silent drop is the same defect as publishing the garbage.
        assert config.facts_refused_path().exists()

    def test_the_spanish_page_states_the_regular_monthly_fare_it_publishes(self):
        # The defect this gate was written for: mst-fares-es asserted a $35
        # regular monthly fare, which is the *discount* price. $70 is regular.
        rows = [f for f in load_facts(config.FACTS_PATH) if f.doc_id == "mst-fares-es"]
        assert rows, "the Spanish MST page must still produce fare facts"
        regular = {f.price for f in rows if "regular" in f.rider_class.casefold()}
        assert 70.0 in regular
        assert 35.0 not in regular

    def test_the_gate_passes_on_the_committed_corpus(self, capsys):
        assert check_fact_quality.main([]) == 0


class TestTheRefusalRecordFollowsTheFactTable:
    def test_redirecting_the_fact_table_redirects_the_refusals(self, tmp_path, monkeypatch):
        # Bound as its own module constant, the refusal path stayed pointed at
        # the repository from every test that redirected FACTS_PATH at a
        # tmpdir. Measured: one such test emptied the committed
        # corpus/processed/facts_refused.jsonl on a full-suite run.
        monkeypatch.setattr(config, "FACTS_PATH", tmp_path / "facts.jsonl")
        assert config.facts_refused_path() == tmp_path / "facts_refused.jsonl"

    def test_ingest_writes_the_refusals_beside_the_fact_table_it_was_given(
        self, tmp_path, monkeypatch
    ):
        processed = tmp_path / "processed"
        processed.mkdir()
        monkeypatch.setattr(config, "FACTS_PATH", processed / "facts.jsonl")
        monkeypatch.setattr(ingest, "load_chunks", lambda: [_chunk()])
        ingest.build_facts()
        assert (processed / "facts_refused.jsonl").exists()
        assert config.REPO_ROOT not in (processed / "facts_refused.jsonl").parents


class TestTheGateCanFail:
    def test_a_prose_program_fails_the_gate(self, corpus, capsys):
        _write(corpus, [_row(program="per week, or")])
        assert check_fact_quality.main([]) == 1
        assert "label contract" in capsys.readouterr().err

    def test_a_bare_decimal_fragment_fails_the_gate(self, corpus, capsys):
        _write(corpus, [_row(program=",00", rider_class="", price=35.0)])
        assert check_fact_quality.main([]) == 1
        assert "decimal_fragment" in capsys.readouterr().err

    def test_a_price_with_no_label_fails_the_gate(self, corpus, capsys):
        _write(corpus, [_row(program="", rider_class="", price=1.75)])
        assert check_fact_quality.main([]) == 1
        assert "price_without_label" in capsys.readouterr().err

    def test_exceeding_the_refusal_ceiling_fails_the_gate(self, corpus, capsys):
        refusal = {
            "agency": "MST",
            "doc_id": "mst-fares",
            "chunk_id": "mst-fares#1",
            "reason": "program_sentence_length",
            "program": "x" * 200,
            "rider_class": "",
            "price": 1.0,
        }
        _write(corpus, [], refusals=[refusal, refusal], ceiling=1)
        assert check_fact_quality.main([]) == 1
        assert "exceeds the pinned ceiling" in capsys.readouterr().err

    def test_a_missing_refusal_record_fails_the_gate(self, corpus, capsys):
        _write(corpus, [_row()])
        config.facts_refused_path().unlink()
        assert check_fact_quality.main([]) == 1
        assert "must record them" in capsys.readouterr().err

    def test_a_clean_table_under_its_ceiling_passes(self, corpus, monkeypatch):
        # The gate's reproduction check needs the real chunk index; this
        # fixture has none, so it is stubbed to isolate the two properties
        # above from it. Its own failure mode is covered by
        # `test_a_table_that_is_not_derivable_from_the_chunks_fails_the_gate`.
        monkeypatch.setattr(check_fact_quality, "_reproduction_drift", lambda _: [])
        _write(corpus, [_row()], ceiling=0)
        assert check_fact_quality.main([]) == 0

    def test_a_table_that_is_not_derivable_from_the_chunks_fails_the_gate(
        self, corpus, monkeypatch, capsys
    ):
        # A gate over a file nobody regenerates measures the file, not the
        # parser: a hand-edited facts.jsonl would sail through every other
        # check here.
        monkeypatch.setattr(check_fact_quality, "_violations", lambda _: [])
        _write(corpus, [_row(program="Hand-typed Pass", price=999.0)], ceiling=0)
        assert check_fact_quality.main([]) == 1
        assert "is not what the extractor derives" in capsys.readouterr().err


class TestLabelDefectVocabulary:
    @pytest.mark.parametrize(
        ("label", "reason"),
        [
            (",00", "decimal_fragment"),
            (") and checks (credit cards are not accepted)", "fragment_start"),
            ("X", "too_short"),
            ("(unspecified)", "unlabelled_price"),
            ("per unit annually or", "dangling_word"),
            ("for adults, youth, & Clipper START/", "dangling_punctuation"),
            ("per week, or another period entirely", "mid_sentence"),
            (
                "Good on all RTA and South County routes for 7 consecutive days.",
                "complete_sentence",
            ),
            ("a" * 81, "sentence_length"),
        ],
    )
    def test_each_shape_has_its_own_reason(self, label, reason):
        assert label_defect(label) == reason

    def test_an_empty_label_is_not_itself_a_defect(self):
        # A row may legitimately name only one of the two axes; whether that
        # is enough is `refusal_reason`'s call, not the label checker's.
        assert label_defect("") is None
