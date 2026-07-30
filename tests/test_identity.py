"""Behavior-complete content and validated source-snapshot identities."""

from __future__ import annotations

import dataclasses
import hashlib
import re

import pytest

from assistant.identity import (
    CONTENT_SCHEMA,
    SNAPSHOT_SCHEMA,
    ContentIdentity,
    DocumentObservation,
    IdentityValidationError,
    SnapshotIdentity,
    build_content_identity,
    build_snapshot_identity,
    content_version,
    snapshot_version,
)
from assistant.ingest import Chunk
from tests.conftest import make_chunk

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _metadata(
    chunk: Chunk,
    raw: bytes,
    *,
    effective_format: str = "html",
    **overrides: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "doc_id": chunk.doc_id,
        "url": chunk.url,
        "final_url": chunk.url,
        "fetch_date": chunk.fetch_date,
        "http_status": 200,
        "format": effective_format,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    values.update(overrides)
    return values


def _observation(
    chunk: Chunk,
    *,
    raw: bytes = b"<main>Published fare policy</main>",
    effective_format: str = "html",
    **overrides: object,
) -> DocumentObservation:
    return DocumentObservation.from_metadata(
        _metadata(chunk, raw, effective_format=effective_format, **overrides),
        raw=raw,
        effective_format=effective_format,
    )


def _second_document() -> Chunk:
    return make_chunk(
        chunk_id="sbmtd-fares-passes#0",
        doc_id="sbmtd-fares-passes",
        agency="SBMTD",
        agency_full="Santa Barbara Metropolitan Transit District",
        doc_title="Fares & Passes",
        url="https://sbmtd.gov/fares-passes/",
        section="Cash fares",
        text="The published adult cash fare is $2.50.",
    )


def test_content_identity_is_full_width_deterministic_and_schema_framed():
    chunk = make_chunk()
    identity = build_content_identity([chunk])

    assert _HEX64.fullmatch(identity.content_version)
    assert identity.content_version == content_version([dataclasses.replace(chunk)])
    assert identity.content_version == (
        "e219aa735580544f97ee4f19ecf50ee0a072b30f6931cdbaee5c4636670984cd"
    )
    assert CONTENT_SCHEMA == "fare-assistant.content.v1"
    assert identity.chunk_count == identity.document_count == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("chunk_id", "mst-fares#changed"),
        ("doc_id", "mst-policy"),
        ("agency", "MST-Changed"),
        ("agency_full", "Monterey Transit"),
        ("doc_title", "Updated fares"),
        ("url", "https://mst.org/updated-fares/"),
        ("language", "es"),
        ("section", "Updated eligibility"),
        ("text", "A materially different published policy."),
    ],
)
def test_every_non_date_stored_chunk_field_changes_content_version(field, replacement):
    chunk = make_chunk()
    changed = dataclasses.replace(chunk, **{field: replacement})

    assert content_version([changed]) != content_version([chunk])


def test_fetch_date_is_observation_metadata_not_content():
    chunk = make_chunk()
    reverified = dataclasses.replace(chunk, fetch_date="2026-07-30")

    assert content_version([reverified]) == content_version([chunk])


def test_content_identity_covers_behavior_relevant_chunk_order():
    first = make_chunk()
    second = dataclasses.replace(
        first,
        chunk_id="mst-fares#1",
        section="Passes",
        text="A monthly pass is valid for 31 days.",
    )

    assert content_version([first, second]) != content_version([second, first])


def test_content_identity_rejects_duplicate_chunk_ids():
    chunk = make_chunk()
    duplicate = dataclasses.replace(chunk, section="Another section")

    with pytest.raises(IdentityValidationError, match="duplicate chunk_id"):
        content_version([chunk, duplicate])


def test_content_identity_rejects_inconsistent_metadata_within_one_document():
    chunk = make_chunk()
    inconsistent = dataclasses.replace(
        chunk,
        chunk_id="mst-fares#1",
        agency_full="A different agency name",
    )

    with pytest.raises(IdentityValidationError, match="inconsistent agency_full"):
        content_version([chunk, inconsistent])


