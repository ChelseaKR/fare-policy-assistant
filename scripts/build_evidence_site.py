#!/usr/bin/env python3
"""Export, validate, and render sanitized public promotion evidence.

``export`` is the only command that may read private evaluation summary/result
receipts. It first applies the shared promotion-evidence verifier, then writes a
closed canonical manifest containing only that verifier's sanitized view.

``render`` accepts only the canonical public manifest. It cannot read raw eval
results and atomically creates a static site containing a summary, a
safe-ID-only report, and a machine-readable release receipt.

``compare-runtime`` checks a downloaded rider ``/version`` response against
every field in the attested immutable runtime tuple. It performs no HTTP itself.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Never
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from assistant.promotion_evidence import (  # noqa: E402
    PromotionEvidenceError,
    verify_promotion_evidence,
)
from assistant.release_attestation import (  # noqa: E402
    PromotionAttestationError,
    RuntimeRelease,
)
from assistant.release_identity import (  # noqa: E402
    ReleaseIdentityError,
    build_release_identity,
)

PUBLIC_EVIDENCE_SCHEMA: Final = "fare-assistant.public-evidence.v1"
PUBLIC_RELEASE_SCHEMA: Final = "fare-assistant.public-release.v1"

MAX_PUBLIC_MANIFEST_BYTES: Final = 4 * 1024 * 1024
MAX_TEMPLATE_BYTES: Final = 256 * 1024
MAX_VERSION_RESPONSE_BYTES: Final = 256 * 1024
MAX_HISTORY_SVG_BYTES: Final = 5 * 1024 * 1024
MAX_CNAME_BYTES: Final = 1024

_READ_CHUNK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CORPUS_VERSION = re.compile(r"^[0-9a-f]{12}$")
_FUNCTION_VERSION = re.compile(r"^[1-9][0-9]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CASE_ID = _RUN_ID
_RFC3339_Z = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
#: The address this site answers on. A canonical link and a sitemap both publish
#: absolute addresses, so this cannot be inferred from the output directory; it is
#: written down, and `render_evidence_site` refuses to publish a CNAME naming a
#: different host, so the two can never quietly disagree.
SITE_ORIGIN = "https://evals.chelseakr.com"

#: The pages offered for indexing, in the order the sitemap lists them. The other
#: three published files -- the evidence manifest, the release receipt and the
#: history SVG -- are data a reader reaches through these pages, not pages.
INDEXABLE_PAGES: tuple[str, ...] = ("index.html", "report.html")

_PLACEHOLDER = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
_TEMPLATE_FIELDS = frozenset(
    {
        "CASE_COUNT",
        "FUNCTION_VERSION",
        "PROMOTED_AT",
        "RELEASE_VERSION",
        "RUN_DATE",
        "RUN_AT",
        "SOURCE_REVISION",
        "STATUS_CLASS",
        "STATUS_DETAIL",
        "STATUS_LABEL",
        "SUITE_ROWS",
        "TOTAL_SCORE",
        "TREND_SECTION",
    }
)
_RUNTIME_FIELDS = (
    "source_revision",
    "config_version",
    "content_version",
    "snapshot_version",
    "release_version",
    "corpus_version",
    "artifact_code_sha256",
    "function_version",
)
_MANIFEST_FIELDS = frozenset({"schema", "evidence", "manifest_version"})
_EVIDENCE_REQUIRED_FIELDS = frozenset(
    {
        "status",
        "warnings",
        "fresh",
        "age_seconds",
        "max_age_seconds",
        "run_id",
        "run_at",
        "promoted_at",
        "runtime_release",
        "run_context_version",
        "evaluation_attestation_version",
        "summary_sha256",
        "results_sha256",
        "promotion_sha256",
        "total",
        "suites",
        "cases",
    }
)
_EVIDENCE_OPTIONAL_FIELDS = frozenset({"served_models"})
_RUNTIME_FIELD_SET = frozenset(_RUNTIME_FIELDS)
_SCORE_FIELDS = frozenset({"passed", "total", "pass_rate"})
_SUITE_FIELDS = frozenset({"name", *_SCORE_FIELDS})
_MODEL_FIELDS = frozenset({"answer", "judge"})
_CASE_REQUIRED_FIELDS = frozenset({"case_id", "suite", "passed"})
_CASE_OPTIONAL_FIELDS = frozenset(
    {"run_context_version", "case_semantics_version", "served_models"}
)


class EvidenceSiteError(ValueError):
    """A public evidence input or output is unsafe, malformed, or inconsistent."""


def _fail(message: str) -> Never:
    raise EvidenceSiteError(message)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise EvidenceSiteError("value is not canonical-JSON compatible") from exc


def _manifest_version(evidence: Mapping[str, object]) -> str:
    payload = _canonical_bytes(dict(evidence))[:-1]
    return hashlib.sha256(PUBLIC_EVIDENCE_SCHEMA.encode("ascii") + b"\0" + payload).hexdigest()


def _fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _lstat_regular(path: Path, *, limit: int, context: str) -> os.stat_result:
    """Pre-open checks: a real Path, a regular non-symlink file, within budget.

    Split out of `_read_regular` for CQ-05 (max-complexity 10). Order and
    messages are unchanged; the caller still opens with O_NOFOLLOW and
    re-verifies the fingerprint, so this is a cheap early reject, not the
    security boundary on its own.
    """

    if not isinstance(path, Path):
        _fail(f"{context} path must be a pathlib.Path")
    try:
        before = path.lstat()
    except OSError as exc:
        raise EvidenceSiteError(f"{context} is missing or unreadable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"{context} must be a regular non-symlink file")
    if before.st_size > limit:
        _fail(f"{context} exceeds its {limit}-byte limit")
    return before


def _read_bounded(descriptor: int, *, limit: int, context: str) -> bytes:
    """Read a descriptor to EOF, refusing to buffer more than `limit` bytes.

    Reads one byte past the limit deliberately, so a file that grew past the
    budget between lstat and read is caught rather than silently truncated.
    """

    chunks: list[bytes] = []
    consumed = 0
    while True:
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, limit - consumed + 1))
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > limit:
            _fail(f"{context} exceeds its {limit}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_regular(path: Path, *, limit: int, context: str) -> bytes:
    before = _lstat_regular(path, limit=limit, context=context)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceSiteError(f"{context} could not be opened safely") from exc
    payload = b""
    opened: os.stat_result | None = None
    after: os.stat_result | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != _fingerprint(before):
            _fail(f"{context} changed while it was opened")
        payload = _read_bounded(descriptor, limit=limit, context=context)
        after = os.fstat(descriptor)
    except EvidenceSiteError:
        raise
    except OSError as exc:
        raise EvidenceSiteError(f"{context} could not be read completely") from exc
    finally:
        os.close(descriptor)
    assert opened is not None
    assert after is not None
    try:
        final_path = path.lstat()
    except OSError as exc:
        raise EvidenceSiteError(f"{context} changed while it was read") from exc
    if _fingerprint(opened) != _fingerprint(after) or _fingerprint(opened) != _fingerprint(
        final_path
    ):
        _fail(f"{context} changed while it was read")
    if len(payload) != opened.st_size:
        _fail(f"{context} changed while it was read")
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate object key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    _fail("JSON contains a non-finite numeric value")


def _parse_json(data: bytes, *, context: str) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceSiteError(f"{context} must be valid UTF-8 JSON") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except EvidenceSiteError:
        raise
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise EvidenceSiteError(f"{context} must contain valid JSON") from exc


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail(f"{context} must be a JSON object")
    return value


def _exact_fields(
    value: object,
    expected: frozenset[str],
    context: str,
    *,
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    actual = set(mapping)
    if not set(expected) <= actual or actual - set(expected) - set(optional):
        _fail(f"{context} has an invalid field set")
    return mapping


def _sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail(f"{context} must be a lowercase SHA-256")
    return value


def _timestamp(value: object, context: str) -> str:
    if not isinstance(value, str) or not _RFC3339_Z.fullmatch(value):
        _fail(f"{context} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceSiteError(f"{context} must be an RFC3339 UTC timestamp ending in Z") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(f"{context} must use UTC")
    return value


def _safe_text(value: object, context: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _fail(f"{context} is not a safe identifier")
    return value


def _safe_label(value: object, context: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(f"{context} must be a safe, trimmed string")
    return value


def _count(value: object, context: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        _fail(f"{context} must be an integer of at least {minimum}")
    return value


def _score(
    value: object,
    context: str,
    *,
    name: str | None = None,
) -> tuple[int, int]:
    fields = _SUITE_FIELDS if name is not None else _SCORE_FIELDS
    score = _exact_fields(value, fields, context)
    if name is not None and score["name"] != name:
        _fail(f"{context}.name is inconsistent")
    passed = _count(score["passed"], f"{context}.passed")
    total = _count(score["total"], f"{context}.total", positive=True)
    if passed > total:
        _fail(f"{context}.passed exceeds total")
    rate = score["pass_rate"]
    expected_rate = round(100 * passed / total, 1)
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or float(rate) != expected_rate:
        _fail(f"{context}.pass_rate is inconsistent")
    return passed, total


def _model_set(value: object, context: str) -> dict[str, tuple[str, ...]]:
    models = _exact_fields(value, _MODEL_FIELDS, context)
    result: dict[str, tuple[str, ...]] = {}
    for kind in ("answer", "judge"):
        raw = models[kind]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            _fail(f"{context}.{kind} must be an array")
        normalized = tuple(_safe_label(item, f"{context}.{kind}", maximum=256) for item in raw)
        if normalized != tuple(sorted(set(normalized))):
            _fail(f"{context}.{kind} must be sorted and unique")
        result[kind] = normalized
    return result


def _runtime_release(value: object) -> RuntimeRelease:
    runtime = _exact_fields(value, _RUNTIME_FIELD_SET, "evidence.runtime_release")
    try:
        release = RuntimeRelease(**{field: runtime[field] for field in _RUNTIME_FIELDS})  # type: ignore[arg-type]
        deterministic = build_release_identity(
            release.source_revision,
            release.config_version,
            content_version=release.content_version,
            snapshot_version=release.snapshot_version,
        )
    except (PromotionAttestationError, ReleaseIdentityError, TypeError, ValueError) as exc:
        raise EvidenceSiteError("evidence.runtime_release is invalid") from exc
    if not hmac.compare_digest(release.release_version, deterministic.release_version):
        _fail("evidence.runtime_release.release_version is inconsistent")
    return release


def _validate_freshness(evidence: Mapping[str, object]) -> None:
    """The freshness triple (status, fresh, warnings) plus the age/budget pair.

    Split out of `validate_public_manifest` for CQ-05 (max-complexity 10); the
    order of the checks, and every message they raise, is unchanged.
    """

    status = evidence["status"]
    warnings = evidence["warnings"]
    fresh = evidence["fresh"]
    if status not in {"verified", "warning"} or type(fresh) is not bool:
        _fail("manifest.evidence freshness status is invalid")
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        _fail("manifest.evidence.warnings must be a string array")
    if status == "verified":
        if fresh is not True or warnings != []:
            _fail("verified evidence must be fresh and warning-free")
    elif fresh is not False or warnings != ["evaluation.stale"]:
        _fail("warning evidence must carry only evaluation.stale")
    age = _count(evidence["age_seconds"], "manifest.evidence.age_seconds")
    budget = _count(
        evidence["max_age_seconds"],
        "manifest.evidence.max_age_seconds",
        positive=True,
    )
    if (status == "verified" and age > budget) or (status == "warning" and age <= budget):
        _fail("manifest.evidence freshness age is inconsistent")


@dataclass(frozen=True)
class _CaseTally:
    """What the per-case pass counts as it walks `manifest.evidence.cases`."""

    count: int
    counts_by_suite: dict[str, list[int]]
    answer_models: tuple[str, ...]
    judge_models: tuple[str, ...]
    model_provenance_count: int


def _validate_case(
    raw_case: object,
    index: int,
    *,
    context_version: str,
    seen_cases: set[str],
    counts_by_suite: dict[str, list[int]],
) -> dict[str, tuple[str, ...]] | None:
    """One case entry. Returns its served-model sets, or None when it declares none."""

    case = _exact_fields(
        raw_case,
        _CASE_REQUIRED_FIELDS,
        f"manifest.evidence.cases[{index}]",
        optional=_CASE_OPTIONAL_FIELDS,
    )
    case_id = _safe_text(
        case["case_id"],
        f"manifest.evidence.cases[{index}].case_id",
        _CASE_ID,
    )
    if case_id in seen_cases:
        _fail("manifest.evidence.cases contains duplicate case_id")
    seen_cases.add(case_id)
    suite = _safe_label(
        case["suite"],
        f"manifest.evidence.cases[{index}].suite",
        maximum=128,
    )
    passed = case["passed"]
    if type(passed) is not bool:
        _fail(f"manifest.evidence.cases[{index}].passed must be a boolean")
    counts = counts_by_suite.setdefault(suite, [0, 0])
    counts[1] += 1
    counts[0] += int(passed)
    if "run_context_version" in case:
        candidate_context = _sha256(
            case["run_context_version"],
            f"manifest.evidence.cases[{index}].run_context_version",
        )
        if not hmac.compare_digest(candidate_context, context_version):
            _fail(f"manifest.evidence.cases[{index}] has the wrong run context")
    if "case_semantics_version" in case:
        _sha256(
            case["case_semantics_version"],
            f"manifest.evidence.cases[{index}].case_semantics_version",
        )
    if "served_models" not in case:
        return None
    models = _model_set(
        case["served_models"],
        f"manifest.evidence.cases[{index}].served_models",
    )
    return models


def _validate_cases(evidence: Mapping[str, object], context_version: str) -> _CaseTally:
    """Every case entry, and the per-suite tallies the summary is checked against."""

    raw_cases = evidence["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        _fail("manifest.evidence.cases must be a nonempty array")
    seen_cases: set[str] = set()
    counts_by_suite: dict[str, list[int]] = {}
    answer_models: set[str] = set()
    judge_models: set[str] = set()
    model_provenance_count = 0
    for index, raw_case in enumerate(raw_cases):
        models = _validate_case(
            raw_case,
            index,
            context_version=context_version,
            seen_cases=seen_cases,
            counts_by_suite=counts_by_suite,
        )
        if models is not None:
            model_provenance_count += 1
            answer_models.update(models["answer"])
            judge_models.update(models["judge"])
    if model_provenance_count not in {0, len(raw_cases)}:
        _fail("case served-model provenance must be present for all cases or none")
    return _CaseTally(
        count=len(raw_cases),
        counts_by_suite=counts_by_suite,
        answer_models=tuple(sorted(answer_models)),
        judge_models=tuple(sorted(judge_models)),
        model_provenance_count=model_provenance_count,
    )


def _validate_totals_and_suites(evidence: Mapping[str, object], tally: _CaseTally) -> None:
    """`total` and `suites` must be exactly what the case list adds up to."""

    counts_by_suite = tally.counts_by_suite
    expected_total = (
        sum(counts[0] for counts in counts_by_suite.values()),
        sum(counts[1] for counts in counts_by_suite.values()),
    )
    if _score(evidence["total"], "manifest.evidence.total") != expected_total:
        _fail("manifest.evidence.total does not match cases")
    raw_suites = evidence["suites"]
    if not isinstance(raw_suites, list) or len(raw_suites) != len(counts_by_suite):
        _fail("manifest.evidence.suites does not match cases")
    observed_names: list[str] = []
    for index, raw_suite in enumerate(raw_suites):
        suite_mapping = _mapping(raw_suite, f"manifest.evidence.suites[{index}]")
        name = _safe_label(
            suite_mapping.get("name"),
            f"manifest.evidence.suites[{index}].name",
            maximum=128,
        )
        observed_names.append(name)
        if name not in counts_by_suite or _score(
            raw_suite,
            f"manifest.evidence.suites[{index}]",
            name=name,
        ) != tuple(counts_by_suite[name]):
            _fail(f"manifest.evidence.suites[{index}] does not match cases")
    if observed_names != sorted(set(observed_names)):
        _fail("manifest.evidence.suites must be sorted and unique")


def _validate_summary_served_models(evidence: Mapping[str, object], tally: _CaseTally) -> None:
    """Summary served models are all-or-nothing with the per-case ones, and must agree."""

    if "served_models" in evidence:
        if tally.model_provenance_count != tally.count:
            _fail("summary served models require per-case served-model provenance")
        models = _model_set(evidence["served_models"], "manifest.evidence.served_models")
        if models["answer"] != tally.answer_models or models["judge"] != tally.judge_models:
            _fail("manifest.evidence.served_models does not match cases")
    elif tally.model_provenance_count:
        _fail("per-case served-model provenance requires summary served models")


def validate_public_manifest(value: object) -> dict[str, object]:
    """Validate and return a plain, closed public-evidence manifest."""

    manifest = _exact_fields(value, _MANIFEST_FIELDS, "manifest")
    if manifest["schema"] != PUBLIC_EVIDENCE_SCHEMA:
        _fail("manifest.schema is unsupported")
    evidence = _exact_fields(
        manifest["evidence"],
        _EVIDENCE_REQUIRED_FIELDS,
        "manifest.evidence",
        optional=_EVIDENCE_OPTIONAL_FIELDS,
    )
    claimed_version = _sha256(manifest["manifest_version"], "manifest.manifest_version")
    expected_version = _manifest_version(evidence)
    if not hmac.compare_digest(claimed_version, expected_version):
        _fail("manifest.manifest_version is inconsistent")

    _validate_freshness(evidence)

    run_id = _safe_text(evidence["run_id"], "manifest.evidence.run_id", _RUN_ID)
    _timestamp(evidence["run_at"], "manifest.evidence.run_at")
    _timestamp(evidence["promoted_at"], "manifest.evidence.promoted_at")
    runtime = _runtime_release(evidence["runtime_release"])
    context_version = _sha256(
        evidence["run_context_version"],
        "manifest.evidence.run_context_version",
    )
    for field in (
        "evaluation_attestation_version",
        "summary_sha256",
        "results_sha256",
        "promotion_sha256",
    ):
        _sha256(evidence[field], f"manifest.evidence.{field}")

    tally = _validate_cases(evidence, context_version)
    _validate_totals_and_suites(evidence, tally)
    _validate_summary_served_models(evidence, tally)

    # Keep names live for type/narrowing checks and make accidental deletion of
    # their validation above visible to coverage.
    assert run_id
    assert runtime.function_version
    return {
        "schema": PUBLIC_EVIDENCE_SCHEMA,
        "evidence": json.loads(_canonical_bytes(dict(evidence))),
        "manifest_version": claimed_version,
    }


def _verification_time(clock: Callable[[], datetime] | None) -> datetime:
    selected = _utc_now if clock is None else clock
    if not callable(selected):
        _fail("verification clock must be callable")
    try:
        now = selected()
    except Exception as exc:
        raise EvidenceSiteError("verification clock failed") from exc
    if not isinstance(now, datetime) or now.tzinfo is None:
        _fail("verification clock must return a timezone-aware datetime")
    try:
        return now.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise EvidenceSiteError("verification clock returned an invalid datetime") from exc


def require_current_public_evidence(
    manifest: Mapping[str, object],
    *,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Reject evidence that is stale or future-dated at consumption time.

    ``age_seconds`` records the export-time observation and remains part of the
    canonical manifest identity. Publication consumers independently recompute
    age from ``run_at`` so replaying an old, once-fresh manifest cannot preserve
    its original ``verified`` claim.
    """

    evidence = _mapping(manifest["evidence"], "manifest.evidence")
    if evidence["status"] != "verified" or evidence["fresh"] is not True:
        _fail("public evidence was already stale when it was exported")
    now = _verification_time(clock)
    run_at = datetime.fromisoformat(
        _timestamp(evidence["run_at"], "manifest.evidence.run_at")[:-1] + "+00:00"
    )
    promoted_at = datetime.fromisoformat(
        _timestamp(evidence["promoted_at"], "manifest.evidence.promoted_at")[:-1] + "+00:00"
    )
    if run_at > now:
        _fail("public evidence run time is in the future")
    if promoted_at > now:
        _fail("public evidence promotion time is in the future")
    budget_seconds = _count(
        evidence["max_age_seconds"],
        "manifest.evidence.max_age_seconds",
        positive=True,
    )
    try:
        current_age = now - run_at
    except (OverflowError, ValueError) as exc:
        raise EvidenceSiteError("public evidence age could not be computed") from exc
    if current_age > timedelta(seconds=budget_seconds):
        _fail("public evidence is stale at verification time")


