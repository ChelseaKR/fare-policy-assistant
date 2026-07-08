"""Tests for the structured fare-fact extractor (EXP-01).

Layouts mirror the real corpus shapes so a regression here means a real
document would misparse, not just a synthetic fixture.
"""

from assistant.facts import (
    FareFact,
    extract_chunk_facts,
    load_facts,
    merge_manual_rows,
    parse_age_claims,
    parse_price_claims,
    write_facts,
)


def _by_program(rows: list[FareFact]) -> dict[str, FareFact]:
    return {r.program: r for r in rows}


class TestSequentialLabelPriceBlocks:
    """SacRT-style: a rider-class header, then repeating label/price lines."""

    TEXT = (
        "All fares are subject to change.\n"
        "Age 19-61 - Basic\n"
        "Single Ride Ticket\n"
        "$2.50\n"
        "Transfer Ticket\n"
        "$0.25\n"
        "Senior (age 62+) - Discount\n"
        "Single\n"
        "$1.25\n"
    )

    def test_prices_attach_to_the_correct_program(self):
        rows = _by_program(extract_chunk_facts("SacRT", "sacrt-fares", "sacrt-fares#1", self.TEXT))
        assert rows["Single Ride Ticket"].price == 2.50
        assert rows["Transfer Ticket"].price == 0.25
        assert rows["Single"].price == 1.25

    def test_rider_class_and_age_propagate_from_the_header(self):
        rows = _by_program(extract_chunk_facts("SacRT", "sacrt-fares", "sacrt-fares#1", self.TEXT))
        basic = rows["Single Ride Ticket"]
        assert basic.age_min == 19 and basic.age_max == 61
        senior = rows["Single"]
        assert senior.age_min == 62 and senior.age_max is None
        assert "Discount" in senior.rider_class

    def test_no_leading_header_still_pairs_label_with_its_own_price(self):
        # A section whose *heading* names the rider class (not a body line)
        # has no in-body header before its label/price pairs. This is the bug
        # that used to shift every price onto its neighboring label.
        text = "Single Ride Ticket\n$1.25\nTransfer Ticket\n$0.25\nDaily Pass\n$3.50\n"
        rows = _by_program(extract_chunk_facts("SacRT", "sacrt-fares", "sacrt-fares#2", text))
        assert rows["Single Ride Ticket"].price == 1.25
        assert rows["Transfer Ticket"].price == 0.25
        assert rows["Daily Pass"].price == 3.50


class TestGridLayout:
    """MST-style: N program labels, then a rider-class header, then N prices
    in the same order, repeated for a second rider class."""

    TEXT = (
        "Single Ride 2 hours\n"
        "Daily GoPass valid until 2:00 AM\n"
        "Weekly GoPass (7 Days)\n"
        "Regular Fixed Route\n"
        "$2.00\n"
        "$6.00\n"
        "$20.00\n"
        "Discount Fixed Route\n"
        "$1.00\n"
        "$3.00\n"
        "$10.00\n"
    )

    def test_each_price_zips_to_its_own_program_in_order(self):
        rows = extract_chunk_facts("MST", "mst-fares", "mst-fares#1", self.TEXT)
        regular = {r.program: r.price for r in rows if r.rider_class == "Regular Fixed Route"}
        assert regular["Single Ride 2 hours"] == 2.00
        assert regular["Daily GoPass valid until 2:00 AM"] == 6.00
        assert regular["Weekly GoPass (7 Days)"] == 20.00

    def test_second_rider_class_reuses_the_same_program_order(self):
        rows = extract_chunk_facts("MST", "mst-fares", "mst-fares#1", self.TEXT)
        discount = {r.program: r.price for r in rows if r.rider_class == "Discount Fixed Route"}
        assert discount["Single Ride 2 hours"] == 1.00
        assert discount["Weekly GoPass (7 Days)"] == 10.00


class TestPipeTable:
    TEXT = (
        "Regular Adult (19-61) | Senior/Disabled (62+)\n"
        "Single Ride Tickets\n"
        "Local Fare | $2.00 | $1.00\n"
    )

    def test_each_column_scoped_to_its_header(self):
        rows = extract_chunk_facts("Yolobus", "yolobus-fares", "yolobus-fares#1", self.TEXT)
        by_class = {r.rider_class: r.price for r in rows if r.program == "Local Fare"}
        assert by_class["Regular Adult (19-61)"] == 2.00
        assert by_class["Senior/Disabled (62+)"] == 1.00

    def test_age_hint_on_header_column_propagates(self):
        rows = extract_chunk_facts("Yolobus", "yolobus-fares", "yolobus-fares#1", self.TEXT)
        regular = next(r for r in rows if r.rider_class == "Regular Adult (19-61)")
        assert regular.age_min == 19 and regular.age_max == 61

    def test_mismatched_column_count_falls_back_to_unscoped(self):
        # HTA's "Cash | Tap-to-Pay" header (2 cols) over a 1-price data row
        # ("Single Ride | $2.00") must not guess which column the price is.
        text = "Cash | Tap-to-Pay\nSingle Ride | $2.00\n"
        rows = extract_chunk_facts("HTA", "hta-fares", "hta-fares#1", text)
        assert rows[0].price == 2.00
        assert rows[0].rider_class == ""