@pytest.mark.parametrize(
    "bad_chunk",
    [
        dataclasses.replace(make_chunk(), text=" "),
        dataclasses.replace(make_chunk(), fetch_date="July 30"),
        dataclasses.replace(make_chunk(), url="javascript:alert(1)"),
        dataclasses.replace(make_chunk(), doc_id="MST Fares"),
    ],
)
def test_content_identity_strictly_validates_chunk_values(bad_chunk):
    with pytest.raises(IdentityValidationError):
        content_version([bad_chunk])


def test_content_identity_rejects_empty_or_non_chunk_input():
    with pytest.raises(IdentityValidationError, match="must not be empty"):
        content_version([])
    with pytest.raises(IdentityValidationError, match="must be a Chunk"):
        content_version([object()])  # type: ignore[list-item]


def test_observation_builder_proves_metadata_describes_raw_bytes():
    chunk = make_chunk()
    raw = b"<html>fare evidence</html>"
    observation = _observation(chunk, raw=raw)

    assert observation.doc_id == chunk.doc_id
    assert observation.chunk_fetch_date == chunk.fetch_date
    assert observation.requested_url == chunk.url
    assert observation.final_url == chunk.url
    assert observation.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert observation.raw_bytes == len(raw)


def test_observation_cannot_be_constructed_without_raw_metadata_proof():
    with pytest.raises(TypeError):
        DocumentObservation(  # type: ignore[call-arg]
            doc_id="mst-fares",
            chunk_fetch_date="2026-06-12",
            requested_url="https://mst.org/fares/",
            final_url="https://mst.org/fares/",
            http_status=200,
            effective_format="html",
            raw_sha256="0" * 64,
            raw_bytes=999_999,
        )


def test_observation_builder_rejects_digest_mismatch():
    chunk = make_chunk()
    raw = b"published bytes"
    metadata = _metadata(chunk, raw, sha256="0" * 64)

    with pytest.raises(IdentityValidationError, match="sha256.*does not match"):
        DocumentObservation.from_metadata(metadata, raw=raw, effective_format="html")


def test_observation_builder_rejects_byte_count_mismatch():
    chunk = make_chunk()
    raw = b"published bytes"
    metadata = _metadata(chunk, raw, bytes=len(raw) + 1)

    with pytest.raises(IdentityValidationError, match="bytes.*does not match"):
        DocumentObservation.from_metadata(metadata, raw=raw, effective_format="html")


def test_observation_builder_rejects_metadata_and_effective_format_mismatch():
    chunk = make_chunk()
    raw = b"%PDF-1.7 evidence"
    metadata = _metadata(chunk, raw, effective_format="html")

    with pytest.raises(IdentityValidationError, match="format does not match"):
        DocumentObservation.from_metadata(metadata, raw=raw, effective_format="pdf")


def test_observation_builder_requires_complete_metadata_and_bytes():
    chunk = make_chunk()
    raw = b"evidence"
    metadata = _metadata(chunk, raw)
    del metadata["final_url"]

    with pytest.raises(IdentityValidationError, match="missing required field"):
        DocumentObservation.from_metadata(metadata, raw=raw, effective_format="html")
    with pytest.raises(IdentityValidationError, match="raw must be bytes"):
        DocumentObservation.from_metadata(  # type: ignore[arg-type]
            _metadata(chunk, raw),
            raw="not bytes",
            effective_format="html",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"fetch_date": "not-a-date"},
        {"url": "/relative"},
        {"final_url": "file:///tmp/source"},
        {"http_status": 404},
        {"http_status": True},
        {"format": "docx"},
        {"sha256": "ABC"},
        {"bytes": "10"},
    ],
)
def test_observation_builder_strictly_validates_metadata(overrides):
    chunk = make_chunk()
    raw = b"source evidence"
    effective_format = str(overrides.get("format", "html"))

    with pytest.raises(IdentityValidationError):
        DocumentObservation.from_metadata(
            _metadata(chunk, raw, **overrides),
            raw=raw,
            effective_format=effective_format,
        )


