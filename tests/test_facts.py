"""Tests for the structured fare-fact extractor (EXP-01).

Layouts mirror the real corpus shapes so a regression here means a real
document would misparse, not just a synthetic fixture.
"""

from assistant.facts import (
    FareFact,
    extract_chunk_candidates,
    extract_chunk_facts,
    extract_chunk_rows,
    label_defect,
    load_facts,
    load_refusals,
    merge_manual_rows,
    parse_age_claims,
    parse_price_claims,
    refusal_reason,
    write_facts,
    write_refusals,
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

    def test_ignores_large_financial_context_as_non_fare(self):
        assert parse_price_claims("The agency lost $3.0 million, but the fare is $2.50.") == [2.50]

    def test_parses_age_plus_and_and_older_phrasings(self):
        claims = parse_age_claims("Seniors (age 65+) and riders 62 years and older both qualify.")
        assert (65, None) in claims
        assert (62, None) in claims


class TestSpanishDecimalComma:
    """MST's Spanish page writes "$ 35,00", and until 2026-09-07 the money
    pattern stopped at the comma: it read `$ 35`, then labelled the *next*
    row with the orphan `,00`. On mst-fares-es that published a regular
    monthly fare of $35 when the regular monthly fare is $70 — the discount
    price presented as the regular one."""

    TEXT = (
        "Viaje único 2 horas Efectivo o Tarjeta Go\n"
        "GoPass mensual (31 días)\n"
        "Regular Ruta fija\n"
        "$ 2.00\n"
        "$ 70,00\n"
        "Descuento Ruta fija\n"
        "$ 1,00\n"
        "$ 35,00\n"
    )

    def test_a_decimal_comma_is_a_decimal_point(self):
        rows = extract_chunk_facts("MST", "mst-fares-es", "mst-fares-es#1", self.TEXT)
        monthly = {r.rider_class: r.price for r in rows if r.program == "GoPass mensual (31 días)"}
        assert monthly["Regular Ruta fija"] == 70.00
        assert monthly["Descuento Ruta fija"] == 35.00

    def test_no_row_is_labelled_with_the_orphaned_decimal_tail(self):
        # Prose, deliberately: in the grid above every amount is claimed by a
        # structured pass, so the fallback never sees one and this assertion
        # cannot fail there however the money pattern is written. Here the
        # fallback is the only thing that labels the amount, which is the
        # shape the real page produced -- the pre-fix pattern ended the match
        # at "$ 70" and the label began ",00 por mes".
        text = "Nunca se le cobrara mas de $ 70,00 por mes en el sistema.\n"
        candidates = extract_chunk_candidates("MST", "mst-fares-es", "mst-fares-es#0", text)
        assert candidates, "the fallback must still see the amount"
        assert not [r for r in candidates if r.program.lstrip().startswith(",")]

    def test_the_grid_itself_carries_no_orphaned_decimal_tail(self):
        rows = extract_chunk_facts("MST", "mst-fares-es", "mst-fares-es#1", self.TEXT)
        assert not [r for r in rows if r.program.startswith(",")]

    def test_the_regular_monthly_fare_is_never_the_discount_price(self):
        rows = extract_chunk_facts("MST", "mst-fares-es", "mst-fares-es#1", self.TEXT)
        regular = {r.price for r in rows if "regular" in r.rider_class.lower()}
        assert 35.00 not in regular

    def test_a_thousands_separator_is_not_a_decimal_comma(self):
        # E-tran's senior-pass page says the program runs "until the $100,000
        # program fund is fully expended". Read as `$100` it was a fare.
        assert parse_price_claims("until the $100,000 program fund is expended") == [100000.0]
        assert parse_price_claims("a $1,234.50 charge") == [1234.50]

    def test_english_decimal_prices_are_unchanged(self):
        assert parse_price_claims("$2.50, $0.25 and $125.00") == [2.50, 0.25, 125.00]


class TestPublicationContract:
    """A price must arrive attached to a label that is a label."""

    def test_prose_fragment_program_is_refused_not_published(self):
        # SBMTD's fare-capping paragraph: the fallback labelled $1.00 with the
        # sentence that followed it.
        text = (
            "$1.00 over the dollar value of pass activations needed to be fare "
            "capped, the passenger will be refunded the difference.\n"
        )
        published, refused = extract_chunk_rows("SBMTD", "sbmtd-farechange", "s#1", text)
        assert not published
        assert [r.reason for r in refused] == ["program_sentence_length"]
        assert refused[0].price == 1.00

    def test_dangling_conjunction_program_is_refused(self):
        text = "Group rates are $20.00 per week, or $70.00 per month.\n"
        published, refused = extract_chunk_rows("MST", "mst-fares", "m#1", text)
        assert not published
        assert "program_dangling_word" in {r.reason for r in refused}
        assert {r.price for r in refused} == {20.00, 70.00}

    def test_bare_decimal_fragment_program_is_refused(self):
        assert label_defect(",00") == "decimal_fragment"
        assert label_defect(".50") == "decimal_fragment"

    def test_a_price_with_no_label_at_all_is_refused(self):
        text = "$1.75\n"
        published, refused = extract_chunk_rows("SBMTD", "sbmtd-fares-passes", "s#1", text)
        assert not published
        assert [r.reason for r in refused] == ["program_unlabelled_price"]

    def test_a_single_character_table_sentinel_is_not_a_program(self):
        assert label_defect("X") == "too_short"

    def test_a_real_program_label_is_published(self):
        for label in (
            "Monthly GoPass (31 Days)",
            "Super Senior Monthly Pass/Sticker (age 75+)",
            "20 Single Ride Prepaid Cards (Reduced Fare)",
            "SolanoExpress Within Solano County",
            "Full – Ages 18 to 59.",
        ):
            assert label_defect(label) is None, label

    def test_refusals_are_recorded_rather_than_dropped(self, tmp_path):
        # A silent drop is the same defect wearing the other mask: the corpus
        # would read as if the page held nothing the parser mishandled.
        text = "Day Pass\n$6.00\nfor a $1.75 surcharge, or\n"
        published, refused = extract_chunk_rows("SBMTD", "sbmtd-fares-passes", "s#1", text)
        assert [r.program for r in published] == ["Day Pass"]
        assert refused and all(r.reason for r in refused)
        path = tmp_path / "facts_refused.jsonl"
        write_refusals(refused, path)
        assert load_refusals(path) == refused

    def test_manual_rows_are_never_second_guessed(self):
        manual = FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#0",
            program="per week, or",
            rider_class="",
            price=20.0,
            currency="USD",
            age_min=None,
            age_max=None,
            confidence="manual",
        )
        assert refusal_reason(manual) is None


