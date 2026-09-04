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
    python -m assistant.gtfs fetch    # atomically capture exact gtfs_feeds[] ZIPs
    python -m assistant.gtfs check    # cross-check snapshotted feed fares vs. corpus prose
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from assistant import config, ingest

GTFS_RAW_DIR = config.RAW_DIR / "gtfs"
CROSS_CHECK_PATH = config.PROCESSED_DIR / "gtfs_cross_check.json"

# The exact ZIP is retained, but only files read by this codebase are extracted.
# `parse_fares` consumes the two schema-specific product tables and
# `fare_table.load_rider_categories` consumes the optional v2 category table.
# Any later consumer of another GTFS member must add it here (and thereby change
# the receipt) rather than silently depending on an unrecorded extraction.
_CONSUMED_FARE_MEMBERS = (
    "fare_attributes.txt",
    "fare_products.txt",
    "rider_categories.txt",
)
_REQUIRED_COLUMNS = {
    "fare_attributes.txt": frozenset({"fare_id", "price"}),
    "fare_products.txt": frozenset({"fare_product_id", "amount"}),
    "rider_categories.txt": frozenset({"rider_category_id"}),
}
_SCHEMA_REQUIRED_FILE = {"v1": "fare_attributes.txt", "v2": "fare_products.txt"}

GTFS_RECEIPT_SCHEMA = "fare-assistant.gtfs-feed-receipt.v1"
GTFS_CURRENT_SCHEMA = "fare-assistant.gtfs-current.v1"
GTFS_SELECTED_SET_SCHEMA = "fare-assistant.gtfs-selected-set.v1"
_CURRENT_NAME = "current.json"
_SNAPSHOTS_NAME = "snapshots"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The agency identifier is both a storage path component and the join key onto
# the prose corpus, so it has to admit the corpus's own agency names — two of
# which ("AC Transit", "Marin Transit") contain a space. A single space class is
# all that widened for issue #141; the properties this guard actually exists for
# are unchanged: first and last character are alphanumeric (so no leading or
# trailing whitespace, and neither "." nor ".." can be spelled), no path or
# drive separator, no NUL, and a bounded length.
_AGENCY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,62}[A-Za-z0-9])?$")
_MAX_ZIP_BYTES = 256 * 1024 * 1024
_MAX_CONSUMED_MEMBER_BYTES = 32 * 1024 * 1024
_FETCH_PROCESS_LOCK = threading.Lock()
_CURRENT_CACHE_LOCK = threading.Lock()

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
# How many matching chunk ids a record carries. `prose_matches` still publishes
# the true total, so a truncated list never understates the collision.
_PROSE_CHUNK_SAMPLE = 6


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
    # How much that verdict is worth (issue #141). The comparison is coarse by
    # construction — see the module docstring — so a bare "yes" only means "this
    # amount appears somewhere in this agency's prose", never "this program's
    # fare agrees". `prose_matches` is how many distinct corpus chunks for this
    # agency the amount was found in: 1 is a specific match, 12 is a collision
    # in a page full of dollar figures and the "yes" proves close to nothing.
    # None on a `no_feed` record, where nothing was compared.
    #
    # Published rather than inferred, because the dry run in #141 found zero
    # disagreements across eleven agencies and read as agreement when it was
    # really the check being nearly vacuous. A number a reader can see is the
    # difference between those two readings.
    prose_matches: int | None = None
    # *Which* chunks those were, so an agreement can be audited instead of
    # trusted. The count alone still hides the thing that matters: SCMTD's feed
    # prices a 3-Day Pass at $15.00 and this check reported "yes, 1 prose
    # chunk", which reads like a clean single match — but the one chunk is the
    # sentence "There is a $15.00 service charge on all returned checks", and
    # the 3-Day Pass is a product SCMTD's prose says it stopped selling on
    # 2026-07-01. A reader cannot tell those apart from a number. Sorted,
    # capped at `_PROSE_CHUNK_SAMPLE`, empty on a disagreement and None where
    # nothing was compared.
    prose_chunks: list[str] | None = None
    # Why nothing was compared, on a `no_feed` record; None on a compared row.
    # Three shapes reach a reader here, and they are not the same fact:
    # "no feed configured" (nobody has looked at this agency), the manifest's
    # own `no_feed_reason` (somebody looked, and this is what they found), and
    # "configured feed has no snapshot" (a feed is configured but `make
    # gtfs-fetch` has not run or did not succeed). Before issue #141 all three
    # rendered as the same bare `no_feed`, which made an unchecked agency and a
    # checked-and-unusable one indistinguishable in the report.
    reason: str | None = None


class GTFSStorageError(ValueError):
    """A feed capture or selected GTFS snapshot set is unsafe or inconsistent."""


@dataclass(frozen=True)
class FeedSnapshot:
    """One validated immutable feed capture selected by ``current.json``."""

    agency: str
    snapshot_version: str
    receipt_sha256: str
    zip_sha256: str
    zip_bytes: int
    fares_schema: str
    requested_url: str
    final_url: str
    fetched_at: str
    http_status: int
    directory: Path

    def pointer(self) -> dict[str, object]:
        return {
            "agency": self.agency,
            "receipt_sha256": self.receipt_sha256,
            "snapshot_version": self.snapshot_version,
            "zip_bytes": self.zip_bytes,
            "zip_sha256": self.zip_sha256,
        }


