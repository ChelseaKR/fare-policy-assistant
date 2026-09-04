"""GTFS(-Fares) cross-validation tests (EXP-06).

Covers both fare schemas (v1 fare_attributes.txt, v2 fare_products.txt), the
fetch path against a mocked transport (no real network call), the free-fare
false-positive guard, and the no_feed/no-snapshot coverage cases.
"""

from __future__ import annotations

import hashlib
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


def test_fetch_all_retains_exact_zip_receipt_and_only_consumed_fare_files(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    manifest = {
        "user_agent": "test-agent/0.1",
        "gtfs_feeds": [
            {"agency": "MST", "url": "https://mst.org/google_transit.zip", "fares_version": "v1"}
        ],
    }
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest["gtfs_feeds"])
    monkeypatch.setattr("assistant.gtfs.ingest.load_manifest", lambda: manifest)

    feed_zip = _build_zip(_V1_ZIP_FILES)

    def handler(request):
        return httpx.Response(200, content=feed_zip)

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    assert gtfs.fetch_all() is True

    selected = gtfs.load_current_snapshot_set()
    assert selected is not None
    agency_dir = selected["MST"].directory
    assert selected["MST"].fares_schema == "v1"
    assert selected["MST"].http_status == 200
    assert selected["MST"].requested_url == "https://mst.org/google_transit.zip"
    assert (agency_dir / "feed.zip").read_bytes() == feed_zip
    assert (agency_dir / "fare_attributes.txt").exists()
    assert not (agency_dir / "agency.txt").exists()
    assert not (agency_dir / "fare_rules.txt").exists()
    assert not (agency_dir / "stops.txt").exists(), "geo files should not be snapshotted"
    receipt_bytes = (agency_dir / "receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt_bytes == gtfs._canonical_json(receipt)
    assert receipt["schema"] == gtfs.GTFS_RECEIPT_SCHEMA
    assert receipt["fares_schema"] == "v1"
    assert receipt["requested_url"] == "https://mst.org/google_transit.zip"
    assert receipt["final_url"] == "https://mst.org/google_transit.zip"
    assert receipt["http_status"] == 200
    assert receipt["zip"] == {
        "bytes": len(feed_zip),
        "sha256": hashlib.sha256(feed_zip).hexdigest(),
    }
    assert [row["name"] for row in receipt["extracted_files"]] == ["fare_attributes.txt"]
    assert agency_dir.name == hashlib.sha256(receipt_bytes).hexdigest()
    current_bytes = (raw / "gtfs" / "current.json").read_bytes()
    assert current_bytes == gtfs._canonical_json(json.loads(current_bytes))
    current = json.loads(current_bytes)
    assert gtfs.current_snapshot_set_version() == current["set_version"]
    assert len(current["set_version"]) == 64
    fares = gtfs.parse_fares("MST")
    assert fares and all(fare.agency == "MST" for fare in fares)


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

    assert gtfs.fetch_all() is False  # must not raise

    assert "FAIL" in capsys.readouterr().err
    assert not (gtfs.GTFS_RAW_DIR / "current.json").exists()


def test_fetch_all_only_filter_atomically_replaces_one_member_of_existing_set(
    tmp_path, monkeypatch
):
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

    assert gtfs.fetch_all() is True
    first = gtfs.load_current_snapshot_set()
    assert first is not None
    sbmtd_version = first["SBMTD"].snapshot_version

    assert gtfs.fetch_all(only={"MST"}) is True
    second = gtfs.load_current_snapshot_set()
    assert second is not None

    assert set(second) == {"MST", "SBMTD"}
    assert second["SBMTD"].snapshot_version == sbmtd_version
    assert not (raw / "gtfs" / "MST").exists()
    assert not (raw / "gtfs" / "SBMTD").exists()


def test_partial_multi_feed_failure_preserves_exact_current_set(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    feeds = [
        {"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"},
        {"agency": "SBMTD", "url": "https://sbmtd.gov/g.zip", "fares_version": "v2"},
    ]
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: feeds)
    monkeypatch.setattr(
        "assistant.gtfs.ingest.load_manifest",
        lambda: {"user_agent": "test-agent/0.1"},
    )
    failing = False
    changed_v1 = dict(_V1_ZIP_FILES)
    changed_v1["fare_attributes.txt"] = changed_v1["fare_attributes.txt"].replace("2.00", "3.00")

    def handler(request):
        if "mst" in str(request.url):
            files = changed_v1 if failing else _V1_ZIP_FILES
            return httpx.Response(200, content=_build_zip(files))
        if failing:
            return httpx.Response(200, content=b"truncated-not-a-zip")
        return httpx.Response(200, content=_build_zip(_V2_ZIP_FILES))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))
    assert gtfs.fetch_all() is True
    current_path = raw / "gtfs" / "current.json"
    before_pointer = current_path.read_bytes()
    before = gtfs.load_current_snapshot_set()
    assert before is not None
    before_versions = {agency: item.snapshot_version for agency, item in before.items()}
    before_snapshots = sorted(
        str(path.relative_to(raw / "gtfs")) for path in (raw / "gtfs" / "snapshots").glob("*/*")
    )

    failing = True
    assert gtfs.fetch_all() is False

    assert current_path.read_bytes() == before_pointer
    after = gtfs.load_current_snapshot_set()
    assert after is not None
    assert {agency: item.snapshot_version for agency, item in after.items()} == before_versions
    assert (
        sorted(
            str(path.relative_to(raw / "gtfs")) for path in (raw / "gtfs" / "snapshots").glob("*/*")
        )
        == before_snapshots
    )
    assert not list((raw / "gtfs").glob(".transaction.*"))


