"""Focused tests for deterministic, secret-free evaluation attestations."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import evals.attestation as attestation_module
from assistant import config
from assistant.identity import DocumentObservation, SnapshotIdentity
from assistant.release_identity import (
    ReleaseIdentityError,
    build_config_identity,
    build_release_identity,
)
from evals.attestation import (
    ATTESTATION_SCHEMA,
    CASE_SEMANTICS_SCHEMA,
    CONTEXT_SCHEMA,
    EvalAttestationError,
    build_attestation,
    canonical_digest,
    case_manifest,
    case_semantics_version,
    evaluator_identity,
    facts_identity,
    file_receipt,
    git_source_status,
    gtfs_legacy_input_identity,
    suite_version,
)


def _subject(
    *,
    state: str = "clean",
    head: str = "1" * 40,
    config_version: str = "a" * 64,
    content_version: str = "b" * 64,
    snapshot_version: str = "c" * 64,
    descriptor_verified: bool = True,
) -> dict[str, object]:
    release_version = (
        build_release_identity(
            head,
            config_version,
            content_version=content_version,
            snapshot_version=snapshot_version,
        ).release_version
        if state == "clean"
        else None
    )
    return {
        "source_state": state,
        "head_revision": head,
        "source_revision": head if state == "clean" else None,
        "config_version": config_version,
        "content_version": content_version,
        "snapshot_version": snapshot_version,
        "release_version": release_version,
        "corpus_version": "d" * 12,
        "descriptor_verified": descriptor_verified,
    }


def _promotion(
    *,
    eligible: bool = True,
    evaluated_at: str = "2026-07-30T22:45:00+00:00",
) -> dict[str, object]:
    return {
        "eligible": eligible,
        "live": eligible,
        "uncached": eligible,
        "judges_ran": eligible,
        "gates_passed": eligible,
        "reasons": [] if eligible else ["offline"],
        "evaluated_at": evaluated_at,
    }


def _attestation(
    *,
    subject: dict[str, object] | None = None,
    promotion: dict[str, object] | None = None,
    protocol: dict[str, object] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    cases = [
        {"id": "case-a", "question": "Question A", "expected_behavior": "answer"},
        {"id": "case-b", "question": "Question B", "expected_behavior": "partial"},
    ]
    return build_attestation(
        subject=subject if subject is not None else _subject(),
        suite_version=suite_version(cases),
        case_manifest=case_manifest(cases),
        facts_version="f" * 64,
        gtfs_input_version="0" * 64,
        protocol=(
            protocol
            if protocol is not None
            else {
                "mode": "full",
                "offline": False,
                "replicates": 1,
                "evaluator_version": "9" * 64,
            }
        ),
        promotion=promotion if promotion is not None else _promotion(),
        **kwargs,
    )


def test_canonical_digest_is_schema_framed_sorted_utf8_sha256() -> None:
    left = canonical_digest("test.identity.v1", {"z": "café", "a": [1, True]})
    right = canonical_digest("test.identity.v1", {"a": [1, True], "z": "café"})
    expected_bytes = b'test.identity.v1\x00{"a":[1,true],"z":"caf\xc3\xa9"}'

    assert left == right == hashlib.sha256(expected_bytes).hexdigest()
    assert canonical_digest("test.identity.v2", {"a": [1, True], "z": "café"}) != left


@pytest.mark.parametrize(
    "schema",
    ["", "TEST.identity.v1", "test identity v1", "test.identity", "test.identity.v0"],
)
def test_canonical_digest_rejects_noncanonical_schemas(schema: str) -> None:
    with pytest.raises(EvalAttestationError, match="schema"):
        canonical_digest(schema, {})


def test_canonical_digest_rejects_non_json_and_nonfinite_values() -> None:
    with pytest.raises(EvalAttestationError, match="unsupported JSON"):
        canonical_digest("test.identity.v1", {"value": b"raw"})
    with pytest.raises(EvalAttestationError, match="non-finite"):
        canonical_digest("test.identity.v1", {"value": float("nan")})
    with pytest.raises(EvalAttestationError, match="keys must be strings"):
        canonical_digest("test.identity.v1", {1: "ambiguous"})


def test_file_receipt_hashes_exact_bytes_and_rejects_nonfiles(tmp_path: Path) -> None:
    target = tmp_path / "input.bin"
    target.write_bytes(b"\x00exact\r\nbytes")

    assert file_receipt(target, root=tmp_path) == {
        "sha256": hashlib.sha256(b"\x00exact\r\nbytes").hexdigest(),
        "bytes": 13,
    }
    with pytest.raises(EvalAttestationError, match="regular file"):
        file_receipt(tmp_path, root=tmp_path)
    with pytest.raises(EvalAttestationError, match="escapes"):
        file_receipt(tmp_path.parent / "outside", root=tmp_path)


def test_file_receipt_rejects_file_and_parent_symlinks(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    target = actual / "input.txt"
    target.write_text("bytes", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    parent_link = tmp_path / "linked-dir"
    parent_link.symlink_to(actual, target_is_directory=True)

    with pytest.raises(EvalAttestationError, match="regular file"):
        file_receipt(link, root=tmp_path)
    with pytest.raises(EvalAttestationError, match="parent"):
        file_receipt(parent_link / "input.txt", root=tmp_path)


def test_case_semantics_covers_complete_mapping_but_not_key_order() -> None:
    case = {
        "id": "case-1",
        "suite": "edge",
        "question": "How much?",
        "expected_behavior": "answer",
        "required_facts": ["$2.00"],
        "history": [{"q": "Earlier?", "a": "Earlier answer."}],
        "rationale": "Checks the complete semantics.",
    }
    reordered = dict(reversed(list(case.items())))

    baseline = case_semantics_version(case)
    assert baseline == case_semantics_version(reordered)
    assert baseline != case_semantics_version({**case, "rationale": "Changed."})
    assert baseline != case_semantics_version(
        {**case, "history": [{"q": "Earlier?", "a": "Different."}]}
    )
    assert len(baseline) == 64


def test_case_semantics_digest_uses_its_declared_schema() -> None:
    case = {"id": "case-1", "question": "Q", "expected_behavior": "answer"}
    expected = canonical_digest(CASE_SEMANTICS_SCHEMA, case)
    assert case_semantics_version(case) == expected


def test_suite_version_preserves_case_order_and_rejects_duplicate_ids() -> None:
    first = {"id": "a", "question": "Q1", "expected_behavior": "answer"}
    second = {"id": "b", "question": "Q2", "expected_behavior": "partial"}
    first_reordered = dict(reversed(list(first.items())))

    assert suite_version([first, second]) == suite_version([first_reordered, second])
    assert suite_version([first, second]) != suite_version([second, first])
    with pytest.raises(EvalAttestationError, match="duplicate case id"):
        suite_version([first, {**second, "id": "a"}])
    with pytest.raises(EvalAttestationError, match="must not be empty"):
        suite_version([])


def test_case_manifest_binds_ordered_ids_and_complete_case_semantics() -> None:
    first = {"id": "a", "question": "Q1", "expected_behavior": "answer"}
    second = {"id": "b", "question": "Q2", "expected_behavior": "partial"}

    manifest = case_manifest([first, second])

    assert manifest == [
        {
            "case_id": "a",
            "case_semantics_version": case_semantics_version(first),
        },
        {
            "case_id": "b",
            "case_semantics_version": case_semantics_version(second),
        },
    ]
    assert case_manifest([second, first]) != manifest
    assert case_manifest([{**first, "question": "Changed"}, second]) != manifest


@pytest.mark.parametrize("case_id", ["", " leading", "trailing ", "../escape", "contains space"])
def test_case_manifest_rejects_unsafe_case_ids(case_id: str) -> None:
    with pytest.raises(EvalAttestationError, match=r"id|trimmed"):
        case_manifest([{"id": case_id, "question": "Q"}])


def test_facts_identity_is_exact_byte_sensitive_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    facts = tmp_path / "facts.jsonl"
    facts.write_bytes(b'{"price":"2.00"}\n')
    first = facts_identity(facts)
    facts.write_bytes(b'{"price":"2.00"}\r\n')
    second = facts_identity(facts)

    assert first["facts_version"] != second["facts_version"]
    assert first["receipt"]["bytes"] + 1 == second["receipt"]["bytes"]
    link = tmp_path / "facts-link.jsonl"
    link.symlink_to(facts)
    with pytest.raises(EvalAttestationError, match="regular file"):
        facts_identity(link)


def test_gtfs_identity_is_canonical_explicit_and_content_sensitive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gtfs"
    (root / "MST").mkdir(parents=True)
    fares = root / "MST" / "fare_attributes.txt"
    fares.write_bytes(b"fare_id,price\nadult,2.00\n")
    categories = root / "MST" / "rider_categories.txt"
    categories.write_bytes(b"rider_category_id,rider_category_name\nadult,Adult\n")
    manifest = {
        "gtfs_feeds": [
            {"agency": "SBMTD", "url": "https://example.test/sb.zip", "fares_version": "v2"},
            {"agency": "MST", "url": "https://example.test/mst.zip", "fares_version": "v1"},
        ]
    }
    reversed_manifest = {"gtfs_feeds": list(reversed(manifest["gtfs_feeds"]))}

    first = gtfs_legacy_input_identity(manifest, root)
    assert first == gtfs_legacy_input_identity(reversed_manifest, root)
    assert [(item["agency"], item["state"]) for item in first["agencies"]] == [
        ("MST", "legacy_extracted_only"),
        ("SBMTD", "unavailable"),
    ]
    serialized = json.dumps(first)
    assert "zip_sha256" not in serialized
    assert "feed_bytes" not in serialized

    fares.write_bytes(b"fare_id,price\nadult,3.00\n")
    changed_bytes = gtfs_legacy_input_identity(manifest, root)
    assert changed_bytes["gtfs_input_version"] != first["gtfs_input_version"]
    categories.write_bytes(b"rider_category_id,rider_category_name\nadult,Full fare adult\n")
    changed_categories = gtfs_legacy_input_identity(manifest, root)
    assert changed_categories["gtfs_input_version"] != changed_bytes["gtfs_input_version"]

    changed_manifest = {
        "gtfs_feeds": [
            *manifest["gtfs_feeds"][:-1],
            {
                **manifest["gtfs_feeds"][-1],
                "url": "https://example.test/new-mst.zip",
            },
        ]
    }
    assert (
        gtfs_legacy_input_identity(changed_manifest, root)["gtfs_input_version"]
        != changed_categories["gtfs_input_version"]
    )


def test_gtfs_identity_rejects_duplicates_unsafe_agencies_and_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gtfs"
    agency = root / "MST"
    agency.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("fare_id,price\nx,1\n", encoding="utf-8")
    (agency / "fare_products.txt").symlink_to(outside)

    with pytest.raises(EvalAttestationError, match="regular file"):
        gtfs_legacy_input_identity([{"agency": "MST"}], root)
    with pytest.raises(EvalAttestationError, match="duplicate"):
        gtfs_legacy_input_identity([{"agency": "MST"}, {"agency": "MST"}], root)
    with pytest.raises(EvalAttestationError, match="safe path segment"):
        gtfs_legacy_input_identity([{"agency": "../MST"}], root)


def test_evaluator_identity_covers_complete_sorted_python_trees(tmp_path: Path) -> None:
    (tmp_path / "evals").mkdir()
    (tmp_path / "src" / "assistant").mkdir(parents=True)
    check = tmp_path / "evals" / "checks.py"
    check.write_text("PASS = True\n", encoding="utf-8")
    answer = tmp_path / "src" / "assistant" / "answer.py"
    answer.write_text("def answer(): return 1\n", encoding="utf-8")
    (tmp_path / "evals" / "notes.txt").write_text("not executable", encoding="utf-8")

    first = evaluator_identity(tmp_path, source_trees=("src/assistant", "evals"))
    reordered = evaluator_identity(tmp_path, source_trees=("evals", "src/assistant"))
    assert first == reordered
    assert [receipt["path"] for receipt in first["files"]] == [
        "evals/checks.py",
        "src/assistant/answer.py",
    ]

    answer.write_text("def answer(): return 2\n", encoding="utf-8")
    assert evaluator_identity(tmp_path)["evaluator_version"] != first["evaluator_version"]


def test_evaluator_identity_rejects_symlinks_overlaps_and_unsafe_trees(
    tmp_path: Path,
) -> None:
    (tmp_path / "evals" / "nested").mkdir(parents=True)
    source = tmp_path / "real.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "evals" / "linked.py").symlink_to(source)

    with pytest.raises(EvalAttestationError, match="regular file"):
        evaluator_identity(tmp_path, source_trees=("evals",))
    (tmp_path / "evals" / "linked.py").unlink()
    (tmp_path / "evals" / "nested" / "one.py").write_text("ONE = 1\n", encoding="utf-8")
    with pytest.raises(EvalAttestationError, match="overlap"):
        evaluator_identity(tmp_path, source_trees=("evals", "evals/nested"))
    with pytest.raises(EvalAttestationError, match="safe repository-relative"):
        evaluator_identity(tmp_path, source_trees=("/etc",))


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
        },
    )


def test_git_source_status_never_assigns_dirty_bytes_to_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(
        repo,
        "-c",
        "user.name=Eval Test",
        "-c",
        "user.email=eval@example.test",
        "commit",
        "-qm",
        "initial",
    )

    clean = git_source_status(repo)
    assert clean["source_state"] == "clean"
    assert clean["source_revision"] == clean["head_revision"]

    tracked.write_text("dirty\n", encoding="utf-8")
    dirty = git_source_status(repo)
    assert dirty == {
        "source_state": "dirty",
        "head_revision": clean["head_revision"],
        "source_revision": None,
    }


def test_build_attestation_has_stable_context_and_full_attestation_digest() -> None:
    first = _attestation()
    second = _attestation(
        promotion=_promotion(
            eligible=False,
            evaluated_at="2026-07-30T22:46:00Z",
        )
    )

    assert first["context_version"] == second["context_version"]
    assert first["attestation_version"] != second["attestation_version"]
    context = {
        "subject": first["subject"],
        "evidence": first["evidence"],
        "protocol": first["protocol"],
    }
    assert first["context_version"] == canonical_digest(CONTEXT_SCHEMA, context)
    without_version = dict(first)
    without_version.pop("attestation_version")
    assert first["attestation_version"] == canonical_digest(ATTESTATION_SCHEMA, without_version)
    changed_protocol = _attestation(
        protocol={
            "mode": "smoke",
            "offline": False,
            "replicates": 1,
            "evaluator_version": "9" * 64,
        }
    )
    assert changed_protocol["context_version"] != first["context_version"]
    assert first["evidence"]["case_count"] == 2
    assert first["evidence"]["case_manifest"] == [
        {
            "case_id": "case-a",
            "case_semantics_version": case_semantics_version(
                {
                    "id": "case-a",
                    "question": "Question A",
                    "expected_behavior": "answer",
                }
            ),
        },
        {
            "case_id": "case-b",
            "case_semantics_version": case_semantics_version(
                {
                    "id": "case-b",
                    "question": "Question B",
                    "expected_behavior": "partial",
                }
            ),
        },
    ]


def test_build_attestation_accepts_validated_identities_without_serializing_payloads() -> None:
    secret = "sk-ant-super-secret-value"
    config_identity = build_config_identity(
        environment={"FPA_PROVIDER": "mock", "ANTHROPIC_API_KEY": secret}
    )
    raw = b"raw policy"
    observation = DocumentObservation.from_metadata(
        {
            "doc_id": "example-policy",
            "url": "https://example.test/policy",
            "final_url": "https://example.test/final-policy",
            "fetch_date": "2026-07-30",
            "http_status": 200,
            "format": "html",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        raw=raw,
        effective_format="html",
    )
    snapshot_identity = SnapshotIdentity(
        content_version="b" * 64,
        snapshot_version="c" * 64,
        observations=(observation,),
    )
    subject = _subject(
        config_version=config_identity.config_version,
        content_version=snapshot_identity.content_version,
        snapshot_version=snapshot_identity.snapshot_version,
    )

    result = _attestation(
        subject=subject,
        config_identity=config_identity,
        snapshot_identity=snapshot_identity,
    )
    serialized = json.dumps(result)
    assert secret not in serialized
    assert "ANTHROPIC_API_KEY" not in serialized
    assert "example.test/final-policy" not in serialized
    assert result["subject"]["config_version"] == config_identity.config_version


def test_build_attestation_rejects_secret_fields_and_false_promotion_claims() -> None:
    subject = {**_subject(), "api_key": "should-never-serialize"}
    with pytest.raises(EvalAttestationError, match="unexpected api_key"):
        _attestation(subject=subject)
    with pytest.raises(EvalAttestationError, match="sensitive field"):
        _attestation(
            protocol={
                "mode": "full",
                "environment": {"ANTHROPIC_API_KEY": "should-never-serialize"},
            }
        )

    dirty = _subject(state="dirty", descriptor_verified=False)
    with pytest.raises(EvalAttestationError, match="cannot be true"):
        _attestation(subject=dirty)
    invalid = _promotion(eligible=False)
    invalid["reasons"] = []
    with pytest.raises(EvalAttestationError, match="must explain"):
        _attestation(promotion=invalid)


def test_build_attestation_rejects_unverified_release_mismatch_and_bad_versions() -> None:
    subject = _subject()
    subject["release_version"] = "0" * 64
    with pytest.raises(EvalAttestationError, match="release_version does not match"):
        _attestation(subject=subject)
    with pytest.raises(EvalAttestationError, match="suite_version"):
        build_attestation(
            subject=_subject(),
            suite_version="short",
            case_manifest=[
                {
                    "case_id": "case-a",
                    "case_semantics_version": "1" * 64,
                }
            ],
            facts_version="f" * 64,
            gtfs_input_version="0" * 64,
            protocol={"mode": "full"},
            promotion=_promotion(),
        )


def test_build_attestation_rejects_manifest_count_duplicates_and_suite_mismatch() -> None:
    cases = [{"id": "case-a", "question": "Q", "expected_behavior": "answer"}]
    manifest = case_manifest(cases)
    with pytest.raises(EvalAttestationError, match="suite_version does not match"):
        build_attestation(
            subject=_subject(),
            suite_version="e" * 64,
            case_manifest=manifest,
            facts_version="f" * 64,
            gtfs_input_version="0" * 64,
            protocol={"mode": "full"},
            promotion=_promotion(),
        )
    with pytest.raises(EvalAttestationError, match="duplicate case id"):
        build_attestation(
            subject=_subject(),
            suite_version=suite_version(cases),
            case_manifest=[*manifest, *manifest],
            facts_version="f" * 64,
            gtfs_input_version="0" * 64,
            protocol={"mode": "full"},
            promotion=_promotion(),
        )


def test_default_evaluator_tree_rule_covers_current_eval_and_assistant_sources() -> None:
    identity = evaluator_identity(config.REPO_ROOT)
    paths = {receipt["path"] for receipt in identity["files"]}

    assert "evals/attestation.py" in paths
    assert "evals/runner.py" in paths
    assert "evals/checks.py" in paths
    assert "src/assistant/answer.py" in paths
    assert "src/assistant/release_identity.py" in paths
    assert identity["file_rule"] == "recursive-*.py-excluding-__pycache__"


def test_canonical_digest_accepts_finite_floats_and_repeated_container_aliases() -> None:
    shared = ["same", 1.25]
    payload = {"left": shared, "right": shared}

    assert canonical_digest("test.identity.v1", payload) == canonical_digest(
        "test.identity.v1",
        {"right": ["same", 1.25], "left": ["same", 1.25]},
    )


def test_canonical_digest_rejects_mapping_and_sequence_cycles() -> None:
    mapping_cycle: dict[str, object] = {}
    mapping_cycle["self"] = mapping_cycle
    sequence_cycle: list[object] = []
    sequence_cycle.append(sequence_cycle)

    with pytest.raises(EvalAttestationError, match="container cycle"):
        canonical_digest("test.identity.v1", mapping_cycle)
    with pytest.raises(EvalAttestationError, match="container cycle"):
        canonical_digest("test.identity.v1", sequence_cycle)


def test_canonical_digest_translates_unexpected_serializer_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_serialization(*_args: object, **_kwargs: object) -> str:
        raise ValueError("simulated serializer failure")

    monkeypatch.setattr(attestation_module.json, "dumps", fail_serialization)

    with pytest.raises(EvalAttestationError, match="canonical-JSON compatible"):
        canonical_digest("test.identity.v1", {"valid": "normalized"})


def test_file_receipt_supports_unanchored_files_and_rejects_unsafe_anchors(
    tmp_path: Path,
) -> None:
    target = tmp_path / "input.txt"
    target.write_bytes(b"exact")
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)

    assert file_receipt(target) == {
        "sha256": hashlib.sha256(b"exact").hexdigest(),
        "bytes": 5,
    }
    with pytest.raises(EvalAttestationError, match="dot path segments"):
        file_receipt(Path("nested") / ".." / "input.txt", root=tmp_path)
    with pytest.raises(EvalAttestationError, match="root is missing"):
        file_receipt("input.txt", root=tmp_path / "missing-root")
    with pytest.raises(EvalAttestationError, match="real directory"):
        file_receipt("input.txt", root=root_link)
    with pytest.raises(EvalAttestationError, match="parent is missing"):
        file_receipt("missing-parent/input.txt", root=tmp_path)
    with pytest.raises(EvalAttestationError, match="file is missing"):
        file_receipt(tmp_path / "missing.txt")


def test_file_receipt_fails_closed_across_open_and_read_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "input.txt"
    target.write_bytes(b"exact")
    original_open = attestation_module.os.open
    original_read = attestation_module.os.read

    def deny_open(_path: object, _flags: int) -> int:
        raise PermissionError("simulated open denial")

    monkeypatch.setattr(attestation_module.os, "open", deny_open)
    with pytest.raises(EvalAttestationError, match="opened safely"):
        file_receipt(target)

    def open_directory_instead(_path: object, _flags: int) -> int:
        return original_open(tmp_path, os.O_RDONLY)

    monkeypatch.setattr(attestation_module.os, "open", open_directory_instead)
    with pytest.raises(EvalAttestationError, match="opened path is not a regular file"):
        file_receipt(target)

    monkeypatch.setattr(attestation_module.os, "open", original_open)

    def fail_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("simulated read failure")

    monkeypatch.setattr(attestation_module.os, "read", fail_read)
    with pytest.raises(EvalAttestationError, match="read completely"):
        file_receipt(target)
    monkeypatch.setattr(attestation_module.os, "read", original_read)


def test_case_inputs_require_ordered_string_keyed_objects() -> None:
    with pytest.raises(EvalAttestationError, match="case must be an object"):
        case_semantics_version([])  # type: ignore[arg-type]
    with pytest.raises(EvalAttestationError, match="keys must be strings"):
        case_semantics_version({1: "ambiguous"})  # type: ignore[dict-item]
    with pytest.raises(EvalAttestationError, match="ordered sequence"):
        case_manifest("case-a")  # type: ignore[arg-type]
    with pytest.raises(EvalAttestationError, match=r"cases\[0\] must be an object"):
        case_manifest([[]])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("case-a", "ordered sequence"),
        ([], "must not be empty"),
        ([{"case_id": "case-a"}], "missing case_semantics_version"),
        (
            [
                {
                    "case_id": "case-a",
                    "case_semantics_version": "1" * 64,
                    "extra": True,
                }
            ],
            "unexpected extra",
        ),
        (
            [{"case_id": "../case-a", "case_semantics_version": "1" * 64}],
            "safe identifier",
        ),
        (
            [{"case_id": "case-a", "case_semantics_version": "A" * 64}],
            "lowercase SHA-256",
        ),
    ],
)
def test_build_attestation_rejects_malformed_case_manifest_records(
    manifest: object,
    message: str,
) -> None:
    with pytest.raises(EvalAttestationError, match=message):
        build_attestation(
            subject=_subject(),
            suite_version="e" * 64,
            case_manifest=manifest,  # type: ignore[arg-type]
            facts_version="f" * 64,
            gtfs_input_version="0" * 64,
            protocol={"mode": "full"},
            promotion=_promotion(),
        )


def test_gtfs_identity_rejects_malformed_manifests_and_receipts_missing_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(EvalAttestationError, match="missing gtfs_feeds"):
        gtfs_legacy_input_identity({}, tmp_path)
    with pytest.raises(EvalAttestationError, match="must be an array"):
        gtfs_legacy_input_identity({"gtfs_feeds": "MST"}, tmp_path)
    with pytest.raises(EvalAttestationError, match=r"gtfs_feeds\[0\] must be an object"):
        gtfs_legacy_input_identity([[]], tmp_path)  # type: ignore[list-item]

    missing_root = tmp_path / "not-downloaded"
    identity = gtfs_legacy_input_identity([{"agency": "MST"}], missing_root)
    assert identity["agencies"] == [{"agency": "MST", "state": "unavailable", "files": []}]


def test_gtfs_identity_rejects_non_directory_roots_and_agency_symlinks(
    tmp_path: Path,
) -> None:
    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(EvalAttestationError, match="real directory"):
        gtfs_legacy_input_identity([{"agency": "MST"}], root_file)

    root = tmp_path / "gtfs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "MST").symlink_to(outside, target_is_directory=True)
    with pytest.raises(EvalAttestationError, match="real directory"):
        gtfs_legacy_input_identity([{"agency": "MST"}], root)


def test_evaluator_identity_rejects_invalid_tree_collections_and_empty_sets(
    tmp_path: Path,
) -> None:
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "notes.txt").write_text("not Python", encoding="utf-8")

    with pytest.raises(EvalAttestationError, match="ordered sequence"):
        evaluator_identity(tmp_path, source_trees="evals")  # type: ignore[arg-type]
    with pytest.raises(EvalAttestationError, match="duplicates"):
        evaluator_identity(tmp_path, source_trees=("evals", "evals"))
    with pytest.raises(EvalAttestationError, match="unsafe path segment"):
        evaluator_identity(tmp_path, source_trees=("evals/unsafe name",))
    with pytest.raises(EvalAttestationError, match="no Python files"):
        evaluator_identity(tmp_path, source_trees=("evals",))


def test_evaluator_identity_rejects_directory_symlinks_and_ignores_bytecode(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "evals"
    tree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tree / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(EvalAttestationError, match="directory symlink"):
        evaluator_identity(tmp_path, source_trees=("evals",))

    linked.unlink()
    (tree / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    cache = tree / "__pycache__"
    cache.mkdir()
    (cache / "hidden.py").write_text("VALUE = 2\n", encoding="utf-8")
    identity = evaluator_identity(tmp_path, source_trees=("evals",))
    assert [item["path"] for item in identity["files"]] == ["evals/main.py"]


def test_git_source_status_wraps_git_failures_and_rejects_invalid_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(EvalAttestationError, match="could not inspect Git source state"):
        git_source_status(tmp_path)

    monkeypatch.setattr(attestation_module, "_git", lambda *_args: "not-a-full-object-id\n")
    with pytest.raises(EvalAttestationError, match="HEAD is not a full lowercase object ID"):
        git_source_status(tmp_path)


@pytest.mark.parametrize(
    "protocol",
    [
        {"metadata": {1: "ambiguous"}},
        {"metadata": ["safe", "Bearer top-secret"]},
        {"metadata": "-----BEGIN PRIVATE KEY-----"},
        {"metadata": "AKIA1234567890ABCDEF"},
        {"metadata": "sk-ant-secret-value"},
    ],
)
def test_protocol_rejects_nested_nonstring_keys_and_secret_values(
    protocol: dict[object, object],
) -> None:
    with pytest.raises(EvalAttestationError, match=r"keys must be strings|secret material"):
        _attestation(protocol=protocol)  # type: ignore[arg-type]


def test_protocol_allows_empty_public_arrays() -> None:
    assert _attestation(protocol={"mode": "full", "tags": []})["protocol"]["tags"] == []


@pytest.mark.parametrize(
    ("protocol", "message"),
    [
        ({}, "must not be empty"),
        ({"mode": "full", "protocol_version": "caller-value"}, "computed"),
        ({"mode": "full", "headers": {"x-request-id": "public"}}, "sensitive field"),
        ({"mode": "full", "token": "redacted"}, "sensitive field"),
    ],
)
def test_protocol_rejects_empty_computed_and_sensitive_fields(
    protocol: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(EvalAttestationError, match=message):
        _attestation(protocol=protocol)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"source_state": "unknown"}, "source_state"),
        ({"head_revision": "ABC"}, "full lowercase Git object ID"),
        ({"source_revision": "2" * 40}, "must equal"),
        ({"corpus_version": "D" * 12}, "compatibility digest"),
        ({"descriptor_verified": 1}, "must be a boolean"),
    ],
)
def test_subject_rejects_invalid_source_and_descriptor_fields(
    mutation: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(EvalAttestationError, match=message):
        _attestation(subject={**_subject(), **mutation})


def test_subject_rejects_inconsistent_dirty_and_wide_revision_claims() -> None:
    dirty_source = _subject(state="dirty", descriptor_verified=False)
    dirty_source["source_revision"] = dirty_source["head_revision"]
    with pytest.raises(EvalAttestationError, match="dirty subject.source_revision must be null"):
        _attestation(subject=dirty_source)

    dirty_release = _subject(state="dirty", descriptor_verified=False)
    dirty_release["release_version"] = "e" * 64
    with pytest.raises(EvalAttestationError, match="dirty subject.release_version must be null"):
        _attestation(subject=dirty_release)

    dirty_verified = _subject(state="dirty", descriptor_verified=False)
    dirty_verified["descriptor_verified"] = True
    with pytest.raises(EvalAttestationError, match="dirty subject cannot verify"):
        _attestation(subject=dirty_verified)

    wide_revision = _subject(descriptor_verified=False)
    wide_revision.update(
        {
            "head_revision": "1" * 64,
            "source_revision": "1" * 64,
            "release_version": "e" * 64,
            "descriptor_verified": True,
        }
    )
    with pytest.raises(EvalAttestationError, match="require a SHA-1 Git object ID"):
        _attestation(subject=wide_revision)


def test_subject_rejects_missing_fields_and_invalid_identity_objects() -> None:
    missing = _subject()
    missing.pop("snapshot_version")
    with pytest.raises(EvalAttestationError, match="missing snapshot_version"):
        _attestation(subject=missing)
    with pytest.raises(EvalAttestationError, match="validated ConfigIdentity"):
        _attestation(config_identity=object())  # type: ignore[arg-type]
    with pytest.raises(EvalAttestationError, match="does not match config_identity"):
        _attestation(config_identity=build_config_identity(environment={"FPA_PROVIDER": "mock"}))
    with pytest.raises(EvalAttestationError, match="validated SnapshotIdentity"):
        _attestation(snapshot_identity=object())  # type: ignore[arg-type]


def test_subject_rejects_snapshot_mismatch_and_translates_release_identity_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"raw policy"
    observation = DocumentObservation.from_metadata(
        {
            "doc_id": "example-policy",
            "url": "https://example.test/policy",
            "final_url": "https://example.test/policy",
            "fetch_date": "2026-07-30",
            "http_status": 200,
            "format": "html",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        raw=raw,
        effective_format="html",
    )
    snapshot = SnapshotIdentity(
        content_version="4" * 64,
        snapshot_version="5" * 64,
        observations=(observation,),
    )
    with pytest.raises(EvalAttestationError, match="do not match snapshot_identity"):
        _attestation(snapshot_identity=snapshot)

    def fail_release(*_args: object, **_kwargs: object) -> object:
        raise ReleaseIdentityError("simulated release identity rejection")

    monkeypatch.setattr(attestation_module, "build_release_identity", fail_release)
    with pytest.raises(EvalAttestationError, match="release tuple is invalid"):
        _attestation()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eligible", 1),
        ("live", "true"),
        ("uncached", None),
        ("judges_ran", 1.0),
        ("gates_passed", []),
    ],
)
def test_promotion_flags_require_exact_booleans(field: str, value: object) -> None:
    promotion = _promotion()
    promotion[field] = value
    with pytest.raises(EvalAttestationError, match=rf"promotion\.{field} must be a boolean"):
        _attestation(promotion=promotion)


@pytest.mark.parametrize(
    ("reasons", "message"),
    [
        ("offline", "must be an array"),
        (["offline", "offline"], "must not contain duplicates"),
        ([""], "non-empty, trimmed string"),
        ([1], "non-empty, trimmed string"),
    ],
)
def test_promotion_reasons_require_a_unique_trimmed_string_array(
    reasons: object,
    message: str,
) -> None:
    promotion = _promotion(eligible=False)
    promotion["reasons"] = reasons
    with pytest.raises(EvalAttestationError, match=message):
        _attestation(promotion=promotion)


def test_promotion_reasons_are_sorted_and_forbidden_on_eligible_runs() -> None:
    ineligible = _promotion(eligible=False)
    ineligible["reasons"] = ["z-last", "a-first"]
    assert _attestation(promotion=ineligible)["promotion"]["reasons"] == [
        "a-first",
        "z-last",
    ]

    eligible = _promotion()
    eligible["reasons"] = ["contradiction"]
    with pytest.raises(EvalAttestationError, match="must not carry rejection reasons"):
        _attestation(promotion=eligible)


@pytest.mark.parametrize(
    ("evaluated_at", "message"),
    [
        ("not-a-timestamp", "ISO-8601"),
        ("2026-07-30T22:45:00", "explicit UTC offset"),
        ("2026-07-30T22:45:00+01:00", "explicit UTC offset"),
    ],
)
def test_promotion_timestamp_requires_parseable_explicit_utc(
    evaluated_at: str,
    message: str,
) -> None:
    with pytest.raises(EvalAttestationError, match=message):
        _attestation(promotion=_promotion(evaluated_at=evaluated_at))