_CURRENT_CACHE: dict[tuple[str, str], dict[str, FeedSnapshot]] = {}


def load_gtfs_manifest() -> list[dict]:
    manifest = ingest.load_manifest()
    return manifest.get("gtfs_feeds", [])


@dataclass(frozen=True)
class DeclaredNoFeed:
    """An agency checked for a GTFS-Fares feed and deliberately not configured."""

    agency: str
    reason: str


def partition_gtfs_feeds(
    raw: list[dict] | None = None,
) -> tuple[list[Mapping[str, object]], list[DeclaredNoFeed]]:
    """Split ``gtfs_feeds`` into fetchable feeds and checked-but-unusable agencies.

    An entry carries either a ``url`` (fetch it) or a ``no_feed_reason`` (it was
    checked and cannot be fetched or carries no fare table), never both and never
    neither. The second form exists because "this agency has no feed" and "nobody
    has looked at this agency yet" are different claims, and until issue #141 the
    report could only make the second one. `make gtfs-fetch` skips a declared
    no-feed agency; `cross_check` still emits its record, carrying the reason.
    """
    entries = raw if raw is not None else load_gtfs_manifest()
    feeds: list[Mapping[str, object]] = []
    declared: list[DeclaredNoFeed] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        context = f"manifest GTFS feed {index}"
        if not isinstance(item, Mapping):
            raise GTFSStorageError(f"{context}: entry must be a mapping")
        agency = _agency_name(item.get("agency"), context)
        if agency in seen:
            raise GTFSStorageError(f"manifest: duplicate GTFS agency {agency}")
        seen.add(agency)
        reason = item.get("no_feed_reason")
        has_url = item.get("url") is not None
        if has_url and reason is not None:
            raise GTFSStorageError(
                f"{context} ({agency}): url and no_feed_reason are mutually exclusive"
            )
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip():
                raise GTFSStorageError(
                    f"{context} ({agency}): no_feed_reason must be a non-empty string"
                )
            declared.append(DeclaredNoFeed(agency=agency, reason=reason.strip()))
            continue
        if not has_url:
            raise GTFSStorageError(f"{context} ({agency}): needs either url or no_feed_reason")
        feeds.append(item)
    return feeds, declared


# ── fetch ────────────────────────────────────────────────────────────────────