def test_pointer_write_failure_preserves_previous_selection(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    manifest = {
        "user_agent": "test-agent/0.1",
        "gtfs_feeds": [{"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"}],
    }
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest["gtfs_feeds"])
    monkeypatch.setattr("assistant.gtfs.ingest.load_manifest", lambda: manifest)
    changed = False
    changed_files = dict(_V1_ZIP_FILES)
    changed_files["fare_attributes.txt"] = changed_files["fare_attributes.txt"].replace(
        "2.00", "4.00"
    )

    def handler(request):
        files = changed_files if changed else _V1_ZIP_FILES
        return httpx.Response(200, content=_build_zip(files))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))
    assert gtfs.fetch_all() is True
    current = raw / "gtfs" / "current.json"
    before = current.read_bytes()

    changed = True

    def fail_before_replace(root, payload):
        raise OSError("injected pointer write failure")

    monkeypatch.setattr(gtfs, "_atomic_write_current", fail_before_replace)
    assert gtfs.fetch_all() is False

    assert current.read_bytes() == before
    selected = gtfs.load_current_snapshot_set()
    assert selected is not None
    assert gtfs.parse_fares("MST")[0].amount == pytest.approx(2.00)


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../outside.txt",
        "/absolute.txt",
        "nested\\windows.txt",
        "nested/../../escape.txt",
    ],
)
def test_malicious_zip_member_aborts_transaction_without_writing_outside(
    malicious_name, tmp_path, monkeypatch
):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    manifest = {
        "user_agent": "test-agent/0.1",
        "gtfs_feeds": [{"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"}],
    }
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest["gtfs_feeds"])
    monkeypatch.setattr("assistant.gtfs.ingest.load_manifest", lambda: manifest)
    files = dict(_V1_ZIP_FILES)
    files[malicious_name] = "do not extract"

    def handler(request):
        return httpx.Response(200, content=_build_zip(files))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    assert gtfs.fetch_all() is False

    assert not (raw / "gtfs" / "current.json").exists()
    assert not (raw / "outside.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not list((raw / "gtfs").glob(".transaction.*"))


def test_first_partial_fetch_requires_a_complete_transaction(tmp_path, monkeypatch, capsys):
    _point_config_at(tmp_path, monkeypatch)
    feeds = [
        {"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"},
        {"agency": "SBMTD", "url": "https://sbmtd.gov/g.zip", "fares_version": "v2"},
    ]
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: feeds)
    monkeypatch.setattr(
        "assistant.gtfs.ingest.load_manifest",
        lambda: {"user_agent": "test-agent/0.1"},
    )

    def handler(request):
        return httpx.Response(200, content=_build_zip(_V1_ZIP_FILES))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))

    assert gtfs.fetch_all(only={"MST"}) is False

    assert "first transactional GTFS fetch must include every configured feed" in (
        capsys.readouterr().err
    )
    assert not (gtfs.GTFS_RAW_DIR / "current.json").exists()


