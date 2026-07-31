"""Behavior-complete corpus and source-snapshot identities.

The legacy :func:`assistant.corpus.corpus_version` intentionally remains
separate.  This module provides full-width, schema-framed identities for the
next release contract:

* ``content_version`` covers the ordered, complete stored ``Chunk`` shape
  except ``fetch_date``.  Observation dates therefore do not masquerade as
  policy-content changes.
* ``snapshot_version`` covers that content identity plus validated raw-source
  observation evidence for every document.

Hash inputs are canonical JSON prefixed by an explicit schema identifier and a
NUL separator.  The schema prefix makes a future payload revision a different
identity domain even if its JSON happens to be byte-for-byte identical.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Self
from urllib.parse import urlsplit

from assistant.ingest import Chunk

CONTENT_SCHEMA = "fare-assistant.content.v1"
SNAPSHOT_SCHEMA = "fare-assistant.snapshot.v1"

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCUMENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CHUNK_FIELDS = tuple(field.name for field in dataclasses.fields(Chunk))
_CONTENT_CHUNK_FIELDS = tuple(name for name in _CHUNK_FIELDS if name != "fetch_date")
_DOCUMENT_CONSTANT_FIELDS = (
    "agency",
    "agency_full",
    "doc_title",
    "url",
    "language",
    "fetch_date",
)
_SUPPORTED_FORMATS = frozenset({"html", "pdf"})


class IdentityValidationError(ValueError):
    """An identity input is incomplete, inconsistent, or malformed."""


def _require_nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityValidationError(f"{field} must be a non-empty string")
    return value


def _require_document_id(value: object, field: str = "doc_id") -> str:
    document_id = _require_nonempty_string(value, field)
    if not _DOCUMENT_ID.fullmatch(document_id):
        raise IdentityValidationError(
            f"{field} must contain lowercase letters, digits, and single hyphens"
        )
    return document_id


def _require_iso_date(value: object, field: str) -> str:
    text = _require_nonempty_string(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise IdentityValidationError(f"{field} must be an ISO YYYY-MM-DD date") from exc
    if parsed.isoformat() != text:
        raise IdentityValidationError(f"{field} must be an ISO YYYY-MM-DD date")
    return text


def _require_http_url(value: object, field: str) -> str:
    url = _require_nonempty_string(value, field)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IdentityValidationError(f"{field} must be an absolute HTTP(S) URL")
    return url


def _require_sha256(value: object, field: str) -> str:
    digest = _require_nonempty_string(value, field)
    if not _HEX_SHA256.fullmatch(digest):
        raise IdentityValidationError(f"{field} must be a 64-character lowercase SHA-256")
    return digest


def _require_int(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise IdentityValidationError(f"{field} must be an integer >= {minimum}")
    return value


def _canonical_digest(schema: str, payload: object) -> str:
    """Hash schema-prefixed canonical JSON using full-width SHA-256."""
    schema_bytes = _require_nonempty_string(schema, "schema").encode("ascii")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IdentityValidationError("identity payload is not canonical-JSON compatible") from exc
    return hashlib.sha256(schema_bytes + b"\0" + encoded).hexdigest()


@dataclass(frozen=True, init=False)
class DocumentObservation:
    """Validated evidence for one fetched source document.

    Construction is intentionally restricted to :meth:`from_metadata`, which
    proves that the metadata digest and byte count describe the supplied raw
    bytes.  A caller cannot turn an unproven digest into snapshot evidence by
    directly instantiating this dataclass.
    """

    doc_id: str
    chunk_fetch_date: str
    requested_url: str
    final_url: str
    http_status: int
    effective_format: str
    raw_sha256: str
    raw_bytes: int

    @classmethod
    def from_metadata(
        cls,
        metadata: Mapping[str, object],
        *,
        raw: bytes,
        effective_format: str,
    ) -> Self:
        """Build evidence and verify metadata against the actual raw bytes."""
        if not isinstance(metadata, Mapping):
            raise IdentityValidationError("metadata must be a mapping")
        if not isinstance(raw, bytes):
            raise IdentityValidationError("raw must be bytes")
        if not raw:
            raise IdentityValidationError("raw source bytes must not be empty")

        required = {
            "doc_id",
            "url",
            "final_url",
            "fetch_date",
            "http_status",
            "format",
            "sha256",
            "bytes",
        }
        missing = sorted(required - metadata.keys())
        if missing:
            raise IdentityValidationError(
                "metadata is missing required field(s): " + ", ".join(missing)
            )

        metadata_format = _require_nonempty_string(metadata["format"], "metadata.format")
        effective = _require_nonempty_string(effective_format, "effective_format")
        if metadata_format != effective:
            raise IdentityValidationError(
                "metadata.format does not match the effective document format"
            )
        if effective not in _SUPPORTED_FORMATS:
            raise IdentityValidationError(
                f"effective_format must be one of {sorted(_SUPPORTED_FORMATS)}"
            )

        actual_sha256 = hashlib.sha256(raw).hexdigest()
        metadata_sha256 = _require_sha256(metadata["sha256"], "metadata.sha256")
        if metadata_sha256 != actual_sha256:
            raise IdentityValidationError("metadata.sha256 does not match the raw source bytes")

        metadata_bytes = _require_int(metadata["bytes"], "metadata.bytes", minimum=1)
        if metadata_bytes != len(raw):
            raise IdentityValidationError("metadata.bytes does not match the raw source bytes")

        status = _require_int(metadata["http_status"], "metadata.http_status", minimum=100)
        if status > 599:
            raise IdentityValidationError("metadata.http_status must be between 100 and 599")
        if not 200 <= status <= 299:
            raise IdentityValidationError(
                "metadata.http_status must describe a successful response"
            )

        values: dict[str, object] = {
            "doc_id": _require_document_id(metadata["doc_id"], "metadata.doc_id"),
            "chunk_fetch_date": _require_iso_date(metadata["fetch_date"], "metadata.fetch_date"),
            "requested_url": _require_http_url(metadata["url"], "metadata.url"),
            "final_url": _require_http_url(metadata["final_url"], "metadata.final_url"),
            "http_status": status,
            "effective_format": effective,
            "raw_sha256": actual_sha256,
            "raw_bytes": len(raw),
        }
        observation = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(observation, name, value)
        return observation

    def canonical_dict(self) -> dict[str, object]:
        """The exact observation shape committed to ``snapshot_version``."""
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ContentIdentity:
    """A validated content digest plus its cardinality provenance."""

    content_version: str
    chunk_count: int
    document_count: int

    def __post_init__(self) -> None:
        _require_sha256(self.content_version, "content_version")
        chunks = _require_int(self.chunk_count, "chunk_count", minimum=1)
        documents = _require_int(self.document_count, "document_count", minimum=1)
        if documents > chunks:
            raise IdentityValidationError("document_count cannot exceed chunk_count")


@dataclass(frozen=True)
class SnapshotIdentity:
    """A validated snapshot digest and its sorted source observations."""

    content_version: str
    snapshot_version: str
    observations: tuple[DocumentObservation, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.content_version, "content_version")
        _require_sha256(self.snapshot_version, "snapshot_version")
        if not isinstance(self.observations, tuple):
            raise IdentityValidationError("observations must be an immutable tuple")
        if not self.observations:
            raise IdentityValidationError("observations must not be empty")
        if any(not isinstance(item, DocumentObservation) for item in self.observations):
            raise IdentityValidationError("every observation must be a DocumentObservation")
        document_ids = [observation.doc_id for observation in self.observations]
        if document_ids != sorted(document_ids):
            raise IdentityValidationError("observations must be sorted by doc_id")
        if len(document_ids) != len(set(document_ids)):
            raise IdentityValidationError("observations contain duplicate doc_id values")

    @property
    def document_count(self) -> int:
        return len(self.observations)


def _validated_chunks(chunks: Iterable[Chunk]) -> tuple[Chunk, ...]:
    try:
        ordered = tuple(chunks)
    except TypeError as exc:
        raise IdentityValidationError("chunks must be an iterable of Chunk values") from exc
    if not ordered:
        raise IdentityValidationError("chunks must not be empty")

    seen_chunk_ids: set[str] = set()
    by_document: dict[str, list[Chunk]] = {}
    for ordinal, chunk in enumerate(ordered):
        if not isinstance(chunk, Chunk):
            raise IdentityValidationError(f"chunks[{ordinal}] must be a Chunk")
        for field_name in _CHUNK_FIELDS:
            _require_nonempty_string(
                getattr(chunk, field_name),
                f"chunks[{ordinal}].{field_name}",
            )
        _require_document_id(chunk.doc_id, f"chunks[{ordinal}].doc_id")
        _require_iso_date(chunk.fetch_date, f"chunks[{ordinal}].fetch_date")
        _require_http_url(chunk.url, f"chunks[{ordinal}].url")
        if chunk.chunk_id in seen_chunk_ids:
            raise IdentityValidationError(f"duplicate chunk_id: {chunk.chunk_id}")
        seen_chunk_ids.add(chunk.chunk_id)
        by_document.setdefault(chunk.doc_id, []).append(chunk)

    for document_id, document_chunks in by_document.items():
        first = document_chunks[0]
        for field_name in _DOCUMENT_CONSTANT_FIELDS:
            expected = getattr(first, field_name)
            if any(getattr(chunk, field_name) != expected for chunk in document_chunks[1:]):
                raise IdentityValidationError(
                    f"document {document_id!r} has inconsistent {field_name} values"
                )
    return ordered


def _content_payload(chunks: tuple[Chunk, ...]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for ordinal, chunk in enumerate(chunks):
        row: dict[str, object] = {"ordinal": ordinal}
        row.update({name: getattr(chunk, name) for name in _CONTENT_CHUNK_FIELDS})
        rows.append(row)
    return {"chunks": rows}


def build_content_identity(chunks: Iterable[Chunk]) -> ContentIdentity:
    """Validate chunks and return their ordered, behavior-complete identity."""
    ordered = _validated_chunks(chunks)
    version = _canonical_digest(CONTENT_SCHEMA, _content_payload(ordered))
    return ContentIdentity(
        content_version=version,
        chunk_count=len(ordered),
        document_count=len({chunk.doc_id for chunk in ordered}),
    )


def content_version(chunks: Iterable[Chunk]) -> str:
    """Return the full SHA-256 content identity."""
    return build_content_identity(chunks).content_version


def build_snapshot_identity(
    chunks: Iterable[Chunk],
    observations: Iterable[DocumentObservation],
) -> SnapshotIdentity:
    """Validate content/evidence agreement and return the snapshot identity."""
    ordered_chunks = _validated_chunks(chunks)
    try:
        supplied_observations = tuple(observations)
    except TypeError as exc:
        raise IdentityValidationError(
            "observations must be an iterable of DocumentObservation values"
        ) from exc
    if not supplied_observations:
        raise IdentityValidationError("observations must not be empty")
    if any(not isinstance(item, DocumentObservation) for item in supplied_observations):
        raise IdentityValidationError("every observation must be a DocumentObservation")

    observation_by_id: dict[str, DocumentObservation] = {}
    for observation in supplied_observations:
        if observation.doc_id in observation_by_id:
            raise IdentityValidationError(f"duplicate observation doc_id: {observation.doc_id}")
        observation_by_id[observation.doc_id] = observation

    chunks_by_id: dict[str, list[Chunk]] = {}
    for chunk in ordered_chunks:
        chunks_by_id.setdefault(chunk.doc_id, []).append(chunk)
    chunk_doc_ids = set(chunks_by_id)
    observation_doc_ids = set(observation_by_id)
    if chunk_doc_ids != observation_doc_ids:
        missing = sorted(chunk_doc_ids - observation_doc_ids)
        unexpected = sorted(observation_doc_ids - chunk_doc_ids)
        details = []
        if missing:
            details.append("missing observations: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected observations: " + ", ".join(unexpected))
        raise IdentityValidationError("; ".join(details))

    sorted_observations = tuple(
        observation_by_id[document_id] for document_id in sorted(observation_by_id)
    )
    for observation in sorted_observations:
        document_chunks = chunks_by_id[observation.doc_id]
        chunk = document_chunks[0]
        if observation.chunk_fetch_date != chunk.fetch_date:
            raise IdentityValidationError(
                f"observation fetch date does not match chunks for {observation.doc_id}"
            )
        if observation.requested_url != chunk.url:
            raise IdentityValidationError(
                f"observation requested URL does not match chunks for {observation.doc_id}"
            )

    content = build_content_identity(ordered_chunks)
    payload = {
        "content_version": content.content_version,
        "observations": [observation.canonical_dict() for observation in sorted_observations],
    }
    snapshot = _canonical_digest(SNAPSHOT_SCHEMA, payload)
    return SnapshotIdentity(
        content_version=content.content_version,
        snapshot_version=snapshot,
        observations=sorted_observations,
    )


def snapshot_version(
    chunks: Iterable[Chunk],
    observations: Iterable[DocumentObservation],
) -> str:
    """Return the full SHA-256 source-snapshot identity."""
    return build_snapshot_identity(chunks, observations).snapshot_version