def _canonical_json(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise GTFSStorageError(f"JSON contains duplicate key: {key}")
        out[key] = value
    return out


def _load_canonical_json(raw: bytes, context: str) -> dict[str, object]:
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GTFSStorageError(f"{context} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise GTFSStorageError(f"{context} must be a JSON object")
    if _canonical_json(payload) != raw:
        raise GTFSStorageError(f"{context} is not canonical JSON")
    return payload


def _exact_fields(payload: Mapping[str, object], expected: set[str], context: str) -> None:
    fields = set(payload)
    if fields != expected:
        missing = sorted(expected - fields)
        unexpected = sorted(fields - expected)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise GTFSStorageError(f"{context} fields are invalid ({'; '.join(details)})")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _selected_set_version(rows: list[dict[str, object]]) -> str:
    return _sha256(_canonical_json({"feeds": rows, "schema": GTFS_SELECTED_SET_SCHEMA}))


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_timestamp(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise GTFSStorageError(f"{context} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise GTFSStorageError(f"{context} must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo != UTC or parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value:
        raise GTFSStorageError(f"{context} must use canonical whole-second UTC form")
    return value


def _validate_url(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GTFSStorageError(f"{context} must be a non-empty absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise GTFSStorageError(f"{context} must be a valid absolute HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise GTFSStorageError(f"{context} must be an absolute HTTP(S) URL without credentials")
    return value


def _agency_name(value: object, context: str) -> str:
    if not isinstance(value, str) or value in {".", ".."} or not _AGENCY.fullmatch(value):
        raise GTFSStorageError(f"{context} is not a safe agency identifier")
    return value


def _regular_file_bytes(path: Path, context: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GTFSStorageError(f"{context} is missing or not a regular file")
    return path.read_bytes()


def _ensure_directory(path: Path, context: str, *, parents: bool = False) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise GTFSStorageError(f"{context} is not a regular directory")
        return
    path.mkdir(parents=parents)


def _safe_zip_infos(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        name = info.filename
        path = PurePosixPath(name)
        mode = (info.external_attr >> 16) & 0xFFFF
        if (
            not name
            or "\x00" in name
            or "\\" in name
            or name.startswith("/")
            or "//" in name
            or any(part in {"", ".", ".."} for part in path.parts)
            or (path.parts and re.fullmatch(r"[A-Za-z]:", path.parts[0]))
        ):
            raise GTFSStorageError(f"unsafe ZIP member path: {name!r}")
        if name in infos:
            raise GTFSStorageError(f"duplicate ZIP member: {name!r}")
        if info.flag_bits & 0x1:
            raise GTFSStorageError(f"encrypted ZIP member is unsupported: {name!r}")
        file_type = stat.S_IFMT(mode)
        if file_type and not (
            file_type == stat.S_IFREG or (info.is_dir() and file_type == stat.S_IFDIR)
        ):
            raise GTFSStorageError(f"non-regular ZIP member is unsupported: {name!r}")
        if info.file_size < 0:
            raise GTFSStorageError(f"ZIP member has an invalid size: {name!r}")
        if name in _CONSUMED_FARE_MEMBERS and info.file_size > _MAX_CONSUMED_MEMBER_BYTES:
            raise GTFSStorageError(f"consumed ZIP member exceeds the size limit: {name!r}")
        infos[name] = info
    return infos


def _validate_csv(name: str, raw: bytes) -> None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GTFSStorageError(f"{name} is not valid UTF-8") from exc
    try:
        header = next(csv.reader(io.StringIO(text, newline="")))
    except (StopIteration, csv.Error) as exc:
        raise GTFSStorageError(f"{name} does not contain a readable CSV header") from exc
    if not header or any(not value for value in header) or len(header) != len(set(header)):
        raise GTFSStorageError(f"{name} has an invalid or duplicate CSV header")
    missing = _REQUIRED_COLUMNS[name] - set(header)
    if missing:
        raise GTFSStorageError(f"{name} is missing required columns: {', '.join(sorted(missing))}")


def _resolve_fares_schema(configured: object, member_names: set[str]) -> str:
    if configured is not None:
        if not isinstance(configured, str) or configured not in _SCHEMA_REQUIRED_FILE:
            raise GTFSStorageError("fares_version must be v1 or v2")
        schema = configured
    else:
        present = [
            schema for schema, required in _SCHEMA_REQUIRED_FILE.items() if required in member_names
        ]
        if len(present) != 1:
            raise GTFSStorageError(
                "fares_version is required when the ZIP schema cannot be inferred uniquely"
            )
        schema = present[0]
    required = _SCHEMA_REQUIRED_FILE[schema]
    if required not in member_names:
        raise GTFSStorageError(f"configured {schema} feed is missing {required}")
    return schema


def _extract_consumed_fare_files(
    zip_bytes: bytes,
    configured_schema: object,
) -> tuple[str, dict[str, bytes]]:
    if not zip_bytes or len(zip_bytes) > _MAX_ZIP_BYTES:
        raise GTFSStorageError("GTFS ZIP is empty or exceeds the download size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            infos = _safe_zip_infos(zf)
            schema = _resolve_fares_schema(configured_schema, set(infos))
            extracted: dict[str, bytes] = {}
            for name in _CONSUMED_FARE_MEMBERS:
                info = infos.get(name)
                if info is None or info.is_dir():
                    continue
                try:
                    raw = zf.read(info)
                except (NotImplementedError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise GTFSStorageError(f"could not validate ZIP member {name!r}") from exc
                _validate_csv(name, raw)
                extracted[name] = raw
    except zipfile.BadZipFile as exc:
        raise GTFSStorageError("response is not a valid GTFS ZIP") from exc
    return schema, extracted


def _receipt_payload(
    *,
    agency: str,
    requested_url: str,
    response: httpx.Response,
    fares_schema: str,
    zip_bytes: bytes,
    extracted: Mapping[str, bytes],
) -> dict[str, object]:
    return {
        "agency": agency,
        "extracted_files": [
            {"bytes": len(raw), "name": name, "sha256": _sha256(raw)}
            for name, raw in sorted(extracted.items())
        ],
        "fares_schema": fares_schema,
        "fetched_at": _utc_timestamp(),
        "final_url": _validate_url(str(response.url), "response final URL"),
        "http_status": response.status_code,
        "requested_url": requested_url,
        "schema": GTFS_RECEIPT_SCHEMA,
        "zip": {"bytes": len(zip_bytes), "sha256": _sha256(zip_bytes)},
    }


def _write_file(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage_feed(
    client: httpx.Client,
    feed: Mapping[str, object],
    transaction: Path,
) -> FeedSnapshot:
    agency = _agency_name(feed.get("agency"), "manifest GTFS agency")
    requested_url = _validate_url(feed.get("url"), f"manifest GTFS URL for {agency}")
    response = client.get(requested_url)
    response.raise_for_status()
    zip_bytes = response.content
    fares_schema, extracted = _extract_consumed_fare_files(
        zip_bytes,
        feed.get("fares_version"),
    )
    receipt = _receipt_payload(
        agency=agency,
        requested_url=requested_url,
        response=response,
        fares_schema=fares_schema,
        zip_bytes=zip_bytes,
        extracted=extracted,
    )
    receipt_bytes = _canonical_json(receipt)
    snapshot_version = _sha256(receipt_bytes)
    stage = transaction / agency
    stage.mkdir()
    _write_file(stage / "feed.zip", zip_bytes)
    for name, raw in sorted(extracted.items()):
        _write_file(stage / name, raw)
    _write_file(stage / "receipt.json", receipt_bytes)
    _fsync_directory(stage)
    return _validate_feed_snapshot(
        stage,
        expected_agency=agency,
        expected_snapshot_version=snapshot_version,
    )


def _validate_receipt(payload: Mapping[str, object], context: str) -> None:
    _exact_fields(
        payload,
        {
            "agency",
            "extracted_files",
            "fares_schema",
            "fetched_at",
            "final_url",
            "http_status",
            "requested_url",
            "schema",
            "zip",
        },
        context,
    )
    if payload["schema"] != GTFS_RECEIPT_SCHEMA:
        raise GTFSStorageError(f"{context} has an unsupported schema")
    _agency_name(payload["agency"], f"{context}.agency")
    _validate_url(payload["requested_url"], f"{context}.requested_url")
    _validate_url(payload["final_url"], f"{context}.final_url")
    _validate_timestamp(payload["fetched_at"], f"{context}.fetched_at")
    status = payload["http_status"]
    if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 299:
        raise GTFSStorageError(f"{context}.http_status must be a successful HTTP status")
    fares_schema = payload["fares_schema"]
    if not isinstance(fares_schema, str) or fares_schema not in _SCHEMA_REQUIRED_FILE:
        raise GTFSStorageError(f"{context}.fares_schema must be v1 or v2")
    zip_record = payload["zip"]
    if not isinstance(zip_record, dict):
        raise GTFSStorageError(f"{context}.zip must be an object")
    _exact_fields(zip_record, {"bytes", "sha256"}, f"{context}.zip")
    if (
        not isinstance(zip_record["bytes"], int)
        or isinstance(zip_record["bytes"], bool)
        or zip_record["bytes"] <= 0
        or not isinstance(zip_record["sha256"], str)
        or not _SHA256.fullmatch(zip_record["sha256"])
    ):
        raise GTFSStorageError(f"{context}.zip has invalid bytes or sha256")
    extracted = payload["extracted_files"]
    if not isinstance(extracted, list) or not extracted:
        raise GTFSStorageError(f"{context}.extracted_files must be a non-empty list")
    names: list[str] = []
    for index, item in enumerate(extracted):
        item_context = f"{context}.extracted_files[{index}]"
        if not isinstance(item, dict):
            raise GTFSStorageError(f"{item_context} must be an object")
        _exact_fields(item, {"bytes", "name", "sha256"}, item_context)
        name = item["name"]
        size = item["bytes"]
        digest = item["sha256"]
        if (
            not isinstance(name, str)
            or name not in _CONSUMED_FARE_MEMBERS
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise GTFSStorageError(f"{item_context} is invalid")
        names.append(name)
    if names != sorted(names) or len(names) != len(set(names)):
        raise GTFSStorageError(f"{context}.extracted_files must be sorted and unique")
    if _SCHEMA_REQUIRED_FILE[fares_schema] not in names:
        raise GTFSStorageError(f"{context} does not retain the required {fares_schema} fare file")


def _validate_feed_snapshot(
    directory: Path,
    *,
    expected_agency: str | None = None,
    expected_snapshot_version: str | None = None,
) -> FeedSnapshot:
    if directory.is_symlink() or not directory.is_dir():
        raise GTFSStorageError(f"GTFS feed snapshot is not a regular directory: {directory}")
    receipt_bytes = _regular_file_bytes(directory / "receipt.json", "GTFS receipt")
    receipt = _load_canonical_json(receipt_bytes, "GTFS receipt")
    _validate_receipt(receipt, "GTFS receipt")
    snapshot_version = _sha256(receipt_bytes)
    if expected_snapshot_version is not None and snapshot_version != expected_snapshot_version:
        raise GTFSStorageError("GTFS receipt digest does not match snapshot version")
    agency = _agency_name(receipt["agency"], "GTFS receipt agency")
    if expected_agency is not None and agency != expected_agency:
        raise GTFSStorageError("GTFS receipt agency does not match selected agency")

    zip_bytes = _regular_file_bytes(directory / "feed.zip", "exact GTFS ZIP")
    zip_record = receipt["zip"]
    assert isinstance(zip_record, dict)
    if len(zip_bytes) != zip_record["bytes"] or _sha256(zip_bytes) != zip_record["sha256"]:
        raise GTFSStorageError("exact GTFS ZIP does not match its receipt")
    fares_schema = receipt["fares_schema"]
    assert isinstance(fares_schema, str)
    derived_schema, extracted = _extract_consumed_fare_files(zip_bytes, fares_schema)
    if derived_schema != fares_schema:
        raise GTFSStorageError("GTFS ZIP schema does not match its receipt")

    receipt_files = receipt["extracted_files"]
    assert isinstance(receipt_files, list)
    expected_records = [
        {"bytes": len(raw), "name": name, "sha256": _sha256(raw)}
        for name, raw in sorted(extracted.items())
    ]
    if receipt_files != expected_records:
        raise GTFSStorageError("extracted GTFS file records do not match the exact ZIP")
    expected_entries = {"feed.zip", "receipt.json", *extracted}
    actual_entries = {path.name for path in directory.iterdir()}
    if actual_entries != expected_entries:
        raise GTFSStorageError("GTFS feed snapshot has missing or unexpected artifacts")
    for name, raw in extracted.items():
        retained = _regular_file_bytes(directory / name, f"retained GTFS file {name}")
        if retained != raw:
            raise GTFSStorageError(f"retained GTFS file {name} differs from the exact ZIP")

    assert isinstance(zip_record["sha256"], str)
    assert isinstance(zip_record["bytes"], int)
    assert isinstance(receipt["fares_schema"], str)
    assert isinstance(receipt["requested_url"], str)
    assert isinstance(receipt["final_url"], str)
    assert isinstance(receipt["fetched_at"], str)
    assert isinstance(receipt["http_status"], int)
    return FeedSnapshot(
        agency=agency,
        snapshot_version=snapshot_version,
        receipt_sha256=snapshot_version,
        zip_sha256=zip_record["sha256"],
        zip_bytes=zip_record["bytes"],
        fares_schema=receipt["fares_schema"],
        requested_url=receipt["requested_url"],
        final_url=receipt["final_url"],
        fetched_at=receipt["fetched_at"],
        http_status=receipt["http_status"],
        directory=directory,
    )


def _load_current_bytes(root: Path) -> tuple[bytes, dict[str, object]] | None:
    path = root / _CURRENT_NAME
    if not path.exists() and not path.is_symlink():
        return None
    raw = _regular_file_bytes(path, "GTFS current manifest")
    payload = _load_canonical_json(raw, "GTFS current manifest")
    _exact_fields(
        payload,
        {"feeds", "published_at", "schema", "set_version"},
        "GTFS current manifest",
    )
    if payload["schema"] != GTFS_CURRENT_SCHEMA:
        raise GTFSStorageError("GTFS current manifest has an unsupported schema")
    _validate_timestamp(payload["published_at"], "GTFS current manifest.published_at")
    if not isinstance(payload["feeds"], list) or not payload["feeds"]:
        raise GTFSStorageError("GTFS current manifest.feeds must be a non-empty list")
    if not isinstance(payload["set_version"], str) or not _SHA256.fullmatch(payload["set_version"]):
        raise GTFSStorageError("GTFS current manifest.set_version must be a SHA-256 digest")
    return raw, payload


def load_current_snapshot_set(root: Path | None = None) -> dict[str, FeedSnapshot] | None:
    """Validate and return the atomically selected immutable feed snapshot set.

    ``None`` means the repository still uses the pre-transactional directory
    layout. Once ``current.json`` exists, corrupt or incomplete pointer state
    fails closed and is never papered over with legacy mutable files.
    """
    selected_root = root if root is not None else GTFS_RAW_DIR
    loaded = _load_current_bytes(selected_root)
    if loaded is None:
        return None
    _, payload = loaded
    rows = payload["feeds"]
    assert isinstance(rows, list)
    snapshots: dict[str, FeedSnapshot] = {}
    agencies: list[str] = []
    for index, row in enumerate(rows):
        context = f"GTFS current manifest.feeds[{index}]"
        if not isinstance(row, dict):
            raise GTFSStorageError(f"{context} must be an object")
        _exact_fields(
            row,
            {"agency", "receipt_sha256", "snapshot_version", "zip_bytes", "zip_sha256"},
            context,
        )
        agency = _agency_name(row["agency"], f"{context}.agency")
        snapshot_version = row["snapshot_version"]
        if (
            not isinstance(snapshot_version, str)
            or not _SHA256.fullmatch(snapshot_version)
            or row["receipt_sha256"] != snapshot_version
            or not isinstance(row["zip_sha256"], str)
            or not _SHA256.fullmatch(row["zip_sha256"])
            or not isinstance(row["zip_bytes"], int)
            or isinstance(row["zip_bytes"], bool)
            or row["zip_bytes"] <= 0
        ):
            raise GTFSStorageError(f"{context} has invalid digests or byte count")
        if agency in snapshots:
            raise GTFSStorageError(f"GTFS current manifest contains duplicate agency {agency}")
        snapshot = _validate_feed_snapshot(
            selected_root / _SNAPSHOTS_NAME / agency / snapshot_version,
            expected_agency=agency,
            expected_snapshot_version=snapshot_version,
        )
        if snapshot.pointer() != row:
            raise GTFSStorageError(f"{context} does not match its immutable snapshot")
        snapshots[agency] = snapshot
        agencies.append(agency)
    if agencies != sorted(agencies):
        raise GTFSStorageError("GTFS current manifest feeds must be sorted by agency")
    pointers = [snapshots[agency].pointer() for agency in agencies]
    if payload["set_version"] != _selected_set_version(pointers):
        raise GTFSStorageError("GTFS current manifest set_version does not match selected feeds")
    return snapshots


def current_snapshot_set_version(root: Path | None = None) -> str | None:
    """Return the verified identity of the atomically selected GTFS feed set."""
    selected_root = root if root is not None else GTFS_RAW_DIR
    snapshots = load_current_snapshot_set(selected_root)
    if snapshots is None:
        return None
    return _selected_set_version([snapshots[agency].pointer() for agency in sorted(snapshots)])


def _cached_current_snapshot_set(root: Path) -> dict[str, FeedSnapshot] | None:
    loaded = _load_current_bytes(root)
    if loaded is None:
        return None
    raw, _ = loaded
    cache_key = (str(root.absolute()), _sha256(raw))
    with _CURRENT_CACHE_LOCK:
        cached = _CURRENT_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)
    snapshots = load_current_snapshot_set(root)
    assert snapshots is not None
    with _CURRENT_CACHE_LOCK:
        _CURRENT_CACHE.clear()
        _CURRENT_CACHE[cache_key] = dict(snapshots)
    return snapshots


def feed_snapshot_directory(agency: str, root: Path | None = None) -> Path:
    """Resolve an agency to its selected immutable snapshot or legacy directory.

    Legacy fallback is intentionally possible only before the first
    transactional ``current.json`` is published.
    """
    safe_agency = _agency_name(agency, "agency")
    selected_root = root if root is not None else GTFS_RAW_DIR
    current = _cached_current_snapshot_set(selected_root)
    if current is None:
        return selected_root / safe_agency
    selected = current.get(safe_agency)
    if selected is not None:
        return selected.directory
    raise FileNotFoundError(f"agency {safe_agency!r} is not selected by GTFS current.json")


@contextmanager
def _publication_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".fetch.lock"
    if lock_path.is_symlink():
        raise GTFSStorageError("GTFS publication lock must not be a symbolic link")
    with _FETCH_PROCESS_LOCK, lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write_current(root: Path, payload: Mapping[str, object]) -> None:
    raw = _canonical_json(payload)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".current.", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / _CURRENT_NAME)
        _fsync_directory(root)
    finally:
        if temporary.exists():
            temporary.unlink()


def _publish_transaction(
    *,
    root: Path,
    staged: Mapping[str, FeedSnapshot],
    configured_agencies: set[str],
    partial: bool,
) -> dict[str, FeedSnapshot]:
    with _publication_lock(root):
        existing = load_current_snapshot_set(root)
        if partial:
            if existing is None:
                raise GTFSStorageError(
                    "the first transactional GTFS fetch must include every configured feed"
                )
            if set(existing) != configured_agencies:
                raise GTFSStorageError(
                    "the current GTFS set does not match configured agencies; run a full fetch"
                )
            selected = dict(existing)
        else:
            selected = {}

        snapshots_root = root / _SNAPSHOTS_NAME
        _ensure_directory(snapshots_root, "GTFS snapshots root")
        _fsync_directory(root)
        for agency, staged_snapshot in sorted(staged.items()):
            agency_root = snapshots_root / agency
            _ensure_directory(agency_root, f"GTFS snapshots root for {agency}")
            _fsync_directory(snapshots_root)
            final = agency_root / staged_snapshot.snapshot_version
            if final.exists() or final.is_symlink():
                winner = _validate_feed_snapshot(
                    final,
                    expected_agency=agency,
                    expected_snapshot_version=staged_snapshot.snapshot_version,
                )
                if winner.pointer() != staged_snapshot.pointer():
                    raise GTFSStorageError("existing immutable GTFS snapshot conflicts with stage")
            else:
                os.rename(staged_snapshot.directory, final)
                _fsync_directory(agency_root)
                winner = _validate_feed_snapshot(
                    final,
                    expected_agency=agency,
                    expected_snapshot_version=staged_snapshot.snapshot_version,
                )
            selected[agency] = winner

        if set(selected) != configured_agencies:
            raise GTFSStorageError(
                "transaction does not provide a validated snapshot for every configured feed"
            )
        pointers = [selected[agency].pointer() for agency in sorted(selected)]
        manifest = {
            "feeds": pointers,
            "published_at": _utc_timestamp(),
            "schema": GTFS_CURRENT_SCHEMA,
            "set_version": _selected_set_version(pointers),
        }
        try:
            _atomic_write_current(root, manifest)
        except OSError:
            # `os.replace` is the commit point. A directory-fsync error can be
            # reported after that atomic replacement already became visible.
            # Treat an exactly validated committed manifest as success; only a
            # pre-commit failure is allowed to surface as "unchanged".
            committed = load_current_snapshot_set(root)
            if committed is None or {
                agency: item.pointer() for agency, item in committed.items()
            } != {agency: item.pointer() for agency, item in selected.items()}:
                raise
            return committed
        validated = load_current_snapshot_set(root)
        if validated is None:
            raise GTFSStorageError("published GTFS current manifest disappeared")
        return validated


def fetch_all(only: set[str] | None = None) -> bool:
    """Transactionally capture configured GTFS feeds.

    Every selected response is downloaded and validated in one hidden staging
    transaction. The exact ZIP, canonical fetch receipt, and only extracted
    files actually consumed by this codebase are then retained in immutable
    per-feed directories. A single atomic ``current.json`` update makes the
    complete configured set visible only after all selected feeds succeed.

    A failed request, unsafe ZIP, invalid fare schema, extraction error, or
    publication error returns ``False`` and leaves the selected current set
    unchanged. Immutable directories are never overwritten.
    """
    try:
        feeds, declared_no_feed = partition_gtfs_feeds()
    except GTFSStorageError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return False
    configured = {str(feed["agency"]) for feed in feeds}
    for entry in declared_no_feed:
        print(f"skip  {entry.agency}  no feed: {entry.reason}")

    if only is not None:
        no_feed_names = {entry.agency for entry in declared_no_feed}
        selected_no_feed = only & no_feed_names
        if selected_no_feed:
            print(
                "FAIL  selected agency has a declared no_feed_reason: "
                + ", ".join(sorted(selected_no_feed)),
                file=sys.stderr,
            )
            return False
        unknown = only - configured
        if unknown:
            print(
                "FAIL  unknown GTFS agency selection: " + ", ".join(sorted(unknown)),
                file=sys.stderr,
            )
            return False
        feeds = [feed for feed in feeds if feed["agency"] in only]
    if not feeds:
        print("FAIL  no GTFS feeds selected", file=sys.stderr)
        return False

    manifest = ingest.load_manifest()
    user_agent = manifest.get("user_agent")
    if not isinstance(user_agent, str) or not user_agent.strip():
        print("FAIL  manifest.user_agent must be a non-empty string", file=sys.stderr)
        return False

    try:
        _ensure_directory(GTFS_RAW_DIR, "GTFS raw root", parents=True)
    except GTFSStorageError as exc:
        print(f"FAIL  GTFS storage: {exc}", file=sys.stderr)
        return False
    transaction = Path(tempfile.mkdtemp(prefix=".transaction.", dir=GTFS_RAW_DIR))
    staged: dict[str, FeedSnapshot] = {}
    failures: list[tuple[str, str]] = []
    try:
        with httpx.Client(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=60,
        ) as client:
            for feed in feeds:
                agency = str(feed["agency"])
                try:
                    staged[agency] = _stage_feed(client, feed, transaction)
                    retained = [
                        path.name
                        for path in staged[agency].directory.iterdir()
                        if path.name in _CONSUMED_FARE_MEMBERS
                    ]
                    print(f"stage {agency}  {', '.join(sorted(retained))}")
                except (GTFSStorageError, httpx.HTTPError, OSError) as exc:
                    failures.append((agency, str(exc)))
                    print(f"FAIL  {agency}: {exc}", file=sys.stderr)

        if failures:
            print(
                f"\n{len(failures)} feed(s) failed; current GTFS set unchanged.",
                file=sys.stderr,
            )
            return False
        try:
            published = _publish_transaction(
                root=GTFS_RAW_DIR,
                staged=staged,
                configured_agencies=configured,
                partial=only is not None and set(staged) != configured,
            )
        except (GTFSStorageError, OSError) as exc:
            print(f"FAIL  GTFS publication: {exc}", file=sys.stderr)
            print("current GTFS set unchanged.", file=sys.stderr)
            return False
        for agency in sorted(staged):
            print(f"ok    {agency}  {published[agency].snapshot_version}")
        return True
    finally:
        if transaction.exists():
            shutil.rmtree(transaction)


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
    try:
        agency_dir = feed_snapshot_directory(agency)
    except FileNotFoundError:
        return []
    if (agency_dir / "fare_products.txt").exists():
        return _parse_fares_v2(agency_dir, agency)
    if (agency_dir / "fare_attributes.txt").exists():
        return _parse_fares_v1(agency_dir, agency)
    return []


def _parse_fares_v1(agency_dir: Path, agency: str) -> list[FeedFare]:
    fares = []
    for row in _read_csv(agency_dir / "fare_attributes.txt"):
        try:
            amount = Decimal(row["price"])
        except (InvalidOperation, KeyError):
            continue
        fare_id = row["fare_id"]
        fares.append(FeedFare(agency=agency, fare_id=fare_id, name=fare_id, amount=amount))
    return fares


def _parse_fares_v2(agency_dir: Path, agency: str) -> list[FeedFare]:
    fares = []
    for row in _read_csv(agency_dir / "fare_products.txt"):
        try:
            amount = Decimal(row["amount"])
        except (InvalidOperation, KeyError):
            continue
        fares.append(
            FeedFare(
                agency=agency,
                fare_id=row["fare_product_id"],
                name=row.get("fare_product_name") or row["fare_product_id"],
                amount=amount,
                rider_category=row.get("rider_category_id") or None,
            )
        )
    return fares


# ── cross-check ──────────────────────────────────────────────────────────────


def prose_amount_chunk_ids(
    agency: str, chunks: list[ingest.Chunk] | None = None
) -> dict[Decimal, list[str]]:
    """Each dollar amount in the agency's prose corpus → the chunks stating it.

    The chunk list is what `prose_fare_amounts` throws away, and it is what makes
    a coarse agreement auditable: one chunk is a specific match, twelve is a
    collision on a page dense with dollar figures, and the id says which chunk so
    a reader can go and look. See `CrossCheckRecord.prose_chunks` and issue #141.
    """
    chunks = chunks if chunks is not None else ingest.load_chunks()
    found: dict[Decimal, list[str]] = {}
    for chunk in chunks:
        if chunk.agency != agency:
            continue
        seen_in_chunk: set[Decimal] = set()
        for match in _DOLLAR_RE.finditer(chunk.text):
            try:
                amount = Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                continue
            seen_in_chunk.add(amount)
        for amount in seen_in_chunk:
            found.setdefault(amount, []).append(chunk.chunk_id)
    return found


def prose_amount_chunk_counts(
    agency: str, chunks: list[ingest.Chunk] | None = None
) -> dict[Decimal, int]:
    """Each dollar amount in the agency's prose corpus → how many chunks state it."""
    return {
        amount: len(chunk_ids)
        for amount, chunk_ids in prose_amount_chunk_ids(agency, chunks).items()
    }


def prose_fare_amounts(agency: str, chunks: list[ingest.Chunk] | None = None) -> set[Decimal]:
    """Every dollar amount mentioned anywhere in the agency's prose corpus."""
    return set(prose_amount_chunk_ids(agency, chunks))


def _compare_agency(
    agency: str,
    fares: list[FeedFare],
    chunks: list[ingest.Chunk],
) -> list[CrossCheckRecord]:
    """One agency's feed fares against its prose, with the evidence attached."""
    prose_chunks = prose_amount_chunk_ids(agency, chunks)
    free_chunks = sorted(
        c.chunk_id for c in chunks if c.agency == agency and _FREE_RE.search(c.text)
    )
    records: list[CrossCheckRecord] = []
    for fare in fares:
        matched = free_chunks if fare.amount == _ZERO else prose_chunks.get(fare.amount, [])
        records.append(
            CrossCheckRecord(
                agency,
                fare.fare_id,
                fare.name,
                str(fare.amount),
                "yes" if matched else "no",
                len(matched),
                sorted(matched)[:_PROSE_CHUNK_SAMPLE],
            )
        )
    return records


def cross_check(chunks: list[ingest.Chunk] | None = None) -> list[CrossCheckRecord]:
    """Compare every agency's snapshotted feed fares against its prose corpus.

    An agency with no configured feed, a manifest entry that declares why no
    feed could be configured, or a configured feed with no snapshot yet (`make
    gtfs-fetch` not run, or the fetch failed), gets one `no_feed` record so a
    report reader sees coverage without it reading as a failure. Each of those
    three carries its own `reason`; they are different facts about the agency.
    Never used to alter an answer — see module docstring.
    """
    chunks = chunks if chunks is not None else ingest.load_chunks()
    feeds, declared_no_feed = partition_gtfs_feeds()
    fed_agencies = {str(f["agency"]) for f in feeds} | {e.agency for e in declared_no_feed}
    corpus_agencies = {c.agency for c in chunks}
    records: list[CrossCheckRecord] = []

    for feed in feeds:
        agency = str(feed["agency"])
        fares = parse_fares(agency)
        if not fares:
            records.append(
                CrossCheckRecord(
                    agency,
                    "(no snapshot)",
                    "(no snapshot)",
                    None,
                    "no_feed",
                    reason="configured feed has no validated snapshot; run `make gtfs-fetch`",
                )
            )
            continue
        records.extend(_compare_agency(agency, fares, chunks))

    for entry in sorted(declared_no_feed, key=lambda e: e.agency):
        records.append(
            CrossCheckRecord(
                entry.agency,
                "(no feed configured)",
                "(no feed configured)",
                None,
                "no_feed",
                reason=entry.reason,
            )
        )

    for agency in sorted(corpus_agencies - fed_agencies):
        records.append(
            CrossCheckRecord(
                agency,
                "(no feed configured)",
                "(no feed configured)",
                None,
                "no_feed",
                reason="no feed configured; this agency has not been checked",
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
        if not fetch_all(only=set(sys.argv[2:]) or None):
            raise SystemExit(1)
    elif cmd == "check":
        records = cross_check()
        write_report(records)
        for r in records:
            strength = ""
            if r.prose_matches:
                where = ", ".join(r.prose_chunks or [])
                strength = f"  [{r.prose_matches} prose chunk(s): {where}]"
            elif r.prose_matches == 0:
                strength = "  [no prose chunk states this amount]"
            detail = f"  — {r.reason}" if r.feed_agrees == "no_feed" and r.reason else ""
            print(f"{r.feed_agrees:8} {r.agency:14} {r.name} ({r.feed_amount}){strength}{detail}")
        disagreements = [r for r in records if r.feed_agrees == "no"]
        covered = sorted({r.agency for r in records if r.feed_agrees != "no_feed"})
        agencies = sorted({r.agency for r in records})
        print(
            f"\ncoverage: {len(covered)} of {len(agencies)} corpus agencies have a "
            "snapshotted GTFS-Fares feed"
        )
        print(f"wrote {len(records)} record(s) -> {CROSS_CHECK_PATH}")
        # An agreement backed by one prose chunk is evidence; the same agreement
        # backed by a dozen is a collision. Say which this run produced rather
        # than letting a wall of "yes" read as corroboration (#141).
        agreed = [r for r in records if r.feed_agrees == "yes" and r.prose_matches is not None]
        weak = [r for r in agreed if r.prose_matches and r.prose_matches > 1]
        if agreed:
            print(
                f"{len(agreed)} agreement(s), of which {len(weak)} matched an amount that "
                "appears in more than one chunk of the same agency's prose — those prove the "
                "amount is published somewhere, not that this program's fare agrees."
            )
        if disagreements:
            print(f"{len(disagreements)} disagreement(s) found.", file=sys.stderr)
    else:
        raise SystemExit(f"unknown command: {cmd} (expected fetch|check)")


if __name__ == "__main__":
    main()