class TestAxisOrientation:
    """VINE publishes its passes rider-class-down, program-across."""

    TEXT = (
        "Day Pass* | 20-Ride Pass** | 31-Day Pass* | BART 31-Day Pass***\n"
        "Adult (19-64) | $7.00 | $30.00 | $55.00 | $125.00\n"
        "Half | $3.50 | $15.00 | $27.50 | $125.00\n"
    )

    def test_the_program_column_holds_the_program(self):
        rows = extract_chunk_facts("VINE", "vine-fares", "vine-fares#1", self.TEXT)
        adult = {r.program: r.price for r in rows if r.rider_class == "Adult (19-64)"}
        assert adult["Day Pass"] == 7.00
        assert adult["31-Day Pass"] == 55.00

    def test_every_row_of_one_table_reads_the_same_way(self):
        # "Half" is not a word this module recognises as a rider class.
        # Deciding orientation per row left it transposed relative to the row
        # directly above it, in the same table.
        rows = extract_chunk_facts("VINE", "vine-fares", "vine-fares#1", self.TEXT)
        assert {r.rider_class for r in rows} == {"Adult (19-64)", "Half"}
        assert {r.program for r in rows} == {
            "Day Pass",
            "20-Ride Pass",
            "31-Day Pass",
            "BART 31-Day Pass",
        }

    def test_a_bare_rider_class_row_does_not_become_a_program(self):
        rows = extract_chunk_facts("VINE", "vine-fares", "vine-fares#0", "Adult (19-64) | $2.00\n")
        assert [(r.program, r.rider_class, r.price) for r in rows] == [("", "Adult (19-64)", 2.00)]

    def test_a_symmetric_matrix_is_refused_on_both_axes(self):
        # VineGo's paratransit fares are origin city down, destination city
        # across. Neither axis is a program and neither is a rider class.
        text = "Calistoga | St. Helena | Napa\nNapa | $4.00 | $4.00 | $4.00\n"
        published, refused = extract_chunk_rows("VINE", "vine-go", "vine-go#1", text)
        assert not published
        assert {r.reason for r in refused} == {"matrix_axis_is_not_a_program"}

    def test_a_refused_price_does_not_return_through_the_prose_fallback(self):
        text = "Calistoga | St. Helena | Napa\nNapa | $4.00 | $4.00 | $4.00\n"
        _, refused = extract_chunk_rows("VINE", "vine-go", "vine-go#1", text)
        assert all(r.reason == "matrix_axis_is_not_a_program" for r in refused)


class TestAgeOnlyPairing:
    def test_each_rider_class_takes_the_age_bound_in_its_own_segment(self):
        # CCCTA's line published "youth means 65+": the first class keyword on
        # the line was paired with the first age bound on the line, across the
        # class that actually owned it.
        text = "Clipper START/Youth (6-18)/ Senior (65+)/Disabled (RTC)\n"
        rows = extract_chunk_facts("CCCTA", "cccta-fare-types-prices", "c#1", text)
        bounds = {r.rider_class: (r.age_min, r.age_max) for r in rows}
        assert bounds["youth"] == (6, 18)
        assert bounds["senior"] == (65, None)

    def test_a_class_whose_segment_states_no_age_yields_no_row(self):
        text = "Seniors must be age 65 or older and Youth must be age 5-18.\n"
        rows = extract_chunk_facts("VTA", "vta-fares", "v#1", text)
        assert [(r.rider_class, r.age_min, r.age_max) for r in rows] == [("youth", 5, 18)]