class TestInlinePriceRuns:
    def test_sbmtd_prose_style_captures_price_and_rider_class_keyword(self):
        text = "$2.50 Regular one-way\n$1.25 Seniors (age 65+) Persons with Disabilities\n"
        rows = extract_chunk_facts("SBMTD", "sbmtd-fares-passes", "sbmtd-fares-passes#1", text)
        senior_row = next(r for r in rows if r.price == 1.25)
        assert senior_row.rider_class == "seniors"
        assert senior_row.age_min == 65

    def test_budget_narrative_million_figures_are_not_captured_as_prices(self):
        # sbmtd-farechange's fare-equity narrative: "$3.0 million per year" is
        # a budget figure, not a fare -- must not become a $3.00 fact.
        text = "the District lost approximately $3.0 million per year in funding.\n"
        rows = extract_chunk_facts("SBMTD", "sbmtd-farechange", "sbmtd-farechange#1", text)
        assert all(r.price != 3.0 for r in rows)

    def test_structured_pass_takes_priority_over_fallback(self):
        # A price already captured by a structured pass must not also appear
        # as a second, worse-labeled fallback row for the same chunk.
        text = "Single Ride Ticket\n$2.50\n"
        rows = extract_chunk_facts("SacRT", "sacrt-fares", "sacrt-fares#1", text)
        assert len(rows) == 1
        assert rows[0].program == "Single Ride Ticket"


class TestAgeOnlyFacts:
    def test_age_eligibility_line_with_no_price_is_captured(self):
        text = "65 years and older (see also: Benefits)\n18 years and under\n"
        rows = extract_chunk_facts("MST", "mst-fares", "mst-fares#5", text)
        ages = {(r.age_min, r.age_max) for r in rows}
        assert (65, None) in ages
        assert (None, 18) in ages


class TestPersistence:
    def test_write_then_load_roundtrips(self, tmp_path):
        rows = [
            FareFact(
                agency="MST",
                doc_id="mst-fares",
                chunk_id="mst-fares#0",
                program="Single Ride",
                rider_class="Regular",
                price=2.0,
                currency="USD",
                age_min=None,
                age_max=None,
                confidence="parsed",
            )
        ]
        path = tmp_path / "facts.jsonl"
        write_facts(rows, path)
        assert load_facts(path) == rows

    def test_manual_rows_survive_a_rebuild(self, tmp_path):
        path = tmp_path / "facts.jsonl"
        manual = FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#0",
            program="Hand-verified courtesy card fee",
            rider_class="",
            price=0.0,
            currency="USD",
            age_min=None,
            age_max=None,
            confidence="manual",
        )
        write_facts([manual], path)
        new_parsed = [
            FareFact(
                agency="MST",
                doc_id="mst-fares",
                chunk_id="mst-fares#1",
                program="Single Ride",
                rider_class="",
                price=2.0,
                currency="USD",
                age_min=None,
                age_max=None,
                confidence="parsed",
            )
        ]
        merged = merge_manual_rows(new_parsed, path)
        assert manual in merged
        assert new_parsed[0] in merged

    def test_rebuild_drops_stale_parsed_rows_not_reproduced_this_run(self, tmp_path):
        # merge_manual_rows only carries "manual" rows forward; a "parsed" row
        # from a previous run that the extractor no longer produces (the
        # source page changed) must not linger.
        path = tmp_path / "facts.jsonl"
        stale = FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#0",
            program="Retired Program",
            rider_class="",
            price=9.99,
            currency="USD",
            age_min=None,
            age_max=None,
            confidence="parsed",
        )
        write_facts([stale], path)
        merged = merge_manual_rows([], path)
        assert stale not in merged


class TestAnswerClaimParsing:
    def test_parses_multiple_prices(self):
        assert parse_price_claims("It's $2.00 regular or $1.00 discount.") == [2.00, 1.00]

    def test_parses_age_plus_and_and_older_phrasings(self):
        claims = parse_age_claims("Seniors (age 65+) and riders 62 years and older both qualify.")
        assert (65, None) in claims
        assert (62, None) in claims
