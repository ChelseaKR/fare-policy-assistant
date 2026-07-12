from decimal import Decimal

from assistant import fare_table


def test_v2_agency_has_typed_rider_categories():
    cats = fare_table.load_rider_categories("SBMTD")
    assert "reduced" in cats
    assert "Seniors" in cats["reduced"].name
    assert cats["reduced"].eligibility_url


def test_structured_fares_bind_amount_to_category():
    # The senior/reduced one-way fare is $1.25, bound to the reduced category —
    # the number conv-forged-002 misread as FREE, now source-of-truth typed.
    reduced = [
        f
        for f in fare_table.structured_fares("SBMTD")
        if f.rider_category
        and f.rider_category.id == "reduced"
        and "Cash" in f.product
        and "Waterfront" not in f.product
    ]
    assert reduced and reduced[0].amount == Decimal("1.25")


def test_v1_agency_has_fares_without_categories():
    fares = fare_table.structured_fares("MST")
    assert fares
    assert all(f.rider_category is None for f in fares)
    assert any(f.amount == Decimal("2.00") for f in fares)


def test_unknown_agency_is_empty():
    assert fare_table.structured_fares("Nope") == []
    assert fare_table.load_rider_categories("Nope") == {}
    assert fare_table.render_fare_card("Nope") == ""


def test_render_card_lists_amounts_and_eligibility():
    card = fare_table.render_fare_card("SBMTD")
    assert "Authoritative fares for SBMTD" in card
    assert "$1.25" in card and "$2.50" in card
    assert "Eligibility: https://sbmtd.gov/fares-passes/" in card


def test_main_prints_card(capsys, monkeypatch):
    monkeypatch.setattr(fare_table.sys, "argv", ["fare_table", "SBMTD"])
    assert fare_table.main() == 0
    assert "$1.25" in capsys.readouterr().out


def test_main_no_feed_and_usage(capsys, monkeypatch):
    monkeypatch.setattr(fare_table.sys, "argv", ["fare_table", "Nope"])
    assert fare_table.main() == 1
    monkeypatch.setattr(fare_table.sys, "argv", ["fare_table"])
    assert fare_table.main() == 2
