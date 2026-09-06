"""Per-agency fare-change feeds (issue #219).

The feeds are a public interface: something else subscribes to them and pins on
their ids. So the tests here are about the properties a subscriber depends on —
one agency's change never appears in another's feed, an unchanged corpus never
republishes, and every entry carries the fields RFC 4287 and JSON Feed 1.1
require — rather than about the exact prose of an entry.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from assistant import config, feeds

ATOM = "{http://www.w3.org/2005/Atom}"


def _chunk(doc_id: str, agency: str, text: str) -> dict:
    return {
        "chunk_id": f"{doc_id}#0",
        "doc_id": doc_id,
        "agency": agency,
        "agency_full": agency,
        "doc_title": "Fares",
        "url": f"https://example.org/{doc_id}",
        "fetch_date": "2026-06-12",
        "language": "en",
        "section": "Fares",
        "text": text,
    }


def _archive(versions_dir, version: str, chunks: list[dict], *, archived_at: str, as_of: str):
    directory = versions_dir / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "chunks.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in chunks), encoding="utf-8"
    )
    (directory / "version.json").write_text(
        json.dumps(
            {
                "corpus_version": version,
                "as_of": as_of,
                "archived_at": archived_at,
                "agencies": sorted({c["agency"] for c in chunks}),
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def two_versions(tmp_path, monkeypatch):
    """A Yolobus page changes; MST's does not. The Done-when case from #219."""
    versions = tmp_path / "versions"
    first = [
        _chunk("yolobus-fares", "Yolobus", "The adult fare is $2.25."),
        _chunk("mst-fares", "MST", "The adult fare is $2.00."),
    ]
    second = [
        _chunk("yolobus-fares", "Yolobus", "The adult fare is $2.50."),
        _chunk("mst-fares", "MST", "The adult fare is $2.00."),
    ]
    _archive(
        versions, "aaaaaaaaaaaa", first, archived_at="2026-07-01T00:00:00+00:00", as_of="2026-07-01"
    )
    _archive(
        versions,
        "bbbbbbbbbbbb",
        second,
        archived_at="2026-08-01T00:00:00+00:00",
        as_of="2026-08-01",
    )
    monkeypatch.setattr(config, "VERSIONS_DIR", versions)
    return versions


class TestChangeEntries:
    def test_a_change_reaches_only_the_agency_whose_document_moved(self, two_versions):
        entries = feeds.change_entries()
        yolobus = [e.corpus_version for e in entries["Yolobus"]]
        mst = [e.corpus_version for e in entries["MST"]]
        assert yolobus == ["bbbbbbbbbbbb", "aaaaaaaaaaaa"]
        assert mst == ["aaaaaaaaaaaa"]
        assert entries["Yolobus"][0].changed == ("yolobus-fares",)

    def test_the_oldest_version_is_a_first_snapshot_not_a_change(self, two_versions):
        """Reporting "2 documents changed" about the beginning of the record
        would be an absence rendered as an event: nothing changed, the archive
        simply starts there."""
        oldest = feeds.change_entries()["MST"][0]
        assert oldest.first_snapshot
        assert oldest.changed == ()
        assert "First retained snapshot" in oldest.summary

    def test_entries_are_newest_first(self, two_versions):
        dates = [e.archived_at for e in feeds.change_entries()["Yolobus"]]
        assert dates == sorted(dates, reverse=True)


