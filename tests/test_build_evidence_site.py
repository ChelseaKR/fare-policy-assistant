"""Tests for deterministic, sanitized public evidence export and rendering."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup

from assistant.promotion_evidence import PromotionEvidenceError
from assistant.release_identity import build_release_identity
from scripts import build_evidence_site as site

_SOURCE = "a" * 40
_CONFIG = "b" * 64
_CONTENT = "c" * 64
_SNAPSHOT = "d" * 64
_CORPUS = "e" * 12
_RELEASE = build_release_identity(
    _SOURCE,
    _CONFIG,
    content_version=_CONTENT,
    snapshot_version=_SNAPSHOT,
).release_version
_ARTIFACT = base64.b64encode(bytes(range(32))).decode("ascii")
_TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "pages" / "index.html"
_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pages.yml"
_PRIVATE_SENTINEL = "PRIVATE-QUESTION-ANSWER-RATIONALE-PASSAGE"
_PUBLISH_NOW = datetime(2026, 7, 30, 21, 15, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fixed_publication_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(site, "_utc_now", lambda: _PUBLISH_NOW)


def _evidence(*, stale: bool = False, served_models: bool = True) -> dict[str, object]:
    context = "1" * 64
    cases: list[dict[str, object]] = [
        {
            "case_id": "policy.basic",
            "suite": "policy",
            "passed": True,
            "run_context_version": context,
            "case_semantics_version": "2" * 64,
        },
        {
            "case_id": "safety.boundary",
            "suite": "safety",
            "passed": False,
            "run_context_version": context,
            "case_semantics_version": "3" * 64,
        },
    ]
    if served_models:
        cases[0]["served_models"] = {
            "answer": ["answer-model@2026"],
            "judge": ["judge-model:1"],
        }
        cases[1]["served_models"] = {
            "answer": ["answer-model@2026"],
            "judge": ["judge-model:1", "judge-model:2"],
        }
    result: dict[str, object] = {
        "status": "warning" if stale else "verified",
        "warnings": ["evaluation.stale"] if stale else [],
        "fresh": not stale,
        "age_seconds": 604_801 if stale else 3_600,
        "max_age_seconds": 604_800,
        "run_id": "20260730T201501Z",
        "run_at": "2026-07-30T20:15:01Z",
        "promoted_at": "2026-07-30T20:20:01Z",
        "runtime_release": {
            "source_revision": _SOURCE,
            "config_version": _CONFIG,
            "content_version": _CONTENT,
            "snapshot_version": _SNAPSHOT,
            "release_version": _RELEASE,
            "corpus_version": _CORPUS,
            "artifact_code_sha256": _ARTIFACT,
            "function_version": "11",
        },
        "run_context_version": context,
        "evaluation_attestation_version": "4" * 64,
        "summary_sha256": "5" * 64,
        "results_sha256": "6" * 64,
        "promotion_sha256": "7" * 64,
        "total": {"passed": 1, "total": 2, "pass_rate": 50.0},
        "suites": [
            {"name": "policy", "passed": 1, "total": 1, "pass_rate": 100.0},
            {"name": "safety", "passed": 0, "total": 1, "pass_rate": 0.0},
        ],
        "cases": cases,
    }
    if served_models:
        result["served_models"] = {
            "answer": ["answer-model@2026"],
            "judge": ["judge-model:1", "judge-model:2"],
        }
    return result


def _canonical(value: object) -> bytes:
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


def _manifest(evidence: dict[str, object] | None = None) -> dict[str, object]:
    selected = copy.deepcopy(evidence or _evidence())
    version = hashlib.sha256(
        site.PUBLIC_EVIDENCE_SCHEMA.encode("ascii") + b"\0" + _canonical(selected)[:-1]
    ).hexdigest()
    return {
        "schema": site.PUBLIC_EVIDENCE_SCHEMA,
        "evidence": selected,
        "manifest_version": version,
    }


def _write_manifest(path: Path, manifest: dict[str, object] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(manifest or _manifest()))
    return path


class _Verified:
    def __init__(self, evidence: dict[str, object]):
        self._evidence = evidence

    def as_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._evidence)


def _fake_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: dict[str, object] | None = None,
) -> Path:
    private = tmp_path / "private"
    private.mkdir(parents=True)
    summary = private / "summary.json"
    results = private / "results.jsonl"
    promotion = private / "promotion.json"
    for path in (summary, results, promotion):
        path.write_text(_PRIVATE_SENTINEL, encoding="utf-8")

    def verify(**kwargs: object) -> _Verified:
        assert kwargs["summary_path"] == summary
        assert kwargs["results_path"] == results
        assert kwargs["promotion_path"] == promotion
        clock = kwargs["clock"]
        assert callable(clock)
        assert clock() == datetime(2026, 7, 31, tzinfo=UTC)
        return _Verified(evidence or _evidence())

    monkeypatch.setattr(site, "verify_promotion_evidence", verify)
    output = tmp_path / "public-evidence.json"
    site.export_public_evidence(
        summary_path=summary,
        results_path=results,
        promotion_path=promotion,
        output_path=output,
        freshness_budget=timedelta(days=7),
        clock=lambda: datetime(2026, 7, 31, tzinfo=UTC),
    )
    return output


def _site_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_calls_closed_verifier_and_writes_only_canonical_sanitized_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _fake_export(tmp_path, monkeypatch)

    payload = output.read_bytes()
    manifest = site.load_public_manifest(output)

    assert payload == _canonical(manifest)
    assert set(manifest) == {"schema", "evidence", "manifest_version"}
    assert _PRIVATE_SENTINEL.encode() not in payload
    assert b'"question"' not in payload
    assert b'"rationale"' not in payload
    assert b'"passages"' not in payload


def test_export_is_deterministic_for_fixed_inputs_and_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _fake_export(tmp_path / "first", monkeypatch)
    first_bytes = first.read_bytes()
    second = _fake_export(tmp_path / "second", monkeypatch)

    assert second.read_bytes() == first_bytes


def test_export_propagates_private_verifier_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private.json"
    private.write_bytes(b"{}")

    def reject(**_kwargs: object) -> None:
        raise PromotionEvidenceError("summary.sha256")

    monkeypatch.setattr(site, "verify_promotion_evidence", reject)

    with pytest.raises(PromotionEvidenceError):
        site.export_public_evidence(
            summary_path=private,
            results_path=private,
            promotion_path=private,
            output_path=tmp_path / "public.json",
            freshness_budget=timedelta(days=7),
            clock=lambda: datetime.now(UTC),
        )
    assert not (tmp_path / "public.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "top_extra",
        "evidence_extra",
        "bad_schema",
        "bad_version",
        "bad_status",
        "bad_warning_type",
        "bad_warning_value",
        "bad_age",
        "bad_run_id",
        "bad_timestamp",
        "bad_context_digest",
        "bad_release",
        "bad_runtime_shape",
        "empty_cases",
        "duplicate_case",
        "bad_case_passed",
        "bad_case_suite",
        "bad_case_context",
        "partial_case_models",
        "bad_total",
        "bad_total_rate",
        "bad_suites",
        "bad_suite_count",
        "bad_suite_score",
        "bad_models",
        "unsorted_models",
        "summary_models_without_cases",
        "case_models_without_summary",
        "raw_field",
    ],
)
def test_strict_manifest_rejects_tamper_and_extra_fields(
    tmp_path: Path,
    mutation: str,
) -> None:
    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    cases = evidence["cases"]
    suites = evidence["suites"]
    runtime = evidence["runtime_release"]
    models = evidence["served_models"]
    assert isinstance(cases, list)
    assert isinstance(suites, list)
    assert isinstance(runtime, dict)
    assert isinstance(models, dict)
    if mutation == "top_extra":
        manifest["unexpected"] = True
    elif mutation == "evidence_extra":
        evidence["unexpected"] = True
    elif mutation == "bad_schema":
        manifest["schema"] = "fare-assistant.public-evidence.v2"
    elif mutation == "bad_version":
        manifest["manifest_version"] = "f" * 64
    elif mutation == "bad_status":
        evidence["fresh"] = False
    elif mutation == "bad_warning_type":
        evidence["warnings"] = "evaluation.stale"
    elif mutation == "bad_warning_value":
        evidence["status"] = "warning"
        evidence["fresh"] = False
        evidence["warnings"] = ["wrong"]
    elif mutation == "bad_age":
        evidence["age_seconds"] = 700_000
    elif mutation == "bad_run_id":
        evidence["run_id"] = "../unsafe"
    elif mutation == "bad_timestamp":
        evidence["run_at"] = "not-a-time"
    elif mutation == "bad_context_digest":
        evidence["run_context_version"] = "invalid"
    elif mutation == "bad_release":
        runtime["release_version"] = "f" * 64
    elif mutation == "bad_runtime_shape":
        runtime.pop("artifact_code_sha256")
    elif mutation == "empty_cases":
        evidence["cases"] = []
    elif mutation == "duplicate_case":
        cases[1]["case_id"] = cases[0]["case_id"]
    elif mutation == "bad_case_passed":
        cases[0]["passed"] = 1
    elif mutation == "bad_case_suite":
        cases[0]["suite"] = " bad "
    elif mutation == "bad_case_context":
        cases[0]["run_context_version"] = "f" * 64
    elif mutation == "partial_case_models":
        cases[0].pop("served_models")
    elif mutation == "bad_total":
        total = evidence["total"]
        assert isinstance(total, dict)
        total["passed"] = 2
    elif mutation == "bad_total_rate":
        total = evidence["total"]
        assert isinstance(total, dict)
        total["pass_rate"] = 99.0
    elif mutation == "bad_suites":
        suites.reverse()
    elif mutation == "bad_suite_count":
        suites.pop()
    elif mutation == "bad_suite_score":
        suites[0]["passed"] = 0
    elif mutation == "bad_models":
        models["judge"] = ["not-observed"]
    elif mutation == "unsorted_models":
        models["judge"] = ["judge-model:2", "judge-model:1"]
    elif mutation == "summary_models_without_cases":
        for case in cases:
            case.pop("served_models")
    elif mutation == "case_models_without_summary":
        evidence.pop("served_models")
    elif mutation == "raw_field":
        cases[0]["question"] = _PRIVATE_SENTINEL
    if mutation not in {"top_extra", "bad_schema", "bad_version"}:
        manifest = _manifest(evidence)
    path = _write_manifest(tmp_path / "public.json", manifest)

    with pytest.raises(site.EvidenceSiteError):
        site.load_public_manifest(path)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"schema":"x","schema":"x"}',
        b'{"value":NaN}',
        b"\xff",
    ],
)
def test_manifest_rejects_malformed_duplicate_nonfinite_and_non_utf8(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "public.json"
    path.write_bytes(payload)

    with pytest.raises(site.EvidenceSiteError):
        site.load_public_manifest(path)


def test_manifest_rejects_noncanonical_bytes_and_symlink(tmp_path: Path) -> None:
    manifest = _manifest()
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(site.EvidenceSiteError, match="not canonical"):
        site.load_public_manifest(pretty)

    canonical = _write_manifest(tmp_path / "canonical.json")
    linked = tmp_path / "linked.json"
    linked.symlink_to(canonical)
    with pytest.raises(site.EvidenceSiteError, match="non-symlink"):
        site.load_public_manifest(linked)


@pytest.mark.parametrize(
    ("stale", "served_models"),
    [(True, True), (False, False)],
)
def test_manifest_accepts_closed_warning_and_legacy_optional_model_shapes(
    tmp_path: Path,
    stale: bool,
    served_models: bool,
) -> None:
    path = _write_manifest(
        tmp_path / "public.json",
        _manifest(_evidence(stale=stale, served_models=served_models)),
    )

    loaded = site.load_public_manifest(path)

    evidence = loaded["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["status"] == ("warning" if stale else "verified")
    assert ("served_models" in evidence) is served_models


def test_warning_requires_age_strictly_greater_than_budget(tmp_path: Path) -> None:
    evidence = _evidence(stale=True)
    evidence["age_seconds"] = evidence["max_age_seconds"]
    path = _write_manifest(tmp_path / "public.json", _manifest(evidence))

    with pytest.raises(site.EvidenceSiteError, match="freshness age"):
        site.load_public_manifest(path)


def test_consumers_recompute_freshness_and_reject_replayed_manifest(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path / "public.json")
    manifest = site.load_public_manifest(manifest_path)
    version_path = tmp_path / "version.json"
    version_path.write_bytes(_canonical(_version_response()))
    run_at = datetime(2026, 7, 30, 20, 15, 1, tzinfo=UTC)
    exact_budget = run_at + timedelta(seconds=604_800)
    replayed = exact_budget + timedelta(seconds=1)

    site.require_current_public_evidence(manifest, clock=lambda: exact_budget)
    with pytest.raises(site.EvidenceSiteError, match="stale at verification time"):
        site.require_current_public_evidence(manifest, clock=lambda: replayed)
    with pytest.raises(site.EvidenceSiteError, match="stale at verification time"):
        site.render_evidence_site(
            manifest_path=manifest_path,
            template_path=_TEMPLATE,
            output_dir=tmp_path / "replayed-site",
            clock=lambda: replayed,
        )
    with pytest.raises(site.EvidenceSiteError, match="stale at verification time"):
        site.compare_runtime_version(
            manifest_path=manifest_path,
            version_response_path=version_path,
            clock=lambda: replayed,
        )
    assert not (tmp_path / "replayed-site").exists()


def test_bounded_reader_rejects_wrong_missing_directory_and_oversized_inputs(
    tmp_path: Path,
) -> None:
    with pytest.raises(site.EvidenceSiteError, match="pathlib"):
        site._read_regular("not-a-path", limit=10, context="test")  # type: ignore[arg-type]
    with pytest.raises(site.EvidenceSiteError, match="missing"):
        site._read_regular(tmp_path / "missing", limit=10, context="test")
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(site.EvidenceSiteError, match="regular non-symlink"):
        site._read_regular(directory, limit=10, context="test")
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"123")
    with pytest.raises(site.EvidenceSiteError, match="exceeds"):
        site._read_regular(oversized, limit=2, context="test")


def test_bounded_reader_detects_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"original")
    original_read = site.os.read
    changed = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, count)
        if not changed:
            changed = True
            path.write_bytes(b"changed")
        return chunk

    monkeypatch.setattr(site.os, "read", racing_read)

    with pytest.raises(site.EvidenceSiteError, match="changed"):
        site._read_regular(path, limit=100, context="test")


def test_atomic_export_refuses_symlink_and_directory_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _fake_export(tmp_path / "base", monkeypatch)
    symlink = tmp_path / "output-link"
    symlink.symlink_to(target)
    with pytest.raises(site.EvidenceSiteError, match="symlink"):
        site._atomic_write(symlink, b"safe")
    directory = tmp_path / "output-directory"
    directory.mkdir()
    with pytest.raises(site.EvidenceSiteError, match="regular file"):
        site._atomic_write(directory, b"safe")


def test_render_is_deterministic_atomic_and_contains_no_private_trace_fields(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    svg = tmp_path / "eval-history.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<title>History</title><path d="M0 10L10 0"/></svg>\n',
        encoding="utf-8",
    )
    cname = tmp_path / "CNAME"
    cname.write_text("EVALS.CHELSEAKR.COM\n", encoding="ascii")

    first = site.render_evidence_site(
        manifest_path=manifest,
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site-one",
        history_svg_path=svg,
        cname_path=cname,
    )
    second = site.render_evidence_site(
        manifest_path=manifest,
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site-two",
        history_svg_path=svg,
        cname_path=cname,
    )

    first_files = _site_files(first)
    assert first_files == _site_files(second)
    assert set(first_files) == {
        "CNAME",
        "eval-history.svg",
        "index.html",
        "public-evidence.json",
        "release.json",
        "report.html",
    }
    assert first_files["CNAME"] == b"evals.chelseakr.com\n"
    combined = b"\n".join(first_files.values())
    assert _PRIVATE_SENTINEL.encode() not in combined
    assert b"{{" not in first_files["index.html"]
    assert b"policy.basic" in first_files["report.html"]


def test_render_without_optional_files_has_no_broken_history_reference(
    tmp_path: Path,
) -> None:
    output = site.render_evidence_site(
        manifest_path=_write_manifest(tmp_path / "public.json"),
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site",
    )

    assert not (output / "CNAME").exists()
    assert not (output / "eval-history.svg").exists()
    assert b"eval-history.svg" not in (output / "index.html").read_bytes()


def test_rendered_pages_have_semantic_accessibility_landmarks(tmp_path: Path) -> None:
    svg = tmp_path / "history.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>Trend</title></svg>')
    output = site.render_evidence_site(
        manifest_path=_write_manifest(tmp_path / "public.json"),
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site",
        history_svg_path=svg,
    )

    for name in ("index.html", "report.html"):
        soup = BeautifulSoup((output / name).read_text(encoding="utf-8"), "html.parser")
        assert soup.html is not None and soup.html.get("lang") == "en"
        assert soup.title is not None and soup.title.get_text(strip=True)
        assert soup.main is not None
        assert soup.h1 is not None
        for table in soup.find_all("table"):
            assert table.find("caption") is not None
            assert all(cell.get("scope") in {"col", "row"} for cell in table.find_all("th"))
    image = BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser").find(
        "img"
    )
    assert image is not None and image.get("alt")


def test_release_receipt_is_canonical_and_contains_exact_runtime_tuple(
    tmp_path: Path,
) -> None:
    output = site.render_evidence_site(
        manifest_path=_write_manifest(tmp_path / "public.json"),
        template_path=_TEMPLATE,
        output_dir=tmp_path / "site",
    )

    payload = (output / "release.json").read_bytes()
    release = json.loads(payload)

    assert payload == _canonical(release)
    assert release["schema"] == site.PUBLIC_RELEASE_SCHEMA
    assert release["runtime_release"] == _evidence()["runtime_release"]
    assert set(release["evaluation"]) == {
        "run_id",
        "run_at",
        "promoted_at",
        "run_context_version",
        "evaluation_attestation_version",
        "summary_sha256",
        "results_sha256",
        "promotion_sha256",
        "public_manifest_version",
    }


def test_render_refuses_existing_output_and_leaves_no_partial_directory(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    existing = tmp_path / "site"
    existing.mkdir()
    marker = existing / "owned.txt"
    marker.write_text("user")
    with pytest.raises(site.EvidenceSiteError, match="must not already exist"):
        site.render_evidence_site(
            manifest_path=manifest,
            template_path=_TEMPLATE,
            output_dir=existing,
        )
    assert marker.read_text() == "user"

    invalid_template = tmp_path / "invalid-template.html"
    invalid_template.write_text("<html>{{STATUS_LABEL}}</html>")
    destination = tmp_path / "not-created"
    with pytest.raises(site.EvidenceSiteError, match="placeholders"):
        site.render_evidence_site(
            manifest_path=manifest,
            template_path=invalid_template,
            output_dir=destination,
        )
    assert not destination.exists()


def test_render_rejects_non_utf8_template_and_non_ascii_cname(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    template = tmp_path / "template.html"
    template.write_bytes(b"\xff")
    with pytest.raises(site.EvidenceSiteError, match="UTF-8"):
        site.render_evidence_site(
            manifest_path=manifest,
            template_path=template,
            output_dir=tmp_path / "site-template",
        )
    cname = tmp_path / "CNAME"
    cname.write_bytes("é.example".encode())
    with pytest.raises(site.EvidenceSiteError, match="ASCII"):
        site.render_evidence_site(
            manifest_path=manifest,
            template_path=_TEMPLATE,
            output_dir=tmp_path / "site-cname",
            cname_path=cname,
        )


def test_render_wraps_output_io_failure_and_removes_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = site._write_site_file
    calls = 0

    def fail_second(root: Path, name: str, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failed")
        original(root, name, payload)

    monkeypatch.setattr(site, "_write_site_file", fail_second)
    destination = tmp_path / "site"

    with pytest.raises(site.EvidenceSiteError, match="could not render"):
        site.render_evidence_site(
            manifest_path=_write_manifest(tmp_path / "public.json"),
            template_path=_TEMPLATE,
            output_dir=destination,
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".site.*")) == []


@pytest.mark.parametrize(
    "svg",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "bad.css"</style></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://bad.test/x"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="bad()"/>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://bad.test/x)"/></svg>',
        "<html/>",
        "<svg",
        '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
    ],
)
def test_render_rejects_unsafe_or_malformed_svg(tmp_path: Path, svg: str) -> None:
    history = tmp_path / "history.svg"
    history.write_text(svg)
    with pytest.raises(site.EvidenceSiteError):
        site.render_evidence_site(
            manifest_path=_write_manifest(tmp_path / "public.json"),
            template_path=_TEMPLATE,
            output_dir=tmp_path / "site",
            history_svg_path=history,
        )
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize(
    "cname",
    ["https://evals.example.com", "two.example\nnames.example", ".example.com", "bad_host"],
)
def test_render_rejects_invalid_cname(tmp_path: Path, cname: str) -> None:
    path = tmp_path / "CNAME"
    path.write_text(cname)
    with pytest.raises(site.EvidenceSiteError, match="hostname"):
        site.render_evidence_site(
            manifest_path=_write_manifest(tmp_path / "public.json"),
            template_path=_TEMPLATE,
            output_dir=tmp_path / "site",
            cname_path=path,
        )


def _version_response(evidence: dict[str, object] | None = None) -> dict[str, object]:
    selected = evidence or _evidence()
    runtime = selected["runtime_release"]
    assert isinstance(runtime, dict)
    return {
        **runtime,
        "identity_status": "verified",
        "matches_pin": True,
        "unrelated_public_version_field": "allowed",
    }


def test_runtime_comparison_requires_every_attested_runtime_field(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    version = tmp_path / "version.json"
    version.write_bytes(_canonical(_version_response()))

    site.compare_runtime_version(manifest_path=manifest, version_response_path=version)

    for field in (
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "artifact_code_sha256",
        "function_version",
        "identity_status",
        "matches_pin",
    ):
        changed = _version_response()
        changed[field] = False if field == "matches_pin" else "mismatch"
        version.write_bytes(_canonical(changed))
        with pytest.raises(site.EvidenceSiteError, match=field):
            site.compare_runtime_version(
                manifest_path=manifest,
                version_response_path=version,
            )


def test_render_and_runtime_compare_bind_the_trusted_source_revision(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    version = tmp_path / "version.json"
    version.write_bytes(_canonical(_version_response()))

    with pytest.raises(site.EvidenceSiteError, match="trusted renderer"):
        site.render_evidence_site(
            manifest_path=manifest,
            template_path=_TEMPLATE,
            output_dir=tmp_path / "site",
            expected_source_revision="f" * 40,
        )
    with pytest.raises(site.EvidenceSiteError, match="trusted verifier"):
        site.compare_runtime_version(
            manifest_path=manifest,
            version_response_path=version,
            expected_source_revision="f" * 40,
        )
    with pytest.raises(site.EvidenceSiteError, match="safe identifier"):
        site.compare_runtime_version(
            manifest_path=manifest,
            version_response_path=version,
            expected_source_revision="main",
        )


def test_cli_render_and_compare_runtime_return_machine_readable_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path / "public.json")
    assert (
        site.main(
            [
                "render",
                "--manifest",
                str(manifest),
                "--template",
                str(_TEMPLATE),
                "--output-dir",
                str(tmp_path / "site"),
                "--expected-source-revision",
                _SOURCE,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["output_dir"] == str(tmp_path / "site")
    version = tmp_path / "version.json"
    version.write_bytes(_canonical(_version_response()))
    assert (
        site.main(
            [
                "compare-runtime",
                "--manifest",
                str(manifest),
                "--version-json",
                str(version),
                "--expected-source-revision",
                _SOURCE,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"runtime_status": "verified"}


def test_cli_export_uses_explicit_clock_and_reports_exact_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private = tmp_path / "private"
    private.write_text(_PRIVATE_SENTINEL)
    observed_clock: list[datetime] = []

    def verify(**kwargs: object) -> _Verified:
        clock = kwargs["clock"]
        assert callable(clock)
        observed_clock.append(clock())
        return _Verified(_evidence())

    monkeypatch.setattr(site, "verify_promotion_evidence", verify)
    output = tmp_path / "public.json"

    assert (
        site.main(
            [
                "export",
                "--summary",
                str(private),
                "--results",
                str(private),
                "--promotion",
                str(private),
                "--output",
                str(output),
                "--as-of",
                "2026-07-31T00:00:00Z",
                "--freshness-seconds",
                "604800",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert observed_clock == [datetime(2026, 7, 31, tzinfo=UTC)]
    assert result["manifest_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["manifest_version"] == json.loads(output.read_bytes())["manifest_version"]


@pytest.mark.parametrize(
    ("as_of", "freshness"),
    [("not-a-time", "604800"), ("2026-07-31T00:00:00Z", "0")],
)
def test_cli_export_rejects_invalid_clock_or_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_of: str,
    freshness: str,
) -> None:
    private = tmp_path / "private"
    private.write_text("{}")

    def verify(**kwargs: object) -> _Verified:
        clock = kwargs["clock"]
        assert callable(clock)
        clock()
        return _Verified(_evidence())

    monkeypatch.setattr(site, "verify_promotion_evidence", verify)
    assert (
        site.main(
            [
                "export",
                "--summary",
                str(private),
                "--results",
                str(private),
                "--promotion",
                str(private),
                "--output",
                str(tmp_path / "output"),
                "--as-of",
                as_of,
                "--freshness-seconds",
                freshness,
            ]
        )
        == 2
    )
    assert "public evidence build failed" in capsys.readouterr().err


def test_cli_failure_is_nonzero_and_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}")

    assert (
        site.main(
            [
                "render",
                "--manifest",
                str(bad),
                "--template",
                str(_TEMPLATE),
                "--output-dir",
                str(tmp_path / "site"),
                "--expected-source-revision",
                _SOURCE,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("public evidence build failed:")


def test_pages_workflow_is_manual_commit_and_digest_pinned_and_sanitized() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)

    assert isinstance(parsed, dict)
    assert set(parsed["on"]) == {"workflow_dispatch"}
    inputs = parsed["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "source_revision",
        "evidence_ref",
        "expected_public_manifest_sha256",
    }
    assert text.count("ref: ${{ inputs.source_revision }}") == 2
    assert text.count("ref: ${{ inputs.evidence_ref }}") == 2
    assert text.count("fetch-depth: 0") == 2
    assert text.count("merge-base --is-ancestor") == 2
    assert text.count("refs/remotes/origin/main") == 2
    assert "path: source" in text
    assert "path: evidence-checkout" in text
    assert text.count('test "${evidence_entries[0]}" = "public-evidence.json"') == 2
    assert "EXPECTED_PUBLIC_MANIFEST_SHA256" in text
    assert text.count("sha256sum") == 3
    assert text.count("https://fare.chelseakr.com/version") == 2
    assert "https://evals.chelseakr.com/public-evidence.json" in text
    assert "published evidence did not converge to the expected digest" in text
    assert text.count("build_evidence_site.py compare-runtime") == 2
    assert text.count("--expected-source-revision") == 3
    assert 'build_evidence_site.py "${render_args[@]}"' in text
    assert "path: _site" in text
    assert "docs/eval-report.html" not in text
    assert "cp " not in text
    assert "_site/results.jsonl" in text
    assert "_site/summary.json" in text
    assert "_site/promotion.json" in text


def test_every_pages_action_is_pinned_to_a_full_commit_sha() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"^\s*uses:\s*([^#\s]+)", text, flags=re.MULTILINE)

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    assert not re.search(r"uses:\s*[^@\s]+@v[0-9]", text)