def test_snapshot_identity_is_full_width_and_sorts_observations_by_document():
    mst = make_chunk()
    sbmtd = _second_document()
    mst_observation = _observation(mst, raw=b"MST")
    sbmtd_observation = _observation(sbmtd, raw=b"SBMTD")

    forward = build_snapshot_identity(
        [mst, sbmtd],
        [sbmtd_observation, mst_observation],
    )
    reverse_evidence = build_snapshot_identity(
        [mst, sbmtd],
        [mst_observation, sbmtd_observation],
    )

    assert _HEX64.fullmatch(forward.snapshot_version)
    assert forward.snapshot_version == reverse_evidence.snapshot_version
    assert forward.content_version == content_version([mst, sbmtd])
    assert [item.doc_id for item in forward.observations] == [
        "mst-fares",
        "sbmtd-fares-passes",
    ]
    assert forward.document_count == 2
    assert SNAPSHOT_SCHEMA == "fare-assistant.snapshot.v1"


def test_fetch_date_reverification_changes_snapshot_but_not_content():
    chunk = make_chunk()
    reverified = dataclasses.replace(chunk, fetch_date="2026-07-30")

    before = snapshot_version([chunk], [_observation(chunk)])
    after = snapshot_version([reverified], [_observation(reverified)])

    assert content_version([chunk]) == content_version([reverified])
    assert before != after


def test_raw_evidence_change_changes_snapshot_but_not_content():
    chunk = make_chunk()

    before = snapshot_version([chunk], [_observation(chunk, raw=b"first raw snapshot")])
    after = snapshot_version([chunk], [_observation(chunk, raw=b"second raw snapshot")])

    assert before != after
    assert content_version([chunk]) == content_version([chunk])


@pytest.mark.parametrize(
    ("changed_chunk", "observation", "message"),
    [
        (
            make_chunk(fetch_date="2026-07-30"),
            _observation(make_chunk()),
            "fetch date does not match",
        ),
        (
            make_chunk(url="https://mst.org/new-fares/"),
            _observation(make_chunk()),
            "requested URL does not match",
        ),
    ],
)
def test_snapshot_rejects_chunk_and_observation_mismatch(changed_chunk, observation, message):
    with pytest.raises(IdentityValidationError, match=message):
        snapshot_version([changed_chunk], [observation])


def test_snapshot_rejects_duplicate_document_observations():
    chunk = make_chunk()
    observation = _observation(chunk)

    with pytest.raises(IdentityValidationError, match="duplicate observation doc_id"):
        snapshot_version([chunk], [observation, observation])


def test_snapshot_rejects_missing_and_unexpected_document_observations():
    mst = make_chunk()
    sbmtd = _second_document()

    with pytest.raises(IdentityValidationError, match="missing observations"):
        snapshot_version([mst, sbmtd], [_observation(mst)])
    with pytest.raises(IdentityValidationError, match="unexpected observations"):
        snapshot_version([mst], [_observation(mst), _observation(sbmtd)])


def test_snapshot_rejects_non_observation_values():
    with pytest.raises(IdentityValidationError, match="every observation"):
        snapshot_version([make_chunk()], [object()])  # type: ignore[list-item]


def test_identity_dataclasses_reject_malformed_manual_construction():
    with pytest.raises(IdentityValidationError, match="content_version"):
        ContentIdentity(content_version="short", chunk_count=1, document_count=1)
    with pytest.raises(IdentityValidationError, match="document_count"):
        ContentIdentity(content_version="0" * 64, chunk_count=1, document_count=2)
    with pytest.raises(IdentityValidationError, match="sorted"):
        SnapshotIdentity(
            content_version="0" * 64,
            snapshot_version="1" * 64,
            observations=(
                _observation(_second_document()),
                _observation(make_chunk()),
            ),
        )
    with pytest.raises(IdentityValidationError, match="immutable tuple"):
        SnapshotIdentity(
            content_version="0" * 64,
            snapshot_version="1" * 64,
            observations=[_observation(make_chunk())],  # type: ignore[arg-type]
        )
    with pytest.raises(IdentityValidationError, match="every observation"):
        SnapshotIdentity(
            content_version="0" * 64,
            snapshot_version="1" * 64,
            observations=(object(),),  # type: ignore[arg-type]
        )
