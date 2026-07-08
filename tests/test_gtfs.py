"""GTFS(-Fares) cross-validation tests (EXP-06).

Covers both fare schemas (v1 fare_attributes.txt, v2 fare_products.txt), the
fetch path against a mocked transport (no real network call), the free-fare
false-positive guard, and the no_feed/no-snapshot coverage cases.
"""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from assistant import config, gtfs
from assistant.ingest import Chunk

_REAL_CLIENT = httpx.Client


def _mock_client(handler):
    return lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)


def _point_config_at(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    monkeypatch.setattr(config, "RAW_DIR", raw)
    monkeypatch.setattr(config, "PROCESSED_DIR", processed)
    monkeypatch.setattr(gtfs, "GTFS_RAW_DIR", raw / "gtfs")
    monkeypatch.setattr(gtfs, "CROSS_CHECK_PATH", processed / "gtfs_cross_check.json")
    return raw, processed


def _build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _chunk(agency: str, text: str, chunk_id="c#0") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id="doc",
        agency=agency,
        agency_full=agency,
        doc_title="Fares",
        url="https://example.org/fares",
        fetch_date="2026-07-01",
        language="en",
        section="Fares",
        text=text,
    )


_V1_ZIP_FILES = {
    "agency.txt": "agency_id,agency_name\n1,MST\n",
    "fare_attributes.txt": (
        "fare_id,price,currency_type,payment_method,transfers,transfer_duration\n"
        "Regular,2.00,USD,0,2,86400\n"
        "Free,0.00,USD,0,0,7200\n"
    ),
    "fare_rules.txt": "fare_id,route_id\nRegular,001\n",
    "stops.txt": "stop_id,stop_name\n1,Main St\n",  # not a fare file; must not be snapshotted
}

_V2_ZIP_FILES = {
    "agency.txt": "agency_id,agency_name\n1,SBMTD\n",
    "fare_products.txt": (
        "fare_product_id,fare_product_name,fare_media_id,amount,currency,rider_category_id\n"
        "standard_cash,Standard One-way Cash Fare,CashFare,2.50,USD,standard\n"
        "reduced_cash,Reduced One-way Cash Fare,CashFare,1.25,USD,reduced\n"
    ),
    "fare_leg_rules.txt": "leg_group_id,network_id,fare_product_id\nr,r,standard_cash\n",
    "rider_categories.txt": "rider_category_id,rider_category_name\nstandard,Standard\n",
}


# ── fetch ────────────────────────────────────────────────────────────────────


def test_fetch_all_snapshots_only_fare_members(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    manifest = {
        "user_agent": "test-agent/0.1",
        "gtfs_feeds": [
            {"agency": "MST", "url": "https://mst.org/google_transit.zip", "fares_version": "v1"}
        ],
    }
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest["gtfs_feeds"])
    monkeypatch.setattr("assistant.gtfs.ingest.load_manifest", lambda: manifest)

    def handler(request):
        return httpx.Response(200, content=_build_zip(_V1_ZIP_FILES))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    gtfs.fetch_all()

    agency_dir = raw / "gtfs" / "MST"
    assert (agency_dir / "fare_attributes.txt").exists()
    assert (agency_dir / "agency.txt").exists()
    assert not (agency_dir / "stops.txt").exists(), "geo files should not be snapshotted"
    meta = json.loads((agency_dir / "meta.json").read_text())
    assert meta["fares_version"] == "v1"
    assert "fare_attributes.txt" in meta["fare_files"]


def test_fetch_all_bad_zip_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    _point_config_at(tmp_path, monkeypatch)
    manifest_feeds = [{"agency": "MST", "url": "https://mst.org/broken.zip"}]
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest_feeds)
    monkeypatch.setattr(
        "assistant.gtfs.ingest.load_manifest", lambda: {"user_agent": "test-agent/0.1"}
    )

    def handler(request):
        return httpx.Response(200, content=b"not a zip")

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    gtfs.fetch_all()  # must not raise

    assert "FAIL" in capsys.readouterr().err


def test_fetch_all_only_filter(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    feeds = [
        {"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"},
        {"agency": "SBMTD", "url": "https://sbmtd.gov/g.zip", "fares_version": "v2"},
    ]
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: feeds)
    monkeypatch.setattr(
        "assistant.gtfs.ingest.load_manifest", lambda: {"user_agent": "test-agent/0.1"}
    )

    def handler(request):
        files = _V1_ZIP_FILES if "mst" in str(request.url) else _V2_ZIP_FILES
        return httpx.Response(200, content=_build_zip(files))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    gtfs.fetch_all(only={"MST"})

    assert (raw / "gtfs" / "MST").exists()
    assert not (raw / "gtfs" / "SBMTD").exists()