def test_selected_snapshot_validation_rejects_retained_file_tampering(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    manifest = {
        "user_agent": "test-agent/0.1",
        "gtfs_feeds": [{"agency": "MST", "url": "https://mst.org/g.zip", "fares_version": "v1"}],
    }
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: manifest["gtfs_feeds"])
    monkeypatch.setattr("assistant.gtfs.ingest.load_manifest", lambda: manifest)

    def handler(request):
        return httpx.Response(200, content=_build_zip(_V1_ZIP_FILES))

    monkeypatch.setattr("assistant.gtfs.httpx.Client", _mock_client(handler))
    assert gtfs.fetch_all() is True
    selected = gtfs.load_current_snapshot_set()
    assert selected is not None
    (selected["MST"].directory / "fare_attributes.txt").write_text(
        "fare_id,price\nRegular,999.00\n"
    )

    with pytest.raises(gtfs.GTFSStorageError, match="differs from the exact ZIP"):
        gtfs.load_current_snapshot_set()


def test_corrupt_transactional_pointer_never_falls_back_to_legacy(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    legacy = raw / "gtfs" / "MST"
    legacy.mkdir(parents=True)
    (legacy / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\n")
    (raw / "gtfs" / "current.json").write_text('{"schema":"broken"}\n')

    with pytest.raises(gtfs.GTFSStorageError):
        gtfs.parse_fares("MST")


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

    # One prose chunk carries $2.00, so the agreement is a specific match rather
    # than a collision — the count and the chunk id are what say which (#141).
    assert records == [
        gtfs.CrossCheckRecord("MST", "Regular", "Regular", "2.00", "yes", 1, ["c#0"])
    ]


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
        gtfs.CrossCheckRecord(
            "MST",
            "(no snapshot)",
            "(no snapshot)",
            None,
            "no_feed",
            reason="configured feed has no validated snapshot; run `make gtfs-fetch`",
        )
    ]


def test_cross_check_no_feed_configured_for_corpus_agency(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: [])
    chunks = [_chunk("Yolobus", "Regular fare is $2.00.")]

    records = gtfs.cross_check(chunks)

    assert records == [
        gtfs.CrossCheckRecord(
            "Yolobus",
            "(no feed configured)",
            "(no feed configured)",
            None,
            "no_feed",
            reason="no feed configured; this agency has not been checked",
        )
    ]


def test_a_colliding_agreement_says_how_many_chunks_it_matched(tmp_path, monkeypatch):
    """Issue #141: the dry run across eleven agencies found zero disagreements.

    That reads as corroboration and is not: the comparison only asks whether the
    feed's amount appears *somewhere* in the agency's prose, so on a fare page
    dense with dollar figures a match is close to guaranteed. The record now
    carries how many chunks it matched, which is the difference between "this
    program's fare agrees" and "this number is published on the site".
    """
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [
        _chunk("MST", "Regular Fixed Route fare is $2.00."),
        _chunk("MST", "A day pass is $2.00 more than a single ride."),
        _chunk("MST", "Replacement cards cost $2.00."),
    ]

    (record,) = gtfs.cross_check(chunks)

    assert record.feed_agrees == "yes"
    assert record.prose_matches == 3


def test_a_no_feed_record_counts_nothing(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: [])
    (record,) = gtfs.cross_check([_chunk("MST", "The fare is $2.00.")])
    assert record.feed_agrees == "no_feed"
    assert record.prose_matches is None


def test_an_amount_repeated_inside_one_chunk_counts_once(tmp_path, monkeypatch):
    """The unit is the chunk, not the occurrence: a table that prints $2.00 in
    four columns of one section is one place the number is published."""
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "Adult $2.00 / Senior $2.00 / Youth $2.00 / Disabled $2.00")]

    (record,) = gtfs.cross_check(chunks)

    assert record.prose_matches == 1


