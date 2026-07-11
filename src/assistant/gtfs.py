"""GTFS(-Fares) cross-validation: a second, structured evidence source.

Ingests agencies' machine-readable GTFS static feeds and cross-checks the fare
amounts they carry against the prose corpus for the same agency. A
disagreement ("web page says $2.50, feed says $3.00") is exactly the
wrong-fare liability scenario this project cares about, caught mechanically
instead of by luck: agency web pages and GTFS feeds drift at different
speeds. See docs/ideation/03-expansions.md EXP-06 and
docs/decisions/0011-gtfs-cross-validation.md.

Design constraint: the feed is a *tripwire*, never a source of truth. The
published prose remains the citable policy — nothing here overrides an
answer or substitutes the feed price for it; `cross_check` only produces a
report of agreement/disagreement per fare row.

Two GTFS-Fares schemas are in live use and both are handled:
  - v1 ("classic"): fare_attributes.txt (fare_id, price, ...)
  - v2: fare_products.txt (fare_product_id, fare_product_name, amount, ...)

EXP-01's typed per-fact `FareFact` table (agency/program/rider_class/price)
does not exist yet in this codebase, so cross-checking here compares against
raw dollar amounts extracted from the corpus text (`prose_fare_amounts`)
rather than structured, per-program fact rows. That means a match only proves
"the feed's amount appears *somewhere* in this agency's prose", not "this
specific program's fare agrees" — coarser than the fact-row match EXP-06
describes, but the same disagreement shape (a feed amount with no matching
prose figure anywhere for that agency is still a real drift signal). Once
EXP-01 lands, `cross_check` should compare against `facts.jsonl` instead for
tighter per-program matching.

Usage:
    python -m assistant.gtfs fetch    # snapshot each gtfs_feeds[] entry's fare files
    python -m assistant.gtfs check    # cross-check snapshotted feed fares vs. corpus prose
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from assistant import config, ingest

GTFS_RAW_DIR = config.RAW_DIR / "gtfs"
CROSS_CHECK_PATH = config.PROCESSED_DIR / "gtfs_cross_check.json"

# Only these members are ever extracted from a fetched feed zip and written to
# disk: fare data plus agency.txt for a sanity check. A GTFS zip also carries
# stops/shapes/stop_times (multi-MB of geodata this project has no use for);
# keeping the snapshot to fare-relevant files keeps it small and diffable,
# consistent with corpus/raw's "committed snapshot" convention.
_FARE_MEMBERS = (
    "agency.txt",
    "fare_attributes.txt",
    "fare_rules.txt",
    "fare_products.txt",
    "fare_leg_rules.txt",
    "fare_media.txt",
    "fare_transfer_rules.txt",
    "rider_categories.txt",
)

# Fare figures in prose are always written as "$X.XX" or "$X" (see the fare
# tables ingest.py linearizes); tolerate a thousands separator defensively.
_DOLLAR_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)")

# A zero-price feed row ("Free", a child fare, a transfer) is almost never
# spelled "$0.00" in agency prose — it's spelled "free" or "no charge". Without
# this, every free fare in a feed would falsely flag as a disagreement on
# every agency, which is exactly the noisy-alarm failure mode that would get
# this feature turned off. A zero-amount fare counts as agreeing if the
# agency's prose mentions "free" anywhere.
_FREE_RE = re.compile(r"\bfree\b", re.I)
_ZERO = Decimal("0.00")


@dataclass
class FeedFare:
    agency: str
    fare_id: str
    name: str
    amount: Decimal
    rider_category: str | None = None  # v2 only; None for a v1 fare_attributes row


@dataclass
class CrossCheckRecord:
    agency: str
    fare_id: str
    name: str
    feed_amount: str | None
    feed_agrees: str  # "yes" | "no" | "no_feed"


def load_gtfs_manifest() -> list[dict]:
    manifest = ingest.load_manifest()
    return manifest.get("gtfs_feeds", [])


# ── fetch ────────────────────────────────────────────────────────────────────


def fetch_all(only: set[str] | None = None) -> None:
    """Snapshot each configured agency's fare-relevant GTFS files.

    Downloads the agency's GTFS zip, keeps only `_FARE_MEMBERS`, and writes
    them to `corpus/raw/gtfs/<agency>/`. A feed that fails to fetch or isn't a
    valid zip is reported and skipped; existing snapshots are left untouched
    so a transient outage never blanks out the last-known-good fare data.
    """
    feeds = load_gtfs_manifest()
    ua = ingest.load_manifest()["user_agent"]
    GTFS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    with httpx.Client(headers={"User-Agent": ua}, follow_redirects=True, timeout=60) as client:
        for feed in feeds:
            agency = feed["agency"]
            if only and agency not in only:
                continue
            try:
                resp = client.get(feed["url"])
                resp.raise_for_status()
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
            except (httpx.HTTPError, zipfile.BadZipFile) as exc:
                failures.append((agency, str(exc)))
                print(f"FAIL  {agency}: {exc}", file=sys.stderr)
                continue

            agency_dir = GTFS_RAW_DIR / agency
            agency_dir.mkdir(parents=True, exist_ok=True)
            found = [name for name in _FARE_MEMBERS if name in zf.namelist()]
            for name in found:
                (agency_dir / name).write_bytes(zf.read(name))
            meta = {
                "agency": agency,
                "url": feed["url"],
                "fares_version": feed.get("fares_version"),
                "fetch_date": datetime.now(UTC).date().isoformat(),
                "feed_bytes": len(resp.content),
                "fare_files": found,
            }
            (agency_dir / "meta.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
            if not found:
                print(f"warn  {agency}: feed fetched but no fare files in it ({feed['url']})")
            else:
                print(f"ok    {agency}  {', '.join(found)}")

    if failures:
        print(f"\n{len(failures)} feed(s) failed; snapshots unchanged.", file=sys.stderr)


# ── parse ────────────────────────────────────────────────────────────────────


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_fares(agency: str) -> list[FeedFare]:
    """Parse fare rows out of an agency's snapshotted GTFS files.

    Supports both the v1 ("classic") and v2 GTFS-Fares schemas; returns an
    empty list if no snapshot or no recognized fare file exists (the "no
    feed" case the design tolerates).
    """
    agency_dir = GTFS_RAW_DIR / agency
    if (agency_dir / "fare_products.txt").exists():
        return _parse_fares_v2(agency_dir)
    if (agency_dir / "fare_attributes.txt").exists():
        return _parse_fares_v1(agency_dir)
    return []


def _parse_fares_v1(agency_dir: Path) -> list[FeedFare]:
    fares = []
    for row in _read_csv(agency_dir / "fare_attributes.txt"):
        try:
            amount = Decimal(row["price"])
        except (InvalidOperation, KeyError):
            continue
        fare_id = row["fare_id"]
        fares.append(FeedFare(agency=agency_dir.name, fare_id=fare_id, name=fare_id, amount=amount))
    return fares


def _parse_fares_v2(agency_dir: Path) -> list[FeedFare]:
    fares = []
    for row in _read_csv(agency_dir / "fare_products.txt"):
        try:
            amount = Decimal(row["amount"])
        except (InvalidOperation, KeyError):
            continue
        fares.append(
            FeedFare(
                agency=agency_dir.name,
                fare_id=row["fare_product_id"],
                name=row.get("fare_product_name") or row["fare_product_id"],
                amount=amount,
                rider_category=row.get("rider_category_id") or None,
            )
        )
    return fares


# ── cross-check ──────────────────────────────────────────────────────────────


def prose_fare_amounts(agency: str, chunks: list[ingest.Chunk] | None = None) -> set[Decimal]:
    """Every dollar amount mentioned anywhere in the agency's prose corpus."""
    chunks = chunks if chunks is not None else ingest.load_chunks()
    amounts: set[Decimal] = set()
    for chunk in chunks:
        if chunk.agency != agency:
            continue
        for match in _DOLLAR_RE.finditer(chunk.text):
            try:
                amounts.add(Decimal(match.group(1).replace(",", "")))
            except InvalidOperation:
                continue
    return amounts


def cross_check(chunks: list[ingest.Chunk] | None = None) -> list[CrossCheckRecord]:
    """Compare every agency's snapshotted feed fares against its prose corpus.

    An agency with no configured feed, or a configured feed with no snapshot
    yet (`make gtfs-fetch` not run, or the fetch failed), gets one `no_feed`
    record so a report reader sees coverage without it reading as a failure.
    Never used to alter an answer — see module docstring.
    """
    chunks = chunks if chunks is not None else ingest.load_chunks()
    feeds = load_gtfs_manifest()
    fed_agencies = {f["agency"] for f in feeds}
    corpus_agencies = {c.agency for c in chunks}
    records: list[CrossCheckRecord] = []

    for feed in feeds:
        agency = feed["agency"]
        fares = parse_fares(agency)
        if not fares:
            records.append(
                CrossCheckRecord(agency, "(no snapshot)", "(no snapshot)", None, "no_feed")
            )
            continue
        prose_amounts = prose_fare_amounts(agency, chunks)
        prose_mentions_free = any(_FREE_RE.search(c.text) for c in chunks if c.agency == agency)
        for fare in fares:
            if fare.amount == _ZERO:
                agrees = "yes" if prose_mentions_free else "no"
            else:
                agrees = "yes" if fare.amount in prose_amounts else "no"
            records.append(
                CrossCheckRecord(agency, fare.fare_id, fare.name, str(fare.amount), agrees)
            )

    for agency in sorted(corpus_agencies - fed_agencies):
        records.append(
            CrossCheckRecord(
                agency, "(no feed configured)", "(no feed configured)", None, "no_feed"
            )
        )

    return records


def write_report(records: list[CrossCheckRecord]) -> None:
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(UTC).isoformat(),
        "records": [asdict(r) for r in records],
    }
    CROSS_CHECK_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "fetch":
        fetch_all(only=set(sys.argv[2:]) or None)
    elif cmd == "check":
        records = cross_check()
        write_report(records)
        for r in records:
            print(f"{r.feed_agrees:8} {r.agency:10} {r.name} ({r.feed_amount})")
        disagreements = [r for r in records if r.feed_agrees == "no"]
        print(f"\nwrote {len(records)} record(s) -> {CROSS_CHECK_PATH}")
        if disagreements:
            print(f"{len(disagreements)} disagreement(s) found.", file=sys.stderr)
    else:
        raise SystemExit(f"unknown command: {cmd} (expected fetch|check)")


if __name__ == "__main__":
    main()