def load_public_manifest(path: Path) -> dict[str, object]:
    payload = _read_regular(
        path,
        limit=MAX_PUBLIC_MANIFEST_BYTES,
        context="public evidence manifest",
    )
    value = _parse_json(payload, context="public evidence manifest")
    manifest = validate_public_manifest(value)
    if not hmac.compare_digest(payload, _canonical_bytes(manifest)):
        _fail("public evidence manifest bytes are not canonical")
    return manifest


def _atomic_write(path: Path, payload: bytes) -> Path:
    if path.is_symlink():
        _fail(f"refusing to replace output symlink: {path}")
    if path.exists() and not path.is_file():
        _fail(f"output must be a regular file path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        _fail("output parent must be a regular directory")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise EvidenceSiteError(f"could not write output: {path}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def export_public_evidence(
    *,
    summary_path: Path,
    results_path: Path,
    promotion_path: Path,
    output_path: Path,
    freshness_budget: timedelta,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Verify private receipts and atomically export one canonical public manifest."""

    evidence = verify_promotion_evidence(
        summary_path=summary_path,
        results_path=results_path,
        promotion_path=promotion_path,
        freshness_budget=freshness_budget,
        clock=clock,
    ).as_dict()
    manifest = validate_public_manifest(
        {
            "schema": PUBLIC_EVIDENCE_SCHEMA,
            "evidence": evidence,
            "manifest_version": _manifest_version(evidence),
        }
    )
    _atomic_write(output_path, _canonical_bytes(manifest))
    return manifest


def _template_html(template: bytes, evidence: Mapping[str, object], *, trend: bool) -> bytes:
    try:
        source = template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceSiteError("index template must be UTF-8") from exc
    identifiers = _PLACEHOLDER.findall(source)
    if set(identifiers) != set(_TEMPLATE_FIELDS):
        _fail("index template placeholders are incomplete or unexpected")
    runtime = _mapping(evidence["runtime_release"], "evidence.runtime_release")
    total = _mapping(evidence["total"], "evidence.total")
    suites = evidence["suites"]
    assert isinstance(suites, list)
    rows = []
    for suite in suites:
        entry = _mapping(suite, "evidence suite")
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(str(entry["name"]))}</th>'
            f"<td>{entry['passed']}/{entry['total']}</td>"
            f"<td>{entry['pass_rate']:.1f}%</td>"
            "</tr>"
        )
    warning = evidence["status"] == "warning"
    replacements = {
        "CASE_COUNT": str(total["total"]),
        "FUNCTION_VERSION": html.escape(str(runtime["function_version"])),
        "PROMOTED_AT": html.escape(str(evidence["promoted_at"])),
        "RELEASE_VERSION": html.escape(str(runtime["release_version"])),
        "RUN_AT": html.escape(str(evidence["run_at"])),
        "RUN_DATE": html.escape(str(evidence["run_at"])[:10]),
        "SOURCE_REVISION": html.escape(str(runtime["source_revision"])),
        "STATUS_CLASS": "warning" if warning else "verified",
        "STATUS_DETAIL": (
            "The receipt is authentic but older than the publication freshness budget."
            if warning
            else "The receipt is authentic and within the publication freshness budget."
        ),
        "STATUS_LABEL": "Verified with freshness warning" if warning else "Verified",
        "SUITE_ROWS": "".join(rows),
        "TOTAL_SCORE": f"{total['passed']}/{total['total']} ({total['pass_rate']:.1f}%)",
        "TREND_SECTION": (
            '<section class="card" aria-labelledby="trend-heading">'
            '<h2 id="trend-heading">Evaluation history</h2>'
            '<img src="eval-history.svg" '
            'alt="Historical evaluation pass rates by recorded run.">'
            "</section>"
            if trend
            else ""
        ),
    }

    def replace(match: re.Match[str]) -> str:
        return replacements[match.group(1)]

    return _PLACEHOLDER.sub(replace, source).encode("utf-8")


def _report_html(evidence: Mapping[str, object]) -> bytes:
    runtime = _mapping(evidence["runtime_release"], "evidence.runtime_release")
    cases = evidence["cases"]
    suites = evidence["suites"]
    assert isinstance(cases, list)
    assert isinstance(suites, list)
    suite_rows = "".join(
        "<tr>"
        f'<th scope="row">{html.escape(str(_mapping(item, "suite")["name"]))}</th>'
        f"<td>{_mapping(item, 'suite')['passed']}/{_mapping(item, 'suite')['total']}</td>"
        f"<td>{_mapping(item, 'suite')['pass_rate']:.1f}%</td>"
        "</tr>"
        for item in suites
    )
    case_rows = "".join(
        "<tr>"
        f'<th scope="row">{html.escape(str(_mapping(item, "case")["case_id"]))}</th>'
        f"<td>{html.escape(str(_mapping(item, 'case')['suite']))}</td>"
        f"<td>{'Pass' if _mapping(item, 'case')['passed'] is True else 'Fail'}</td>"
        "</tr>"
        for item in cases
    )
    report_url = _page_url("report.html")
    report_description = _report_description(str(evidence["run_at"])[:10])
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_REPORT_TITLE}</title>
<meta name="description" content="{report_description}">
<link rel="canonical" href="{report_url}">
{_social_meta(title=_REPORT_TITLE, description=report_description, url=report_url)}
<style>
body {{ margin: 0; color: #17201b; background: #f7faf8; font: 1rem/1.55 system-ui, sans-serif; }}
main {{ max-width: 68rem; margin: auto; padding: 1.5rem 1rem 4rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0 2rem; background: white; }}
caption {{ text-align: left; font-weight: 700; padding: .5rem 0; }}
th, td {{ border: 1px solid #a8b4ad; padding: .55rem; text-align: left; }}
th {{ font-weight: 650; }}
a {{ color: #075ea8; }}
a:focus-visible {{ outline: 3px solid #075ea8; outline-offset: 3px; }}
code {{ overflow-wrap: anywhere; }}
</style>
</head>
<body>
<main>
<p><a href="index.html">Back to evidence overview</a></p>
<h1>Verified evaluation report</h1>
<p>This report intentionally contains only aggregate scores and safe case identifiers.
It excludes evaluation questions, model responses, rationales, prompts, and passages.</p>
<p>Runtime release <code>{html.escape(str(runtime["release_version"]))}</code>,
Lambda version <strong>{html.escape(str(runtime["function_version"]))}</strong>.</p>
<table>
<caption>Scores by evaluation suite</caption>
<thead><tr><th scope="col">Suite</th><th scope="col">Passed</th>
<th scope="col">Pass rate</th></tr></thead>
<tbody>{suite_rows}</tbody>
</table>
<table>
<caption>Case outcomes</caption>
<thead><tr><th scope="col">Safe case ID</th><th scope="col">Suite</th>
<th scope="col">Outcome</th></tr></thead>
<tbody>{case_rows}</tbody>
</table>
</main>
</body>
</html>
"""
    return page.encode("utf-8")


def _release_receipt(
    manifest: Mapping[str, object],
    evidence: Mapping[str, object],
) -> bytes:
    return _canonical_bytes(
        {
            "schema": PUBLIC_RELEASE_SCHEMA,
            "runtime_release": evidence["runtime_release"],
            "evaluation": {
                "run_id": evidence["run_id"],
                "run_at": evidence["run_at"],
                "promoted_at": evidence["promoted_at"],
                "run_context_version": evidence["run_context_version"],
                "evaluation_attestation_version": evidence["evaluation_attestation_version"],
                "summary_sha256": evidence["summary_sha256"],
                "results_sha256": evidence["results_sha256"],
                "promotion_sha256": evidence["promotion_sha256"],
                "public_manifest_version": manifest["manifest_version"],
            },
        }
    )


def _validated_svg(path: Path) -> bytes:
    payload = _read_regular(path, limit=MAX_HISTORY_SVG_BYTES, context="history SVG")
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail("history SVG must not contain DTD or entity declarations")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise EvidenceSiteError("history SVG must be well-formed XML") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        _fail("history SVG root element must be svg")
    forbidden = {"script", "style", "foreignobject", "iframe", "object", "embed"}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() in forbidden:
            _fail("history SVG contains an unsafe element")
        for raw_name, value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            normalized_value = value.lower().replace(" ", "")
            if name.startswith("on"):
                _fail("history SVG contains an event-handler attribute")
            if name == "href" and value and not value.startswith("#"):
                _fail("history SVG contains an external reference")
            if any(token in normalized_value for token in ("url(", "@import", "expression(")):
                _fail("history SVG contains an unsafe attribute value")
    return payload


def _validated_cname(path: Path) -> bytes:
    payload = _read_regular(path, limit=MAX_CNAME_BYTES, context="CNAME")
    try:
        hostname = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise EvidenceSiteError("CNAME must be ASCII") from exc
    if not _HOSTNAME.fullmatch(hostname):
        _fail("CNAME must contain exactly one hostname")
    return hostname.lower().encode("ascii") + b"\n"


#: The two pages' own titles and descriptions. They are here rather than inline
#: because the share card repeats them exactly: a card saying something the page
#: does not is a second, unreviewed description of this project, published where
#: nobody rereads it. Both sentences are the page's own words.
_REPORT_TITLE: Final = "Verified evaluation report — Transit Fare Policy Assistant"


def _report_description(run_date: str) -> str:
    """The report page's description, carrying the date of the run it reports.

    A search result and a link preview strip a page of everything but its title
    and this sentence. On a page whose whole subject is a dated artifact -- and
    which nothing expires once it is published -- a description that does not
    carry the date is the one part that cannot go stale visibly.
    """
    return (
        f"Aggregate scores by evaluation suite and per-case outcomes for the run of "
        f"{run_date}. Contains no evaluation questions, model responses, or passages."
    )


def _social_meta(*, title: str, description: str, url: str) -> str:
    """The OpenGraph and Twitter tags for one page.

    No ``og:image``. This site publishes a fixed list of files and none of them is
    an image a card could use, and an ``og:image`` naming a file that is not there
    is worse than none at all.
    """
    return "\n".join(
        (
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="Transit Fare Policy Assistant">',
            '<meta property="og:locale" content="en_US">',
            f'<meta property="og:url" content="{html.escape(url)}">',
            f'<meta property="og:title" content="{html.escape(title)}">',
            f'<meta property="og:description" content="{html.escape(description)}">',
            '<meta name="twitter:card" content="summary">',
            f'<meta name="twitter:title" content="{html.escape(title)}">',
            f'<meta name="twitter:description" content="{html.escape(description)}">',
        )
    )


def _page_url(name: str) -> str:
    """The address a published page answers on. The root is the bare origin.

    A canonical naming ``index.html`` would publish a second address for a page
    that already has one, which is the thing a canonical exists to prevent.
    """
    return f"{SITE_ORIGIN}/" if name == "index.html" else f"{SITE_ORIGIN}/{name}"


def _robots_txt() -> bytes:
    """What a crawler is told at ``/robots.txt``.

    Nothing is disallowed. Everything this site publishes is published on purpose:
    the renderer writes a fixed list of files and the workflow asserts the private
    ones are absent, so there is no path here that wants hiding.
    """
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_ORIGIN}/sitemap.xml\n".encode()


def _sitemap_xml() -> bytes:
    """The two pages, as a sitemap.

    No ``lastmod``. The evidence carries its own dates -- the run, the promotion,
    the runtime release -- and they are on the page; a build date stamped here
    would be a third date, about the rendering rather than about the evidence,
    and a sitemap date is worth publishing only while it is true.
    """
    locations = "".join(
        f"<url><loc>{html.escape(_page_url(name))}</loc></url>" for name in INDEXABLE_PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</urlset>\n"
    ).encode()


def _write_site_file(root: Path, name: str, payload: bytes) -> None:
    target = root / name
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        target.unlink(missing_ok=True)
        raise


def render_evidence_site(
    *,
    manifest_path: Path,
    template_path: Path,
    output_dir: Path,
    history_svg_path: Path | None = None,
    cname_path: Path | None = None,
    expected_source_revision: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    """Render a sanitized site only from evidence that is fresh right now."""

    manifest = load_public_manifest(manifest_path)
    require_current_public_evidence(manifest, clock=clock)
    evidence = _mapping(manifest["evidence"], "manifest.evidence")
    runtime = _mapping(evidence["runtime_release"], "manifest.evidence.runtime_release")
    if expected_source_revision is not None:
        expected_source = _safe_text(
            expected_source_revision,
            "expected source revision",
            _SOURCE_REVISION,
        )
        if runtime["source_revision"] != expected_source:
            _fail("public evidence source revision differs from the trusted renderer source")
    template = _read_regular(
        template_path,
        limit=MAX_TEMPLATE_BYTES,
        context="index template",
    )
    history = _validated_svg(history_svg_path) if history_svg_path is not None else None
    cname = _validated_cname(cname_path) if cname_path is not None else None
    if cname is not None and cname.decode("ascii").strip() != urlsplit(SITE_ORIGIN).netloc:
        # Every canonical link and every sitemap entry on this site names
        # SITE_ORIGIN. A CNAME pointing the domain somewhere else would publish a
        # site whose pages all claim to live at an address it does not answer on.
        _fail("CNAME hostname differs from the origin this site publishes")
    if output_dir.is_symlink() or output_dir.exists():
        _fail("output directory must not already exist")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.parent.is_symlink() or not output_dir.parent.is_dir():
        _fail("output directory parent must be a regular directory")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    os.chmod(temporary, 0o755)
    try:
        _write_site_file(
            temporary,
            "index.html",
            _template_html(template, evidence, trend=history is not None),
        )
        _write_site_file(temporary, "report.html", _report_html(evidence))
        _write_site_file(
            temporary,
            "release.json",
            _release_receipt(manifest, evidence),
        )
        _write_site_file(
            temporary,
            "public-evidence.json",
            _canonical_bytes(manifest),
        )
        _write_site_file(temporary, "robots.txt", _robots_txt())
        _write_site_file(temporary, "sitemap.xml", _sitemap_xml())
        if history is not None:
            _write_site_file(temporary, "eval-history.svg", history)
        if cname is not None:
            _write_site_file(temporary, "CNAME", cname)
        directory = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.replace(temporary, output_dir)
        parent = os.open(output_dir.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except (EvidenceSiteError, OSError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(exc, EvidenceSiteError):
            raise
        raise EvidenceSiteError("could not render the evidence site") from exc
    return output_dir


def compare_runtime_version(
    *,
    manifest_path: Path,
    version_response_path: Path,
    expected_source_revision: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Require fresh evidence and compare every attested runtime field."""

    manifest = load_public_manifest(manifest_path)
    require_current_public_evidence(manifest, clock=clock)
    evidence = _mapping(manifest["evidence"], "manifest.evidence")
    expected = _mapping(evidence["runtime_release"], "manifest.evidence.runtime_release")
    if expected_source_revision is not None:
        source = _safe_text(
            expected_source_revision,
            "expected source revision",
            _SOURCE_REVISION,
        )
        if expected["source_revision"] != source:
            _fail("public evidence source revision differs from the trusted verifier source")
    observed = _mapping(
        _parse_json(
            _read_regular(
                version_response_path,
                limit=MAX_VERSION_RESPONSE_BYTES,
                context="runtime version response",
            ),
            context="runtime version response",
        ),
        "runtime version response",
    )
    mismatches = [field for field in _RUNTIME_FIELDS if observed.get(field) != expected[field]]
    if observed.get("identity_status") != "verified":
        mismatches.append("identity_status")
    if observed.get("matches_pin") is not True:
        mismatches.append("matches_pin")
    if mismatches:
        _fail("runtime version differs from public evidence: " + ", ".join(mismatches))


def _parse_as_of(value: str) -> datetime:
    timestamp = _timestamp(value, "--as-of")
    return datetime.fromisoformat(timestamp[:-1] + "+00:00").astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="verify private receipts and export evidence")
    export.add_argument("--summary", required=True, type=Path)
    export.add_argument("--results", required=True, type=Path)
    export.add_argument("--promotion", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--as-of", required=True, help="fixed RFC3339 UTC verification time")
    export.add_argument("--freshness-seconds", required=True, type=int)

    render = subparsers.add_parser("render", help="render a canonical public manifest")
    render.add_argument("--manifest", required=True, type=Path)
    render.add_argument("--template", required=True, type=Path)
    render.add_argument("--output-dir", required=True, type=Path)
    render.add_argument("--history-svg", type=Path)
    render.add_argument("--cname", type=Path)
    render.add_argument("--expected-source-revision", required=True)

    compare = subparsers.add_parser(
        "compare-runtime",
        help="compare a downloaded /version response with public evidence",
    )
    compare.add_argument("--manifest", required=True, type=Path)
    compare.add_argument("--version-json", required=True, type=Path)
    compare.add_argument("--expected-source-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export":
            if args.freshness_seconds <= 0:
                _fail("--freshness-seconds must be positive")
            manifest = export_public_evidence(
                summary_path=args.summary,
                results_path=args.results,
                promotion_path=args.promotion,
                output_path=args.output,
                freshness_budget=timedelta(seconds=args.freshness_seconds),
                clock=lambda: _parse_as_of(args.as_of),
            )
            result = {
                "manifest_path": str(args.output),
                "manifest_sha256": hashlib.sha256(_canonical_bytes(manifest)).hexdigest(),
                "manifest_version": manifest["manifest_version"],
            }
        elif args.command == "render":
            output = render_evidence_site(
                manifest_path=args.manifest,
                template_path=args.template,
                output_dir=args.output_dir,
                history_svg_path=args.history_svg,
                cname_path=args.cname,
                expected_source_revision=args.expected_source_revision,
            )
            result = {"output_dir": str(output)}
        else:
            compare_runtime_version(
                manifest_path=args.manifest,
                version_response_path=args.version_json,
                expected_source_revision=args.expected_source_revision,
            )
            result = {"runtime_status": "verified"}
    except (
        EvidenceSiteError,
        PromotionEvidenceError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"public evidence build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