def test_an_agreement_names_the_chunks_it_matched(tmp_path, monkeypatch):
    """Issue #141: a count still hides which chunk agreed.

    The live case this is modelled on is SCMTD's 3-Day Pass at $15.00, which the
    coarse check reported as "yes, 1 prose chunk" — a clean-looking single
    match. The one chunk is the sentence about a $15.00 returned-check service
    charge, and the 3-Day Pass is a product SCMTD's prose says it stopped
    selling. Publishing the chunk id is what lets a reader see that.
    """
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\n3Day,15.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "There is a $15.00 service charge on returned checks.", "fees#3")]

    (record,) = gtfs.cross_check(chunks)

    assert record.feed_agrees == "yes"
    assert record.prose_chunks == ["fees#3"]


def test_a_disagreement_names_no_chunks(tmp_path, monkeypatch):
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,3.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )

    (record,) = gtfs.cross_check([_chunk("MST", "Regular fare is $2.00.")])

    assert record.feed_agrees == "no"
    assert record.prose_chunks == []


def test_matched_chunk_ids_are_capped_but_the_count_is_not(tmp_path, monkeypatch):
    """A truncated list must never make a collision look smaller than it is."""
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\n")
    monkeypatch.setattr(
        gtfs, "load_gtfs_manifest", lambda: [{"agency": "MST", "url": "https://mst.org/g.zip"}]
    )
    chunks = [_chunk("MST", "The fare is $2.00.", f"c#{i}") for i in range(20)]

    (record,) = gtfs.cross_check(chunks)

    assert record.prose_matches == 20
    assert len(record.prose_chunks or []) == gtfs._PROSE_CHUNK_SAMPLE


# ── declared no-feed agencies (issue #141) ───────────────────────────────────


def test_a_declared_no_feed_agency_carries_its_reason(tmp_path, monkeypatch):
    """ "Checked, and here is what was found" is not the same claim as "nobody
    looked". Before #141 the report could only make the second one."""
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gtfs,
        "load_gtfs_manifest",
        lambda: [{"agency": "SacRT", "no_feed_reason": "feed carries no fare table"}],
    )

    (record,) = gtfs.cross_check([_chunk("SacRT", "The fare is $2.50.")])

    assert record.feed_agrees == "no_feed"
    assert record.reason == "feed carries no fare table"


def test_an_unchecked_agency_says_it_was_never_checked(tmp_path, monkeypatch):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(gtfs, "load_gtfs_manifest", lambda: [])

    (record,) = gtfs.cross_check([_chunk("Yolobus", "The fare is $2.00.")])

    assert record.reason == "no feed configured; this agency has not been checked"


def test_partition_rejects_an_entry_that_is_both_or_neither():
    with pytest.raises(gtfs.GTFSStorageError, match="mutually exclusive"):
        gtfs.partition_gtfs_feeds(
            [{"agency": "X", "url": "https://x/g.zip", "no_feed_reason": "y"}]
        )
    with pytest.raises(gtfs.GTFSStorageError, match="needs either url or no_feed_reason"):
        gtfs.partition_gtfs_feeds([{"agency": "X"}])
    with pytest.raises(gtfs.GTFSStorageError, match="non-empty string"):
        gtfs.partition_gtfs_feeds([{"agency": "X", "no_feed_reason": "  "}])
    with pytest.raises(gtfs.GTFSStorageError, match="duplicate GTFS agency"):
        gtfs.partition_gtfs_feeds(
            [
                {"agency": "X", "url": "https://x/g.zip"},
                {"agency": "X", "no_feed_reason": "y"},
            ]
        )
    with pytest.raises(gtfs.GTFSStorageError, match="must be a mapping"):
        gtfs.partition_gtfs_feeds(["X"])