# ── parse ────────────────────────────────────────────────────────────────────


def test_parse_fares_v1(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text(_V1_ZIP_FILES["fare_attributes.txt"])

    fares = gtfs.parse_fares("MST")

    by_id = {f.fare_id: f for f in fares}
    assert by_id["Regular"].amount == pytest.approx(2.00)
    assert by_id["Free"].amount == pytest.approx(0.00)


def test_parse_fares_v2(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "SBMTD"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_products.txt").write_text(_V2_ZIP_FILES["fare_products.txt"])

    fares = gtfs.parse_fares("SBMTD")

    by_id = {f.fare_id: f for f in fares}
    assert by_id["standard_cash"].amount == pytest.approx(2.50)
    assert by_id["standard_cash"].rider_category == "standard"


def test_parse_fares_no_snapshot_returns_empty(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    assert gtfs.parse_fares("Yolobus") == []


# ── prose extraction ─────────────────────────────────────────────────────────


def test_prose_fare_amounts_scoped_to_agency():
    chunks = [
        _chunk("MST", "Regular fare is $2.00 per ride."),
        _chunk("SBMTD", "Standard fare is $2.50 per ride.", chunk_id="c#1"),
    ]
    assert gtfs.prose_fare_amounts("MST", chunks) == {gtfs.Decimal("2.00")}
    assert gtfs.prose_fare_amounts("SBMTD", chunks) == {gtfs.Decimal("2.50")}


# ── cross-check ──────────────────────────────────────────────────────────────


def test_cross_check_agrees_when_feed_amount_in_prose(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "Regular Fixed Route fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert records == [gtfs.CrossCheckRecord("MST", "Regular", "Regular", "2.00", "yes")]


def test_cross_check_flags_disagreement_when_feed_amount_absent_from_prose(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,3.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    # Prose still says the old $2.00 — this is the wrong-fare liability scenario.
    chunks = [_chunk("MST", "Regular Fixed Route fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert records[0].feed_agrees == "no"
    assert records[0].feed_amount == "3.00"


def test_cross_check_zero_fare_agrees_when_prose_says_free(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nFree,0.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    # Prose spells a free fare as a word, never "$0.00" — the guard this tests.
    chunks = [_chunk("MST", "Children ride FREE with an adult.")]

    records = gtfs.cross_check(chunks)

    assert records[0].feed_agrees == "yes"


def test_cross_check_zero_fare_flags_when_prose_never_says_free(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nFree,0.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "Regular fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert any(r.feed_agrees == "no" for r in records)


def test_cross_check_no_feed_when_snapshot_missing(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "Regular fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert records == [
        gtfs.CrossCheckRecord("MST", "(no snapshot)", "(no snapshot)", None, "no_feed")
    ]


def test_cross_check_no_feed_configured_for_corpus_agency(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: [])
    chunks = [_chunk("Yolobus", "Regular fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert records == [
        gtfs.CrossCheckRecord(
            "Yolobus", "(no feed configured)", "(no feed configured)", None, "no_feed"
        )
    ]


# ── report + CLI ─────────────────────────────────────────────────────────────


def test_write_report_shape(tmp_path, monkeypatch):
    _, processed = _point_config_at(tmp_path, monkeypatch)
    records = [gtfs.CrossCheckRecord("MST", "Regular", "Regular", "2.00", "yes")]

    gtfs.write_report(records)

    payload = json.loads((processed / "gtfs_cross_check.json").read_text())
    assert payload["records"] == [
        {
            "agency": "MST",
            "fare_id": "Regular",
            "name": "Regular",
            "feed_amount": "2.00",
            "feed_agrees": "yes",
        }
    ]
    assert "generated" in payload


def test_main_check_dispatch(tmp_path, monkeypatch):
    _, processed = _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: [])
    monkeypatch.setattr("assistant.gtfs.ingest.load_chunks", lambda: [])
    monkeypatch.setattr("sys.argv", ["gtfs", "check"])

    gtfs.main()

    assert (processed / "gtfs_cross_check.json").exists()


def test_main_unknown_command_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["gtfs", "bogus"])
    with pytest.raises(SystemExit):
        gtfs.main()