class TestFeedFiles:
    def test_one_file_pair_per_agency_plus_a_combined_feed(self, two_versions):
        files = feeds.build_feeds()
        assert set(files) == {
            "yolobus.xml",
            "yolobus.json",
            "mst.xml",
            "mst.json",
            "all.xml",
            "all.json",
        }

    def test_the_combined_feed_carries_one_entry_per_corpus_version(self, two_versions):
        combined = json.loads(feeds.build_feeds()["all.json"])
        versions = [item["_fare_policy_assistant"]["corpus_version"] for item in combined["items"]]
        assert versions == ["bbbbbbbbbbbb", "aaaaaaaaaaaa"]

    def test_an_agency_feed_never_names_another_agency(self, two_versions):
        assert "MST" not in feeds.build_feeds()["yolobus.xml"]

    def test_atom_carries_what_rfc_4287_requires(self, two_versions):
        root = ET.fromstring(feeds.build_feeds()["yolobus.xml"])
        assert root.tag == f"{ATOM}feed"
        for required in ("id", "title", "updated"):
            assert root.find(f"{ATOM}{required}") is not None
        entries = root.findall(f"{ATOM}entry")
        assert entries
        for entry in entries:
            for required in ("id", "title", "updated"):
                assert entry.find(f"{ATOM}{required}") is not None

    def test_json_feed_carries_what_version_1_1_requires(self, two_versions):
        payload = json.loads(feeds.build_feeds()["yolobus.json"])
        assert payload["version"] == "https://jsonfeed.org/version/1.1"
        assert payload["title"]
        assert payload["items"]
        assert all(item["id"] for item in payload["items"])

    def test_ids_are_stable_across_regeneration(self, two_versions):
        first = json.loads(feeds.build_feeds()["yolobus.json"])
        second = json.loads(feeds.build_feeds()["yolobus.json"])
        assert [i["id"] for i in first["items"]] == [i["id"] for i in second["items"]]

    def test_no_feed_loads_a_script(self, two_versions):
        """The hub serves these under its own content-security policy, and the
        policy is the reason the hub is safe to publish unattended."""
        for content in feeds.build_feeds().values():
            assert "<script" not in content.lower()
            assert "javascript:" not in content.lower()

    def test_the_feed_url_follows_the_cname_rather_than_a_second_constant(
        self, two_versions, tmp_path, monkeypatch
    ):
        cname = tmp_path / "CNAME"
        cname.write_text("example.test\n", encoding="utf-8")
        monkeypatch.setattr(feeds, "CNAME_PATH", cname)
        assert "https://example.test/feeds/yolobus.json" in feeds.build_feeds()["yolobus.json"]

    def test_a_missing_cname_falls_back_rather_than_emitting_an_empty_origin(
        self, two_versions, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(feeds, "CNAME_PATH", tmp_path / "absent")
        assert feeds.site_url() == feeds.DEFAULT_SITE_URL

    def test_an_empty_archive_is_dated_at_the_epoch_not_today(self, tmp_path, monkeypatch):
        """An archive with nothing in it must not look like a fresh
        publication to every subscriber that polls it."""
        monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "empty")
        assert "1970-01-01T00:00:00+00:00" in feeds.build_feeds()["all.xml"]

    def test_colliding_agency_slugs_are_refused_rather_than_overwritten(
        self, tmp_path, monkeypatch
    ):
        versions = tmp_path / "versions"
        _archive(
            versions,
            "aaaaaaaaaaaa",
            [_chunk("a", "Bay Bus", "x"), _chunk("b", "bay-bus", "y")],
            archived_at="2026-07-01T00:00:00+00:00",
            as_of="2026-07-01",
        )
        monkeypatch.setattr(config, "VERSIONS_DIR", versions)
        with pytest.raises(ValueError, match="collide"):
            feeds.build_feeds()


class TestWriteAndCheck:
    def test_regenerating_an_unchanged_corpus_is_byte_identical(self, two_versions, tmp_path):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        before = {p.name: p.read_bytes() for p in sorted(out.iterdir())}
        feeds.write_feeds(out)
        assert {p.name: p.read_bytes() for p in sorted(out.iterdir())} == before

    def test_check_is_clean_after_a_write(self, two_versions, tmp_path):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        assert feeds.stale_feeds(out) == []

    def test_check_names_a_file_the_corpus_no_longer_matches(self, two_versions, tmp_path):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        (out / "yolobus.xml").write_text("stale", encoding="utf-8")
        assert feeds.stale_feeds(out) == ["yolobus.xml"]

    def test_check_names_a_file_that_should_no_longer_exist(self, two_versions, tmp_path):
        """A retired agency's feed left behind would keep serving a record that
        the corpus no longer produces."""
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        (out / "retired.xml").write_text("<feed/>", encoding="utf-8")
        assert "retired.xml" in feeds.stale_feeds(out)

    def test_writing_removes_a_file_the_corpus_no_longer_produces(self, two_versions, tmp_path):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        (out / "retired.xml").write_text("<feed/>", encoding="utf-8")
        feeds.write_feeds(out)
        assert not (out / "retired.xml").exists()

    def test_check_mode_exits_nonzero_on_a_stale_file(
        self, two_versions, monkeypatch, tmp_path, capsys
    ):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        (out / "mst.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(feeds, "FEEDS_DIR", out)
        assert feeds.main(["--check"]) == 1
        assert "mst.json" in capsys.readouterr().err

    def test_check_mode_exits_zero_when_the_committed_feeds_match(
        self, two_versions, monkeypatch, tmp_path
    ):
        out = tmp_path / "feeds"
        feeds.write_feeds(out)
        monkeypatch.setattr(feeds, "FEEDS_DIR", out)
        assert feeds.main(["--check"]) == 0

    def test_write_mode_reports_what_it_wrote(self, two_versions, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(feeds, "FEEDS_DIR", tmp_path / "feeds")
        assert feeds.main([]) == 0
        assert "wrote 6 feed file(s)" in capsys.readouterr().out


class TestCommittedFeedsMatchTheCorpus:
    def test_the_committed_feeds_are_current(self):
        """The same assertion `make feeds-check` makes in CI, so a corpus change
        that forgets to regenerate fails here first."""
        assert feeds.stale_feeds() == []