def test_an_agency_name_may_contain_a_space_but_not_a_separator():
    """Two of the corpus's eighteen agencies are two words, and the agency name
    is the join key onto their prose, so it has to admit them (#141)."""
    feeds, declared = gtfs.partition_gtfs_feeds(
        [
            {"agency": "Marin Transit", "url": "https://marintransit.gov/data/google_transit.zip"},
            {"agency": "AC Transit", "no_feed_reason": "authenticated feed"},
        ]
    )
    assert [f["agency"] for f in feeds] == ["Marin Transit"]
    assert [d.agency for d in declared] == ["AC Transit"]
    for unsafe in ("Marin/Transit", " Marin", "Marin ", "..", "Marin\\Transit"):
        with pytest.raises(gtfs.GTFSStorageError, match="safe agency identifier"):
            gtfs.partition_gtfs_feeds([{"agency": unsafe, "url": "https://x/g.zip"}])


def test_fetch_refuses_to_select_a_declared_no_feed_agency(tmp_path, monkeypatch, capsys):
    _point_config_at(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gtfs,
        "load_gtfs_manifest",
        lambda: [
            {"agency": "SBMTD", "url": "https://sbmtd.gov/g.zip"},
            {"agency": "SacRT", "no_feed_reason": "feed carries no fare table"},
        ],
    )

    assert gtfs.fetch_all(only={"SacRT"}) is False
    assert "declared no_feed_reason" in capsys.readouterr().err


# ── report + CLI ─────────────────────────────────────────────────────────────


def test_write_report_shape(tmp_path, monkeypatch):
    _, processed = _point_config_at(tmp_path, monkeypatch)
    records = [gtfs.CrossCheckRecord("MST", "Regular", "Regular", "2.00", "yes", 1, ["mst#0"])]

    gtfs.write_report(records)

    payload = json.loads((processed / "gtfs_cross_check.json").read_text())
    assert payload["records"] == [
        {
            "agency": "MST",
            "fare_id": "Regular",
            "name": "Regular",
            "feed_amount": "2.00",
            "feed_agrees": "yes",
            "prose_matches": 1,
            "prose_chunks": ["mst#0"],
            "reason": None,
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


def test_main_check_prints_the_evidence_behind_each_verdict(tmp_path, monkeypatch, capsys):
    """The CLI is where a human first meets this check, so it has to show an
    agreement's evidence, a disagreement's absence, and a no_feed's reason."""
    raw, _ = _point_config_at(tmp_path, monkeypatch)
    agency_dir = raw / "gtfs" / "MST"
    agency_dir.mkdir(parents=True)
    (agency_dir / "fare_attributes.txt").write_text("fare_id,price\nRegular,2.00\nNew,9.00\n")
    monkeypatch.setattr(
        gtfs,
        "load_gtfs_manifest",
        lambda: [
            {"agency": "MST", "url": "https://mst.org/g.zip"},
            {"agency": "SacRT", "no_feed_reason": "feed carries no fare table"},
        ],
    )
    monkeypatch.setattr(
        "assistant.gtfs.ingest.load_chunks",
        lambda: [_chunk("MST", "Regular fare is $2.00.", "mst-fares#0")],
    )
    monkeypatch.setattr("sys.argv", ["gtfs", "check"])

    gtfs.main()

    captured = capsys.readouterr()
    assert "1 prose chunk(s): mst-fares#0" in captured.out
    assert "no prose chunk states this amount" in captured.out
    assert "feed carries no fare table" in captured.out
    assert "coverage: 1 of 2 corpus agencies" in captured.out
    assert "1 disagreement(s) found." in captured.err


def test_main_fetch_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(gtfs, "fetch_all", lambda only=None: False)
    monkeypatch.setattr("sys.argv", ["gtfs", "fetch"])

    with pytest.raises(SystemExit, match="1"):
        gtfs.main()


def test_main_unknown_command_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["gtfs", "bogus"])
    with pytest.raises(SystemExit):
        gtfs.main()
