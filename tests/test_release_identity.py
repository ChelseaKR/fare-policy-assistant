"""Behavior-complete configuration and logical-release identity tests."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from assistant import config, domain, release_identity
from assistant.ingest import Chunk
from assistant.release_identity import (
    PROMPT_NAMES,
    ReleaseDescriptor,
    ReleaseIdentityError,
    build_config_identity,
    build_release_descriptor,
    descriptor_bytes,
    history_key_id,
    load_release_descriptor,
    parse_release_descriptor,
    resolve_current_snapshot,
    verify_release_descriptor,
    write_release_descriptor,
)
from assistant.snapshots import archive_snapshot
from scripts import build_release_descriptor as descriptor_builder
from tests.conftest import make_chunk

_SOURCE_REVISION = "a" * 40
_CONTENT_VERSION = "b" * 64
_SNAPSHOT_VERSION = "c" * 64
_CORPUS_VERSION = "d" * 12
_HISTORY_SECRET = "1" * 64
_ROTATED_HISTORY_SECRET = "2" * 64


@dataclass(frozen=True)
class ConfigCase:
    environment: dict[str, str]
    resolved: config.Config
    prompts_dir: Path
    answer_schema_path: Path


@pytest.fixture
def config_case(tmp_path: Path) -> ConfigCase:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for name in PROMPT_NAMES:
        (prompts_dir / f"{name}.txt").write_text(
            f"# prompt-version: {name}-v1\nExact {name} behavior.\n",
            encoding="utf-8",
        )
    answer_schema_path = tmp_path / "answer-contract.schema.json"
    answer_schema_path.write_text(
        '{"additionalProperties":false,"required":["answer"],"type":"object"}\n',
        encoding="utf-8",
    )
    environment = {
        "FPA_PROVIDER": "bedrock",
        "FPA_ANSWER_MODEL": "answer-v1",
        "FPA_JUDGE_MODEL": "judge-v1",
        "FPA_DENSE": "0",
        "AWS_REGION": "us-west-2",
        "FPA_DOMAIN": "transit",
        "FPA_DISABLED_DOC_IDS": "yolobus-fares,sacrt-fares",
        "FPA_STALENESS_BUDGET_DAYS": "90",
        "FPA_EMBED_ANCESTORS": "https://staff.example 'self'",
        "FPA_HISTORY_HMAC_KEY": _HISTORY_SECRET,
    }
    resolved = config.Config(
        models=config.ModelConfig(
            provider="bedrock",
            answer_model="answer-v1",
            judge_model="judge-v1",
            max_tokens=1024,
            temperature=0.0,
        ),
        retrieval=config.RetrievalConfig(
            top_k=8,
            language_boost=1.2,
            decline_z_threshold=1.5,
            decline_coverage_floor=0.1,
            confidence_high_z=3.5,
            use_dense=False,
            dense_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            dense_weight=0.5,
        ),
    )
    return ConfigCase(environment, resolved, prompts_dir, answer_schema_path)


def _config(
    case: ConfigCase,
    *,
    environment: dict[str, str] | None = None,
    resolved: config.Config | None = None,
) -> release_identity.ConfigIdentity:
    return build_config_identity(
        case.environment if environment is None else environment,
        case.resolved if resolved is None else resolved,
        case.prompts_dir,
        case.answer_schema_path,
    )


def _descriptor(case: ConfigCase) -> ReleaseDescriptor:
    return build_release_descriptor(
        _SOURCE_REVISION,
        _config(case),
        content_version=_CONTENT_VERSION,
        snapshot_version=_SNAPSHOT_VERSION,
        corpus_version=_CORPUS_VERSION,
    )


def _mutable_descriptor(descriptor: ReleaseDescriptor) -> dict[str, object]:
    value = json.loads(descriptor_bytes(descriptor))
    assert isinstance(value, dict)
    return value


def _serialized(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_config_and_release_identity_have_stable_golden_values(config_case: ConfigCase) -> None:
    first = _descriptor(config_case)
    reversed_environment = dict(reversed(list(config_case.environment.items())))
    second_config = _config(config_case, environment=reversed_environment)
    second = build_release_descriptor(
        _SOURCE_REVISION,
        second_config,
        content_version=_CONTENT_VERSION,
        snapshot_version=_SNAPSHOT_VERSION,
        corpus_version=_CORPUS_VERSION,
    )

    assert first == second
    assert (
        first.config_version == "fc3d2beb3bbc69d9ad6a40de23182be892d51c7104fa753f9888fb43f0068e7f"
    )
    assert (
        first.release_version == "1488831f5bd16bfdd6f71ae535daa4f69a7b3de9b0e2c3f092e536e830692a90"
    )
    assert (
        hashlib.sha256(descriptor_bytes(first)).hexdigest()
        == "d9af5bab3c618ff5fa9c5f4e0adbc9e320479dfb72a97ff0817f38fa6d528ad5"
    )
    assert descriptor_bytes(first).endswith(b"\n")
    assert descriptor_bytes(first).count(b"\n") == 1


@pytest.mark.parametrize("prompt_name", PROMPT_NAMES)
def test_exact_prompt_body_changes_identity_even_when_header_is_unchanged(
    config_case: ConfigCase,
    prompt_name: str,
) -> None:
    baseline = _config(config_case)
    prompt = config_case.prompts_dir / f"{prompt_name}.txt"
    header = prompt.read_text(encoding="utf-8").splitlines()[0]

    prompt.write_text(f"{header}\nChanged body with the same version header.\n", encoding="utf-8")

    assert _config(config_case).config_version != baseline.config_version


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("answer_model", "answer-v2"),
        ("judge_model", "judge-v2"),
        ("max_tokens", 2048),
        ("temperature", 0.25),
    ],
)
def test_every_model_setting_changes_config_identity(
    config_case: ConfigCase,
    field: str,
    replacement: object,
) -> None:
    baseline = _config(config_case)
    changed_models = dataclasses.replace(
        config_case.resolved.models,
        **{field: replacement},
    )
    changed = dataclasses.replace(config_case.resolved, models=changed_models)

    assert _config(config_case, resolved=changed).config_version != baseline.config_version


def test_provider_choice_changes_config_identity(config_case: ConfigCase) -> None:
    baseline = _config(config_case)
    environment = {
        **config_case.environment,
        "FPA_PROVIDER": "anthropic",
    }
    changed = dataclasses.replace(
        config_case.resolved,
        models=dataclasses.replace(config_case.resolved.models, provider="anthropic"),
    )

    assert (
        _config(config_case, environment=environment, resolved=changed).config_version
        != baseline.config_version
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("top_k", 9),
        ("language_boost", 1.3),
        ("decline_z_threshold", 1.6),
        ("decline_coverage_floor", 0.2),
        ("confidence_high_z", 3.6),
        ("use_dense", True),
        ("dense_model", "dense-v2"),
        ("dense_weight", 0.6),
    ],
)
def test_every_retrieval_setting_changes_config_identity(
    config_case: ConfigCase,
    field: str,
    replacement: object,
) -> None:
    baseline = _config(config_case)
    changed_retrieval = dataclasses.replace(
        config_case.resolved.retrieval,
        **{field: replacement},
    )
    changed = dataclasses.replace(config_case.resolved, retrieval=changed_retrieval)

    assert _config(config_case, resolved=changed).config_version != baseline.config_version


@pytest.mark.parametrize(
    "mutation",
    ["name", "scopes", "aliases", "fallback_contact", "regex_pattern", "regex_flags"],
)
def test_every_domain_behavior_and_regex_input_changes_config_identity(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    baseline = _config(config_case)
    profile = domain.TRANSIT
    if mutation == "name":
        changed = dataclasses.replace(profile, name=profile.name + " revised")
    elif mutation == "scopes":
        changed = dataclasses.replace(profile, scopes=profile.scopes + ("NEW",))
    elif mutation == "aliases":
        changed = dataclasses.replace(profile, aliases={**profile.aliases, "new": "MST"})
    elif mutation == "fallback_contact":
        changed = dataclasses.replace(profile, fallback_contact="https://example.test/help")
    else:
        topics = dict(profile.scope_topics)
        legal = topics["legal_advice"]
        topics["legal_advice"] = (
            re.compile(legal.pattern + r"|tribunal", legal.flags)
            if mutation == "regex_pattern"
            else re.compile(legal.pattern, legal.flags | re.MULTILINE)
        )
        changed = dataclasses.replace(profile, scope_topics=topics)
    monkeypatch.setitem(domain._REGISTRY, "transit", changed)

    assert _config(config_case).config_version != baseline.config_version


def test_disabled_document_order_and_duplicates_are_canonical_but_set_changes_identity(
    config_case: ConfigCase,
) -> None:
    baseline = _config(config_case)
    reordered = {
        **config_case.environment,
        "FPA_DISABLED_DOC_IDS": " sacrt-fares, yolobus-fares, sacrt-fares ",
    }
    changed = {
        **config_case.environment,
        "FPA_DISABLED_DOC_IDS": "yolobus-fares",
    }

    assert _config(config_case, environment=reordered) == baseline
    assert _config(config_case, environment=changed).config_version != baseline.config_version


def test_staleness_and_embed_policy_each_change_identity(config_case: ConfigCase) -> None:
    baseline = _config(config_case)
    staleness = {**config_case.environment, "FPA_STALENESS_BUDGET_DAYS": "91"}
    embed = {
        **config_case.environment,
        "FPA_EMBED_ANCESTORS": "'self' https://partner.example",
    }
    reordered_embed = {
        **config_case.environment,
        "FPA_EMBED_ANCESTORS": "'self' https://staff.example https://staff.example",
    }

    assert _config(config_case, environment=staleness).config_version != baseline.config_version
    assert _config(config_case, environment=embed).config_version != baseline.config_version
    assert _config(config_case, environment=reordered_embed) == baseline


def test_history_key_rotation_changes_only_the_public_key_id(
    config_case: ConfigCase,
) -> None:
    baseline = _config(config_case)
    rotated_environment = {
        **config_case.environment,
        "FPA_HISTORY_HMAC_KEY": _ROTATED_HISTORY_SECRET,
    }
    rotated = _config(config_case, environment=rotated_environment)

    assert rotated.config_version != baseline.config_version
    baseline_signing = baseline.payload["runtime"]["history_signing"]  # type: ignore[index]
    rotated_signing = rotated.payload["runtime"]["history_signing"]  # type: ignore[index]
    assert baseline_signing == {
        "enabled": True,
        "key_id": history_key_id(_HISTORY_SECRET),
    }
    assert rotated_signing == {
        "enabled": True,
        "key_id": history_key_id(_ROTATED_HISTORY_SECRET),
    }


def test_declared_matching_history_key_id_does_not_create_a_second_identity_input(
    config_case: ConfigCase,
) -> None:
    baseline = _config(config_case)
    declared = {
        **config_case.environment,
        "FPA_HISTORY_HMAC_KEY_ID": history_key_id(_HISTORY_SECRET),
    }

    assert _config(config_case, environment=declared) == baseline


def test_every_provider_region_or_endpoint_is_an_opaque_transport_identity_input(
    config_case: ConfigCase,
) -> None:
    baseline = _config(config_case)
    other_region = {**config_case.environment, "AWS_REGION": "us-east-1"}
    other_bedrock_endpoint = {
        **config_case.environment,
        "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock-gateway.example/runtime",
    }
    anthropic_models = dataclasses.replace(
        config_case.resolved.models,
        provider="anthropic",
    )
    anthropic_config = dataclasses.replace(config_case.resolved, models=anthropic_models)
    anthropic_environment = {
        **config_case.environment,
        "FPA_PROVIDER": "anthropic",
        "ANTHROPIC_BASE_URL": "https://anthropic-gateway.example/v1",
    }
    local_models = dataclasses.replace(
        config_case.resolved.models,
        provider="local",
        answer_model="llama",
        judge_model="qwen",
    )
    local_config = dataclasses.replace(config_case.resolved, models=local_models)
    local_environment = {
        **config_case.environment,
        "FPA_PROVIDER": "local",
        "FPA_OLLAMA_HOST": "https://ollama.internal.example:11434",
    }
    changed_host = {
        **local_environment,
        "FPA_OLLAMA_HOST": "https://ollama-alt.internal.example:11434",
    }

    assert _config(config_case, environment=other_region).config_version != baseline.config_version
    assert (
        _config(config_case, environment=other_bedrock_endpoint).config_version
        != baseline.config_version
    )
    anthropic = _config(
        config_case,
        environment=anthropic_environment,
        resolved=anthropic_config,
    )
    local = _config(config_case, environment=local_environment, resolved=local_config)
    changed = _config(config_case, environment=changed_host, resolved=local_config)
    assert changed.config_version != local.config_version
    anthropic_transport = anthropic.payload["models"]["transport"]  # type: ignore[index]
    assert anthropic_transport == {
        "aws_region": None,
        "endpoint_sha256": hashlib.sha256(
            anthropic_environment["ANTHROPIC_BASE_URL"].encode()
        ).hexdigest(),
    }
    transport = local.payload["models"]["transport"]  # type: ignore[index]
    assert (
        transport["endpoint_sha256"]
        == hashlib.sha256(  # type: ignore[index]
            local_environment["FPA_OLLAMA_HOST"].encode()
        ).hexdigest()
    )
    assert (
        anthropic_environment["ANTHROPIC_BASE_URL"]
        not in descriptor_bytes(
            build_release_descriptor(
                _SOURCE_REVISION,
                anthropic,
                content_version=_CONTENT_VERSION,
                snapshot_version=_SNAPSHOT_VERSION,
                corpus_version=_CORPUS_VERSION,
            )
        ).decode()
    )


def test_local_origin_trailing_slash_is_canonical(config_case: ConfigCase) -> None:
    local_models = dataclasses.replace(config_case.resolved.models, provider="local")
    local_config = dataclasses.replace(config_case.resolved, models=local_models)
    environment = {
        **config_case.environment,
        "FPA_PROVIDER": "local",
        "FPA_OLLAMA_HOST": "http://localhost:11434",
    }
    with_slash = {**environment, "FPA_OLLAMA_HOST": "http://localhost:11434/"}

    assert _config(config_case, environment=environment, resolved=local_config) == _config(
        config_case,
        environment=with_slash,
        resolved=local_config,
    )


@pytest.mark.parametrize(
    ("provider", "environment_update", "message"),
    [
        (
            "anthropic",
            {"ANTHROPIC_BASE_URL": "ftp://gateway.example"},
            "ANTHROPIC_BASE_URL",
        ),
        (
            "anthropic",
            {"ANTHROPIC_BASE_URL": "http://gateway.example"},
            "ANTHROPIC_BASE_URL",
        ),
        (
            "anthropic",
            {"ANTHROPIC_BASE_URL": "https://user:secret@gateway.example"},
            "ANTHROPIC_BASE_URL",
        ),
        (
            "anthropic",
            {"ANTHROPIC_CUSTOM_HEADERS": "X-Secret: untracked-secret"},
            "ANTHROPIC_CUSTOM_HEADERS",
        ),
        (
            "bedrock",
            {"ANTHROPIC_BEDROCK_BASE_URL": "https://gateway.example?token=secret"},
            "ANTHROPIC_BEDROCK_BASE_URL",
        ),
        (
            "bedrock",
            {"ANTHROPIC_BEDROCK_BASE_URL": "http://gateway.example"},
            "ANTHROPIC_BEDROCK_BASE_URL",
        ),
        (
            "local",
            {"FPA_OLLAMA_HOST": "https://kiosk.example/ollama"},
            "FPA_OLLAMA_HOST",
        ),
    ],
)
def test_provider_endpoint_validation_fails_closed_before_identity_hashing(
    config_case: ConfigCase,
    provider: str,
    environment_update: dict[str, str],
    message: str,
) -> None:
    environment = {
        **config_case.environment,
        "FPA_PROVIDER": provider,
        **environment_update,
    }
    models = dataclasses.replace(config_case.resolved.models, provider=provider)
    resolved = dataclasses.replace(config_case.resolved, models=models)

    with pytest.raises(ReleaseIdentityError, match=message):
        _config(config_case, environment=environment, resolved=resolved)


@pytest.mark.parametrize(
    "constant",
    [
        "MAX_QUESTION_CHARS",
        "MAX_BODY_BYTES",
        "REQUESTS_PER_MINUTE",
        "ANSWER_CACHE_SIZE",
        "MAX_HISTORY_TURNS",
        "MAX_HISTORY_ANSWER_CHARS",
    ],
)
def test_every_runtime_limit_changes_config_identity(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
) -> None:
    baseline = _config(config_case)
    monkeypatch.setattr(config, constant, getattr(config, constant) + 1)

    assert _config(config_case).config_version != baseline.config_version


def test_answer_cache_key_contract_changes_config_identity(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _config(config_case)
    monkeypatch.setattr(config, "ANSWER_CACHE_KEY_SCHEMA", "fare-assistant.answer-cache.v999")

    assert _config(config_case).config_version != baseline.config_version


@pytest.mark.parametrize(
    ("constant", "replacement"),
    [
        ("JUDGE_MAX_TOKENS", 513),
        ("JUDGE_TEMPERATURE", 0.1),
    ],
)
def test_every_judge_call_limit_changes_config_identity(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    replacement: object,
) -> None:
    baseline = _config(config_case)
    monkeypatch.setattr(config, constant, replacement)

    assert _config(config_case).config_version != baseline.config_version


def test_exact_answer_contract_bytes_change_config_identity(config_case: ConfigCase) -> None:
    baseline = _config(config_case)
    parsed = json.loads(config_case.answer_schema_path.read_text(encoding="utf-8"))
    config_case.answer_schema_path.write_text(
        json.dumps(parsed, indent=2) + "\n",
        encoding="utf-8",
    )

    assert _config(config_case).config_version != baseline.config_version


@pytest.mark.parametrize(
    ("environment_update", "message"),
    [
        (
            {"FPA_HISTORY_HMAC_KEY": "", "FPA_HISTORY_HMAC_KEY_ID": "0" * 64},
            "cannot be declared when history signing is disabled",
        ),
        (
            {"FPA_HISTORY_HMAC_KEY_ID": "0" * 64},
            "declared history signing key ID does not match",
        ),
        ({"FPA_DISABLED_DOC_IDS": "UPPERCASE"}, "must contain lowercase letters"),
        ({"FPA_STALENESS_BUDGET_DAYS": "-1"}, "must be a positive integer"),
        ({"FPA_STALENESS_BUDGET_DAYS": "0"}, "must be a positive integer"),
        ({"FPA_EMBED_ANCESTORS": ""}, "must not be empty"),
        (
            {"FPA_EMBED_ANCESTORS": "'none' 'self'"},
            "cannot be combined with other embed ancestors",
        ),
        ({"FPA_EMBED_ANCESTORS": "javascript:"}, "contains an invalid CSP source"),
        ({"FPA_EMBED_ANCESTORS": "https://example.test:65536"}, "contains an invalid port"),
        ({"FPA_DOMAIN": " transit"}, "must be a trimmed string"),
        ({"FPA_DOMAIN": "unknown"}, "unknown domain profile key"),
    ],
)
def test_configuration_environment_validation_fails_closed(
    config_case: ConfigCase,
    environment_update: dict[str, str],
    message: str,
) -> None:
    environment = {**config_case.environment, **environment_update}

    with pytest.raises(ReleaseIdentityError, match=re.escape(message)):
        _config(config_case, environment=environment)


def test_build_config_rejects_non_mapping_or_wrong_resolved_config(
    config_case: ConfigCase,
) -> None:
    with pytest.raises(ReleaseIdentityError, match="environment must be a string mapping"):
        build_config_identity(  # type: ignore[arg-type]
            [],
            config_case.resolved,
            config_case.prompts_dir,
            config_case.answer_schema_path,
        )
    with pytest.raises(ReleaseIdentityError, match="resolved_config must be"):
        build_config_identity(
            config_case.environment,
            object(),  # type: ignore[arg-type]
            config_case.prompts_dir,
            config_case.answer_schema_path,
        )


def test_build_config_wraps_resolution_errors_without_accepting_partial_config(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolution(_environment: object) -> config.Config:
        raise ValueError("invalid model setting")

    monkeypatch.setattr(config.Config, "from_environment", fail_resolution)

    with pytest.raises(ReleaseIdentityError, match="model or retrieval environment is invalid"):
        build_config_identity(
            config_case.environment,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
        )


def test_build_config_rejects_missing_empty_and_non_utf8_bundle_inputs(
    config_case: ConfigCase,
) -> None:
    system_prompt = config_case.prompts_dir / "system.txt"
    system_prompt.unlink()
    with pytest.raises(ReleaseIdentityError, match="system prompt is missing"):
        _config(config_case)

    system_prompt.write_bytes(b"")
    with pytest.raises(ReleaseIdentityError, match="system prompt must not be empty"):
        _config(config_case)

    system_prompt.write_bytes(b"\xff")
    with pytest.raises(ReleaseIdentityError, match="system prompt must be valid UTF-8"):
        _config(config_case)


def test_build_config_wraps_bundle_read_and_answer_contract_errors(
    config_case: ConfigCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_contract = config_case.answer_schema_path
    original_read_bytes = Path.read_bytes

    def fail_contract_read(path: Path) -> bytes:
        if path == answer_contract:
            raise OSError("injected read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_contract_read)
    with pytest.raises(ReleaseIdentityError, match="answer contract could not be read"):
        _config(config_case)

    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)
    answer_contract.write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError, match="answer contract must contain valid JSON"):
        _config(config_case)


def test_descriptor_and_builder_summary_never_serialize_secrets(
    config_case: ConfigCase,
) -> None:
    secret_environment = {
        **config_case.environment,
        "ANTHROPIC_API_KEY": "anthropic-super-secret",
        "ANTHROPIC_AUTH_TOKEN": "anthropic-auth-super-secret",
        "ANTHROPIC_CUSTOM_HEADERS": "X-Secret: anthropic-header-super-secret",
        "AWS_ACCESS_KEY_ID": "AKIA_TEST_SECRET",
        "AWS_SECRET_ACCESS_KEY": "aws-super-secret",
        "AWS_SESSION_TOKEN": "aws-session-secret",
    }
    identity = _config(config_case, environment=secret_environment)
    descriptor = build_release_descriptor(
        _SOURCE_REVISION,
        identity,
        content_version=_CONTENT_VERSION,
        snapshot_version=_SNAPSHOT_VERSION,
        corpus_version=_CORPUS_VERSION,
    )
    serialized = descriptor_bytes(descriptor).decode()
    summary = json.dumps(
        descriptor_builder._secret_free_environment_summary(
            descriptor,
            Path("release/release.json"),
        )
    )

    for secret in (
        _HISTORY_SECRET,
        "anthropic-super-secret",
        "anthropic-auth-super-secret",
        "anthropic-header-super-secret",
        "AKIA_TEST_SECRET",
        "aws-super-secret",
        "aws-session-secret",
    ):
        assert secret not in serialized
        assert secret not in summary
    assert history_key_id(_HISTORY_SECRET) in serialized
    assert history_key_id(_HISTORY_SECRET) in summary


def test_unrelated_secret_environment_does_not_change_config_identity(
    config_case: ConfigCase,
) -> None:
    baseline = _config(config_case)
    secret_environment = {
        **config_case.environment,
        "ANTHROPIC_API_KEY": "not-an-identity-input",
        "ANTHROPIC_AUTH_TOKEN": "not-an-identity-input-either",
        "ANTHROPIC_CUSTOM_HEADERS": "X-Secret: still-not-an-identity-input",
        "AWS_SECRET_ACCESS_KEY": "not-an-identity-input-either",
    }

    assert _config(config_case, environment=secret_environment) == baseline


def test_descriptor_round_trip_is_canonical_and_strict(config_case: ConfigCase) -> None:
    descriptor = _descriptor(config_case)
    parsed = parse_release_descriptor(descriptor_bytes(descriptor))

    assert parsed == descriptor
    assert descriptor_bytes(parsed) == descriptor_bytes(descriptor)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda value: value.pop("source_state"), "missing source_state"),
        (lambda value: value.__setitem__("created_at", "now"), "unexpected created_at"),
        (
            lambda value: value["compatibility"].__setitem__("extra", True),  # type: ignore[union-attr]
            "unexpected extra",
        ),
        (
            lambda value: value["evidence"][0].__setitem__("extra", True),  # type: ignore[index,union-attr]
            "unexpected extra",
        ),
        (
            lambda value: value["config"]["models"].__setitem__("extra", True),  # type: ignore[index,union-attr]
            "unexpected extra",
        ),
    ],
)
def test_descriptor_rejects_missing_or_unknown_fields_at_every_layer(
    config_case: ConfigCase,
    operation,
    message: str,
) -> None:
    payload = _mutable_descriptor(_descriptor(config_case))
    operation(payload)

    with pytest.raises(ReleaseIdentityError, match=message):
        parse_release_descriptor(_serialized(payload))


def test_descriptor_rejects_duplicate_json_keys_even_when_values_match(
    config_case: ConfigCase,
) -> None:
    serialized = descriptor_bytes(_descriptor(config_case)).decode()
    duplicate = serialized.replace(
        '"source_state":"clean"',
        '"source_state":"clean","source_state":"clean"',
        1,
    )

    with pytest.raises(ReleaseIdentityError, match="duplicate object key: source_state"):
        parse_release_descriptor(duplicate)


def test_descriptor_rejects_non_finite_json_numbers(config_case: ConfigCase) -> None:
    serialized = descriptor_bytes(_descriptor(config_case)).decode()
    invalid = serialized.replace('"temperature":0.0', '"temperature":NaN', 1)

    with pytest.raises(ReleaseIdentityError, match="invalid JSON number literal NaN"):
        parse_release_descriptor(invalid)


def _replace_nested_value(
    payload: dict[str, object],
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    current: object = payload
    for component in path[:-1]:
        if isinstance(component, int):
            assert isinstance(current, list)
            current = current[component]
        else:
            assert isinstance(current, dict)
            current = current[component]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(current, list)
        current[final] = replacement
    else:
        assert isinstance(current, dict)
        current[final] = replacement


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("config_schema",), "fare-assistant.config.v999", "unsupported config_schema"),
        (("release_schema",), "fare-assistant.release.v999", "unsupported release_schema"),
        (("evidence",), {}, "descriptor.evidence must be an array"),
        (("evidence",), [], "requires exactly one web_policy evidence entry"),
        (
            ("compatibility", "corpus_version"),
            "D" * 12,
            "12-character lowercase compatibility digest",
        ),
        (
            ("source_revision",),
            "A" * 40,
            "full 40-character lowercase Git object ID",
        ),
        (("config_version",), "short", "64-character lowercase SHA-256"),
        (("config", "models", "provider"), "unsupported", "models.provider is unsupported"),
        (
            ("config", "models", "transport", "aws_region"),
            None,
            "bedrock transport must contain a valid AWS region",
        ),
        (
            ("config", "models", "provider"),
            "anthropic",
            "anthropic transport must contain only an endpoint digest",
        ),
        (
            ("config", "models", "provider"),
            "mock",
            "mock transport fields must be null",
        ),
        (
            ("config", "models", "answer", "max_tokens"),
            0,
            "models.answer.max_tokens must be an integer >= 1",
        ),
        (
            ("config", "models", "answer", "temperature"),
            0,
            "models.answer.temperature must be a finite JSON float",
        ),
        (
            ("config", "retrieval", "use_dense"),
            1,
            "retrieval.use_dense must be a boolean",
        ),
        (
            ("config", "prompts", "system", "bytes"),
            0,
            "prompts.system.bytes must be an integer >= 1",
        ),
        (("config", "domain", "key"), "Transit", "domain.key has an invalid format"),
        (("config", "domain", "scopes"), [], "domain.scopes must not be empty"),
        (
            ("config", "domain", "scopes"),
            ["MST", "MST"],
            "domain.scopes must not contain duplicates",
        ),
        (("config", "domain", "aliases"), {}, "domain.aliases must not be empty"),
        (
            ("config", "domain", "aliases"),
            {"mst": "UNKNOWN"},
            "names an unknown scope",
        ),
        (
            ("config", "domain", "scope_topics"),
            {"broken": {"pattern": "(", "flags": 0}},
            "pattern is not a valid regular expression",
        ),
        (
            ("config", "source_policy", "disabled_document_ids"),
            ["yolobus-fares", "sacrt-fares"],
            "disabled_document_ids must be sorted and unique",
        ),
        (
            ("config", "runtime", "embed_ancestors"),
            [],
            "runtime.embed_ancestors must not be empty",
        ),
        (
            ("config", "runtime", "embed_ancestors"),
            ["'self'", "'self'"],
            "runtime.embed_ancestors must be sorted and unique",
        ),
        (
            ("config", "runtime", "embed_ancestors"),
            ["'none'", "'self'"],
            "cannot combine 'none' with other sources",
        ),
        (
            ("config", "runtime", "embed_ancestors"),
            ["javascript:"],
            "contains an invalid CSP source",
        ),
        (
            ("config", "runtime", "embed_ancestors"),
            ["https://example.test:65536"],
            "contains an invalid port",
        ),
        (
            ("config", "runtime", "history_signing", "enabled"),
            False,
            "key_id must be null when signing is disabled",
        ),
        (
            ("config", "runtime", "history_signing", "key_id"),
            None,
            "history_signing.key_id must be a non-empty",
        ),
        (
            ("config", "runtime", "limits", "max_question_chars"),
            0,
            "runtime.limits.max_question_chars must be an integer >= 1",
        ),
        (
            ("config", "runtime", "answer_cache_key_schema"),
            "",
            "answer_cache_key_schema must be a non-empty",
        ),
        (
            ("config", "answer_contract", "bytes"),
            0,
            "answer_contract.bytes must be an integer >= 1",
        ),
        (
            ("config", "judge_calls", "groundedness", "temperature"),
            0,
            "judge_calls.groundedness.temperature must be a finite JSON float",
        ),
    ],
)
def test_descriptor_schema_validation_rejects_unsafe_nested_values(
    config_case: ConfigCase,
    path: tuple[str | int, ...],
    replacement: object,
    message: str,
) -> None:
    payload = _mutable_descriptor(_descriptor(config_case))
    _replace_nested_value(payload, path, replacement)

    with pytest.raises(ReleaseIdentityError, match=re.escape(message)):
        parse_release_descriptor(_serialized(payload))


@pytest.mark.parametrize("serialized", ["{", "[]", "null"])
def test_descriptor_rejects_invalid_json_or_non_object_roots(serialized: str) -> None:
    with pytest.raises(ReleaseIdentityError):
        parse_release_descriptor(serialized)


def test_config_payload_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(ReleaseIdentityError, match="object keys must be strings"):
        release_identity.ConfigIdentity("0" * 64, {1: "unsafe"})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "mutation",
    [
        "config_payload",
        "config_version",
        "release_version",
        "source_revision",
        "content_version",
        "snapshot_version",
        "evidence_scope",
        "source_state",
        "descriptor_schema",
    ],
)
def test_descriptor_rejects_identity_or_contract_tampering(
    config_case: ConfigCase,
    mutation: str,
) -> None:
    payload = _mutable_descriptor(_descriptor(config_case))
    if mutation == "config_payload":
        payload["config"]["models"]["answer"]["model_id"] = "substituted"  # type: ignore[index]
    elif mutation == "config_version":
        payload["config_version"] = "0" * 64
    elif mutation == "release_version":
        payload["release_version"] = "0" * 64
    elif mutation == "source_revision":
        payload["source_revision"] = "0" * 40
    elif mutation == "content_version":
        payload["evidence"][0]["content_version"] = "0" * 64  # type: ignore[index]
    elif mutation == "snapshot_version":
        payload["evidence"][0]["snapshot_version"] = "0" * 64  # type: ignore[index]
    elif mutation == "evidence_scope":
        payload["evidence"][0]["scope"] = "gtfs"  # type: ignore[index]
    elif mutation == "source_state":
        payload["source_state"] = "dirty"
    else:
        payload["descriptor_schema"] = "fare-assistant.release-descriptor.v999"

    with pytest.raises(ReleaseIdentityError):
        parse_release_descriptor(_serialized(payload))


def test_domain_behavior_cannot_be_tampered_without_rehashing_profile_and_config(
    config_case: ConfigCase,
) -> None:
    payload = _mutable_descriptor(_descriptor(config_case))
    payload["config"]["domain"]["scope_topics"]["legal_advice"]["pattern"] = "anything"  # type: ignore[index]

    with pytest.raises(ReleaseIdentityError, match="profile_version"):
        parse_release_descriptor(_serialized(payload))


def test_write_is_deterministic_canonical_and_loadable(
    config_case: ConfigCase,
    tmp_path: Path,
) -> None:
    descriptor = _descriptor(config_case)
    first = tmp_path / "one" / "release.json"
    second = tmp_path / "two" / "release.json"

    assert write_release_descriptor(descriptor, first) == first
    assert write_release_descriptor(descriptor, second) == second
    assert first.read_bytes() == second.read_bytes() == descriptor_bytes(descriptor)
    assert first.stat().st_mode & 0o777 == 0o644
    assert load_release_descriptor(first) == descriptor


def test_atomic_write_failure_preserves_prior_descriptor_and_cleans_stage(
    config_case: ConfigCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "release.json"
    prior = b'{"prior":"descriptor"}\n'
    target.write_bytes(prior)
    entries_before = sorted(path.name for path in tmp_path.iterdir())

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(release_identity.os, "replace", fail_replace)

    with pytest.raises(ReleaseIdentityError, match="could not be written"):
        write_release_descriptor(_descriptor(config_case), target)
    assert target.read_bytes() == prior
    assert sorted(path.name for path in tmp_path.iterdir()) == entries_before


def test_writer_refuses_to_replace_a_descriptor_symlink(
    config_case: ConfigCase,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination.json"
    destination.write_text("owned", encoding="utf-8")
    link = tmp_path / "release.json"
    link.symlink_to(destination)

    with pytest.raises(ReleaseIdentityError, match="descriptor symlink"):
        write_release_descriptor(_descriptor(config_case), link)
    assert destination.read_text(encoding="utf-8") == "owned"


def test_loader_rejects_missing_symlinked_and_unreadable_descriptors(
    config_case: ConfigCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ReleaseIdentityError, match="missing or is not a regular file"):
        load_release_descriptor(missing)

    target = tmp_path / "target.json"
    target.write_bytes(descriptor_bytes(_descriptor(config_case)))
    link = tmp_path / "release.json"
    link.symlink_to(target)
    with pytest.raises(ReleaseIdentityError, match="missing or is not a regular file"):
        load_release_descriptor(link)

    original_read_bytes = Path.read_bytes

    def fail_target_read(path: Path) -> bytes:
        if path == target:
            raise OSError("injected read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    with pytest.raises(ReleaseIdentityError, match="release descriptor could not be read"):
        load_release_descriptor(target)


def test_release_value_objects_reject_wrong_types_and_crossed_identities(
    config_case: ConfigCase,
) -> None:
    descriptor = _descriptor(config_case)
    release_arguments = {
        "release_version": "0" * 64,
        "source_revision": descriptor.source_revision,
        "config_version": descriptor.config_version,
    }
    with pytest.raises(ReleaseIdentityError, match="requires exactly one"):
        release_identity.ReleaseIdentity(**release_arguments, evidence=[])  # type: ignore[arg-type]
    with pytest.raises(ReleaseIdentityError, match="invalid value object"):
        release_identity.ReleaseIdentity(**release_arguments, evidence=(object(),))  # type: ignore[arg-type]
    with pytest.raises(ReleaseIdentityError, match="config must be a ConfigIdentity"):
        release_identity.ReleaseDescriptor(  # type: ignore[arg-type]
            config=object(),
            release=descriptor.release,
            corpus_version=descriptor.corpus_version,
        )
    with pytest.raises(ReleaseIdentityError, match="release must be a ReleaseIdentity"):
        release_identity.ReleaseDescriptor(  # type: ignore[arg-type]
            config=descriptor.config,
            release=object(),
            corpus_version=descriptor.corpus_version,
        )

    changed_environment = {**config_case.environment, "FPA_STALENESS_BUDGET_DAYS": "91"}
    changed_config = _config(config_case, environment=changed_environment)
    changed_release = release_identity.build_release_identity(
        descriptor.source_revision,
        changed_config.config_version,
        content_version=descriptor.content_version,
        snapshot_version=descriptor.snapshot_version,
    )
    with pytest.raises(ReleaseIdentityError, match="config and release identities disagree"):
        release_identity.ReleaseDescriptor(
            config=descriptor.config,
            release=changed_release,
            corpus_version=descriptor.corpus_version,
        )

    with pytest.raises(ReleaseIdentityError, match="config_identity must be"):
        build_release_descriptor(  # type: ignore[arg-type]
            _SOURCE_REVISION,
            object(),
            content_version=_CONTENT_VERSION,
            snapshot_version=_SNAPSHOT_VERSION,
            corpus_version=_CORPUS_VERSION,
        )
    with pytest.raises(ReleaseIdentityError, match="descriptor must be"):
        descriptor_bytes(object())  # type: ignore[arg-type]


@dataclass(frozen=True)
class SnapshotCase:
    chunk: Chunk
    chunks_path: Path
    manifest_path: Path
    raw_dir: Path
    snapshots_dir: Path


def _write_chunks(path: Path, chunks: list[Chunk]) -> None:
    rows = [
        json.dumps(dataclasses.asdict(chunk), sort_keys=True, separators=(",", ":"))
        for chunk in chunks
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_raw_source(raw_dir: Path, chunk: Chunk, raw: bytes) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{chunk.doc_id}.html").write_bytes(raw)
    metadata = {
        "doc_id": chunk.doc_id,
        "url": chunk.url,
        "final_url": chunk.url,
        "fetch_date": chunk.fetch_date,
        "http_status": 200,
        "format": "html",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    (raw_dir / f"{chunk.doc_id}.meta.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture
def snapshot_case(tmp_path: Path) -> SnapshotCase:
    chunk = make_chunk(text="The exact retained policy says the fare is $2.00.")
    chunks_path = tmp_path / "processed" / "chunks.jsonl"
    manifest_path = tmp_path / "manifest.yaml"
    raw_dir = tmp_path / "raw"
    snapshots_dir = tmp_path / "snapshots"
    manifest = {
        "documents": [
            {
                "id": chunk.doc_id,
                "agency": chunk.agency,
                "agency_full": chunk.agency_full,
                "title": chunk.doc_title,
                "url": chunk.url,
                "language": chunk.language,
                "format": "html",
            }
        ]
    }
    _write_chunks(chunks_path, [chunk])
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _write_raw_source(raw_dir, chunk, b"<main>Exact retained policy: $2.00.</main>")
    archive_snapshot(
        [chunk],
        manifest,
        raw_dir=raw_dir,
        snapshots_dir=snapshots_dir,
        archived_at="2026-07-30T12:34:56+00:00",
    )
    return SnapshotCase(chunk, chunks_path, manifest_path, raw_dir, snapshots_dir)


def test_exact_current_snapshot_is_resolved_and_builder_binds_it(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
) -> None:
    identity = resolve_current_snapshot(
        chunks_path=snapshot_case.chunks_path,
        manifest_path=snapshot_case.manifest_path,
        raw_dir=snapshot_case.raw_dir,
        snapshots_dir=snapshot_case.snapshots_dir,
    )
    descriptor = descriptor_builder.build_current_descriptor(
        _SOURCE_REVISION,
        environment=config_case.environment,
        chunks_path=snapshot_case.chunks_path,
        manifest_path=snapshot_case.manifest_path,
        raw_dir=snapshot_case.raw_dir,
        snapshots_dir=snapshot_case.snapshots_dir,
        prompts_dir=config_case.prompts_dir,
        answer_schema_path=config_case.answer_schema_path,
    )

    assert descriptor.content_version == identity.content_version
    assert descriptor.snapshot_version == identity.snapshot_version
    assert descriptor.config == _config(config_case)


def test_resolution_rejects_current_source_bytes_without_their_exact_archive(
    snapshot_case: SnapshotCase,
) -> None:
    _write_raw_source(
        snapshot_case.raw_dir,
        snapshot_case.chunk,
        b"<main>A newer but not archived policy says $2.25.</main>",
    )

    with pytest.raises(ReleaseIdentityError, match="exact current source snapshot"):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )


def test_resolution_rejects_an_archived_chunk_substitution(
    snapshot_case: SnapshotCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substituted = dataclasses.replace(snapshot_case.chunk, text="Substituted archived behavior.")
    monkeypatch.setattr(
        release_identity,
        "load_snapshot_chunks",
        lambda _version, _root: [substituted],
    )

    with pytest.raises(ReleaseIdentityError, match="archived snapshot chunks"):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )


def test_resolution_rejects_missing_or_symlinked_manifest(
    snapshot_case: SnapshotCase,
    tmp_path: Path,
) -> None:
    manifest_bytes = snapshot_case.manifest_path.read_bytes()
    snapshot_case.manifest_path.unlink()
    with pytest.raises(ReleaseIdentityError, match="manifest is missing"):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )

    target = tmp_path / "manifest-target.yaml"
    target.write_bytes(manifest_bytes)
    snapshot_case.manifest_path.symlink_to(target)
    with pytest.raises(ReleaseIdentityError, match="manifest is missing"):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )


@pytest.mark.parametrize("malformed_input", ["chunks", "yaml", "manifest_type"])
def test_resolution_rejects_malformed_current_bundle_inputs(
    snapshot_case: SnapshotCase,
    malformed_input: str,
) -> None:
    if malformed_input == "chunks":
        snapshot_case.chunks_path.write_text("{\n", encoding="utf-8")
        message = "current chunks or manifest are malformed"
    elif malformed_input == "yaml":
        snapshot_case.manifest_path.write_text("documents: [\n", encoding="utf-8")
        message = "current chunks or manifest are malformed"
    else:
        snapshot_case.manifest_path.write_text("- not\n- an\n- object\n", encoding="utf-8")
        message = "current manifest must be an object"

    with pytest.raises(ReleaseIdentityError, match=message):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )


def test_resolution_rejects_archived_identity_mismatch(
    snapshot_case: SnapshotCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = resolve_current_snapshot(
        chunks_path=snapshot_case.chunks_path,
        manifest_path=snapshot_case.manifest_path,
        raw_dir=snapshot_case.raw_dir,
        snapshots_dir=snapshot_case.snapshots_dir,
    )
    substituted = dataclasses.replace(expected, snapshot_version="e" * 64)
    monkeypatch.setattr(
        release_identity,
        "validate_snapshot_archive",
        lambda _archive: substituted,
    )

    with pytest.raises(ReleaseIdentityError, match="identity does not match current source"):
        resolve_current_snapshot(
            chunks_path=snapshot_case.chunks_path,
            manifest_path=snapshot_case.manifest_path,
            raw_dir=snapshot_case.raw_dir,
            snapshots_dir=snapshot_case.snapshots_dir,
        )


def test_clean_source_revision_requires_tracked_and_untracked_cleanliness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(_root: Path, *arguments: str) -> str:
        calls.append(arguments)
        if arguments == ("rev-parse", "HEAD"):
            return _SOURCE_REVISION
        if arguments == ("status", "--porcelain", "--untracked-files=normal"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(descriptor_builder, "_git", fake_git)

    assert descriptor_builder.clean_source_revision(tmp_path) == _SOURCE_REVISION
    assert calls == [
        ("rev-parse", "HEAD"),
        ("status", "--porcelain", "--untracked-files=normal"),
    ]

    def dirty_git(_root: Path, *arguments: str) -> str:
        return _SOURCE_REVISION if arguments == ("rev-parse", "HEAD") else "?? untracked.txt"

    # Legacy test/deploy environment names must not weaken the clean-source
    # invariant even when a caller supplies both of them.
    monkeypatch.setenv("FPA_ALLOW_DIRTY_DEPLOY", "1")
    monkeypatch.setenv("FPA_DEPLOY_TEST_MODE", "1")
    monkeypatch.setattr(descriptor_builder, "_git", dirty_git)
    with pytest.raises(ReleaseIdentityError, match="working tree is dirty"):
        descriptor_builder.clean_source_revision(tmp_path)


def test_builder_main_rejects_a_supplied_revision_that_is_not_clean_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        descriptor_builder,
        "clean_source_revision",
        lambda _root: _SOURCE_REVISION,
    )

    result = descriptor_builder.main(
        [
            "--output",
            str(tmp_path / "release.json"),
            "--source-revision",
            "0" * 40,
        ]
    )

    assert result == 2
    assert "does not equal clean Git HEAD" in capsys.readouterr().err
    assert not (tmp_path / "release.json").exists()


def test_history_key_errors_never_echo_the_candidate_secret() -> None:
    candidate = "not-secret-shaped"

    with pytest.raises(ReleaseIdentityError) as caught:
        history_key_id(candidate)

    assert candidate not in str(caught.value)


def test_environment_and_descriptor_reject_non_string_or_malformed_values(
    config_case: ConfigCase,
) -> None:
    with pytest.raises(ReleaseIdentityError, match="only string"):
        build_config_identity(
            {"FPA_PROVIDER": 1},  # type: ignore[dict-item]
            config_case.resolved,
            config_case.prompts_dir,
            config_case.answer_schema_path,
        )
    with pytest.raises(ReleaseIdentityError, match="valid UTF-8"):
        parse_release_descriptor(b"\xff")
    with pytest.raises(ReleaseIdentityError, match="bytes or text"):
        parse_release_descriptor(object())  # type: ignore[arg-type]


def test_descriptor_contains_no_wall_clock_or_deployment_realization_fields(
    config_case: ConfigCase,
) -> None:
    payload = _mutable_descriptor(_descriptor(config_case))

    assert set(payload).isdisjoint(
        {
            "created_at",
            "deployed_at",
            "lambda_version",
            "aws_region",
            "alias",
            "code_sha256",
            "zip_sha256",
        }
    )
    assert payload["source_state"] == "clean"
    assert os.linesep.encode() not in descriptor_bytes(_descriptor(config_case)).rstrip(b"\n")


def _verifiable_descriptor(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
    *,
    corpus_version: str | None = None,
) -> ReleaseDescriptor:
    snapshot = resolve_current_snapshot(
        chunks_path=snapshot_case.chunks_path,
        manifest_path=snapshot_case.manifest_path,
        raw_dir=snapshot_case.raw_dir,
        snapshots_dir=snapshot_case.snapshots_dir,
    )
    chunks = [snapshot_case.chunk]
    return build_release_descriptor(
        _SOURCE_REVISION,
        _config(config_case),
        content_version=release_identity.content_version(chunks),
        snapshot_version=snapshot.snapshot_version,
        corpus_version=corpus_version or release_identity.corpus_version(chunks),
    )


def _complete_identity_environment(
    config_case: ConfigCase,
    descriptor: ReleaseDescriptor,
) -> dict[str, str]:
    return {
        **config_case.environment,
        "FPA_SOURCE_REVISION": descriptor.source_revision,
        "FPA_CONFIG_VERSION": descriptor.config_version,
        "FPA_PINNED_CONTENT_VERSION": descriptor.content_version,
        "FPA_PINNED_SNAPSHOT_VERSION": descriptor.snapshot_version,
        "FPA_RELEASE_VERSION": descriptor.release_version,
        "FPA_PINNED_CORPUS_VERSION": descriptor.corpus_version,
    }


def test_verifier_accepts_exact_bundle_with_absent_or_complete_deployment_tuple(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
) -> None:
    descriptor = _verifiable_descriptor(config_case, snapshot_case)

    assert (
        verify_release_descriptor(
            descriptor,
            environment=config_case.environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )
        == descriptor
    )
    complete = {
        **_complete_identity_environment(config_case, descriptor),
        "AWS_LAMBDA_FUNCTION_VERSION": "11",
    }
    assert (
        verify_release_descriptor(
            descriptor,
            environment=complete,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
            require_environment=True,
        )
        == descriptor
    )


def test_verifier_rejects_partial_or_required_missing_deployment_tuple(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
) -> None:
    descriptor = _verifiable_descriptor(config_case, snapshot_case)
    common = {
        "resolved_config": config_case.resolved,
        "prompts_dir": config_case.prompts_dir,
        "answer_schema_path": config_case.answer_schema_path,
        "chunks_path": snapshot_case.chunks_path,
    }
    partial = {
        **config_case.environment,
        "FPA_SOURCE_REVISION": descriptor.source_revision,
    }
    with pytest.raises(ReleaseIdentityError, match="environment is partial; missing"):
        verify_release_descriptor(descriptor, environment=partial, **common)

    with pytest.raises(ReleaseIdentityError, match="complete release identity environment"):
        verify_release_descriptor(
            descriptor,
            environment=config_case.environment,
            require_environment=True,
            **common,
        )

    numeric_lambda = {
        **config_case.environment,
        "AWS_LAMBDA_FUNCTION_VERSION": "11",
    }
    with pytest.raises(ReleaseIdentityError, match="complete release identity environment"):
        verify_release_descriptor(descriptor, environment=numeric_lambda, **common)


@pytest.mark.parametrize(
    ("environment_name", "field"),
    [
        ("FPA_SOURCE_REVISION", "source_revision"),
        ("FPA_CONFIG_VERSION", "config_version"),
        ("FPA_PINNED_CONTENT_VERSION", "content_version"),
        ("FPA_PINNED_SNAPSHOT_VERSION", "snapshot_version"),
        ("FPA_RELEASE_VERSION", "release_version"),
        ("FPA_PINNED_CORPUS_VERSION", "corpus_version"),
    ],
)
def test_verifier_rejects_each_mismatched_deployment_identity_field(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
    environment_name: str,
    field: str,
) -> None:
    descriptor = _verifiable_descriptor(config_case, snapshot_case)
    environment = _complete_identity_environment(config_case, descriptor)
    environment[environment_name] = "0" * len(environment[environment_name])

    with pytest.raises(
        ReleaseIdentityError,
        match=re.escape(f"environment does not match descriptor: {field}"),
    ):
        verify_release_descriptor(
            descriptor,
            environment=environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )


def test_verifier_rejects_runtime_config_and_chunk_identity_drift(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
) -> None:
    descriptor = _verifiable_descriptor(config_case, snapshot_case)
    changed_environment = {**config_case.environment, "FPA_STALENESS_BUDGET_DAYS": "91"}
    with pytest.raises(ReleaseIdentityError, match="runtime configuration does not match"):
        verify_release_descriptor(
            descriptor,
            environment=changed_environment,
            resolved_config=None,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )

    changed_chunk = dataclasses.replace(snapshot_case.chunk, text="Changed bundled policy.")
    _write_chunks(snapshot_case.chunks_path, [changed_chunk])
    with pytest.raises(ReleaseIdentityError, match="content identity does not match"):
        verify_release_descriptor(
            descriptor,
            environment=config_case.environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )


def test_verifier_rejects_malformed_chunks_and_legacy_corpus_drift(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
) -> None:
    descriptor = _verifiable_descriptor(config_case, snapshot_case)
    snapshot_case.chunks_path.write_text("{\n", encoding="utf-8")
    with pytest.raises(ReleaseIdentityError, match="chunks are malformed or unreadable"):
        verify_release_descriptor(
            descriptor,
            environment=config_case.environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )

    _write_chunks(snapshot_case.chunks_path, [snapshot_case.chunk])
    wrong_corpus = _verifiable_descriptor(
        config_case,
        snapshot_case,
        corpus_version="0" * 12,
    )
    with pytest.raises(ReleaseIdentityError, match="compatibility corpus identity"):
        verify_release_descriptor(
            wrong_corpus,
            environment=config_case.environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )


def test_verifier_rejects_non_descriptor_and_recomputed_release_mismatch(
    config_case: ConfigCase,
    snapshot_case: SnapshotCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ReleaseIdentityError, match="descriptor must be a ReleaseDescriptor"):
        verify_release_descriptor(object())  # type: ignore[arg-type]

    descriptor = _verifiable_descriptor(config_case, snapshot_case)
    substituted = _descriptor(config_case).release
    monkeypatch.setattr(
        release_identity,
        "build_release_identity",
        lambda *_args, **_kwargs: substituted,
    )
    with pytest.raises(ReleaseIdentityError, match="recomputed release identity"):
        verify_release_descriptor(
            descriptor,
            environment=config_case.environment,
            resolved_config=config_case.resolved,
            prompts_dir=config_case.prompts_dir,
            answer_schema_path=config_case.answer_schema_path,
            chunks_path=snapshot_case.chunks_path,
        )
