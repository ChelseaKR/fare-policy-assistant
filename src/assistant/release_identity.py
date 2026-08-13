"""Deterministic application configuration and release identities.

``content_version`` and ``snapshot_version`` name the retained web-policy
evidence.  This module composes that evidence with every reviewed application
setting that can affect an answer:

* ``config_version`` covers resolved models, retrieval, exact prompt and answer
  contract bytes, domain behavior, containment, freshness, presentation,
  history signing, runtime limits, and judge-call settings.
* ``release_version`` covers a full clean source revision, ``config_version``,
  and the scoped evidence identities.

The bundled descriptor is deliberately reproducible.  It contains no timestamp,
Lambda version, deployment region, alias, or ZIP digest.  Runtime/deployment
realization metadata is checked separately and cannot be folded into the ZIP
without creating a recursive digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

from assistant import config, domain
from assistant.corpus import corpus_version
from assistant.identity import SnapshotIdentity, content_version
from assistant.ingest import Chunk, load_chunks
from assistant.snapshots import (
    SnapshotArchiveError,
    collect_snapshot_material,
    load_snapshot_chunks,
    validate_snapshot_archive,
)

CONFIG_SCHEMA = "fare-assistant.config.v1"
RELEASE_SCHEMA = "fare-assistant.release.v1"
DESCRIPTOR_SCHEMA = "fare-assistant.release-descriptor.v1"
DOMAIN_PROFILE_SCHEMA = "fare-assistant.domain-profile.v1"
# This literal is already used by the deployer.  Changing it rotates every key
# ID, and therefore requires a configuration-identity schema change.
HISTORY_KEY_ID_SCHEMA = "fare-assistant.history-key-id.v1"
WEB_POLICY_SCOPE = "web_policy"

PROMPT_NAMES = (
    "system",
    "answer_user",
    "judge_groundedness",
    "judge_helpfulness",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CORPUS_VERSION = re.compile(r"^[0-9a-f]{12}$")
_DOCUMENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DOMAIN_KEY = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_CSP_ORIGIN = re.compile(
    r"^https?://(?:\*\.)?"
    r"(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\])"
    r"(?::[0-9]{1,5})?$",
    re.IGNORECASE,
)

_CONFIG_FIELDS = frozenset(
    {
        "models",
        "retrieval",
        "prompts",
        "domain",
        "source_policy",
        "runtime",
        "answer_contract",
        "judge_calls",
    }
)
_DESCRIPTOR_FIELDS = frozenset(
    {
        "descriptor_schema",
        "config_schema",
        "release_schema",
        "source_state",
        "source_revision",
        "config_version",
        "release_version",
        "config",
        "evidence",
        "compatibility",
    }
)
_IDENTITY_ENVIRONMENT = {
    "source_revision": "FPA_SOURCE_REVISION",
    "config_version": "FPA_CONFIG_VERSION",
    "content_version": "FPA_PINNED_CONTENT_VERSION",
    "snapshot_version": "FPA_PINNED_SNAPSHOT_VERSION",
    "release_version": "FPA_RELEASE_VERSION",
    "corpus_version": "FPA_PINNED_CORPUS_VERSION",
}


class ReleaseIdentityError(ValueError):
    """A configuration, release, descriptor, or archive is not trustworthy."""


# More explicit spelling retained as an alias for callers that prefer it.
ReleaseIdentityValidationError = ReleaseIdentityError


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_json(payload: object) -> bytes:
    try:
        return json.dumps(
            _plain(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReleaseIdentityError("identity payload is not canonical-JSON compatible") from exc


def _canonical_digest(schema: str, payload: object) -> str:
    try:
        schema_bytes = schema.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ReleaseIdentityError("identity schema must be non-empty ASCII") from exc
    if not schema_bytes:
        raise ReleaseIdentityError("identity schema must be non-empty ASCII")
    return hashlib.sha256(schema_bytes + b"\0" + _canonical_json(payload)).hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReleaseIdentityError(f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ReleaseIdentityError(f"{context} object keys must be strings")
    return value


def _exact_fields(
    value: object,
    expected: set[str] | frozenset[str],
    context: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, context)
    actual = set(mapping)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise ReleaseIdentityError(f"{context} has an invalid field set ({'; '.join(details)})")
    return mapping


def _array(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ReleaseIdentityError(f"{context} must be an array")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseIdentityError(f"{context} must be a non-empty, trimmed string")
    return value


def _optional_string(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _string(value, context)


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReleaseIdentityError(f"{context} must be an integer >= {minimum}")
    return value


def _float(value: object, context: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ReleaseIdentityError(f"{context} must be a finite JSON float")
    return value


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise ReleaseIdentityError(f"{context} must be a boolean")
    return value


def _sha256(value: object, context: str) -> str:
    digest = _string(value, context)
    if not _SHA256.fullmatch(digest):
        raise ReleaseIdentityError(f"{context} must be a 64-character lowercase SHA-256")
    return digest


def _source_revision(value: object, context: str = "source_revision") -> str:
    revision = _string(value, context)
    if not _SOURCE_REVISION.fullmatch(revision):
        raise ReleaseIdentityError(f"{context} must be a full 40-character lowercase Git object ID")
    return revision


def _legacy_corpus_version(value: object, context: str = "corpus_version") -> str:
    version = _string(value, context)
    if not _CORPUS_VERSION.fullmatch(version):
        raise ReleaseIdentityError(
            f"{context} must be a 12-character lowercase compatibility digest"
        )
    return version


def _document_id(value: object, context: str) -> str:
    document = _string(value, context)
    if not _DOCUMENT_ID.fullmatch(document):
        raise ReleaseIdentityError(
            f"{context} must contain lowercase letters, digits, and single hyphens"
        )
    return document


def _file_bytes(path: Path, context: str, *, require_utf8: bool = True) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ReleaseIdentityError(f"{context} is missing or is not a regular file: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseIdentityError(f"{context} could not be read: {path}") from exc
    if not raw:
        raise ReleaseIdentityError(f"{context} must not be empty: {path}")
    if require_utf8:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseIdentityError(f"{context} must be valid UTF-8: {path}") from exc
    return raw


def _bytes_receipt(
    raw: bytes,
    context: str,
    *,
    require_utf8: bool = True,
) -> dict[str, object]:
    if not isinstance(raw, bytes):
        raise ReleaseIdentityError(f"{context} captured content must be bytes")
    if not raw:
        raise ReleaseIdentityError(f"{context} must not be empty")
    if require_utf8:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseIdentityError(f"{context} must be valid UTF-8") from exc
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _file_receipt(path: Path, context: str, *, require_utf8: bool = True) -> dict[str, object]:
    return _bytes_receipt(
        _file_bytes(path, context, require_utf8=require_utf8),
        context,
        require_utf8=False,
    )


def history_key_id(secret: str) -> str:
    """Return the deploy-compatible opaque ID for one strong signing secret.

    The secret contract is intentionally exact: 32 bytes represented as 64
    lowercase hexadecimal characters.  Error messages never interpolate it.
    """
    if not isinstance(secret, str) or not _SHA256.fullmatch(secret):
        raise ReleaseIdentityError(
            "history signing secret must be exactly 64 lowercase hexadecimal characters"
        )
    return hashlib.sha256(
        HISTORY_KEY_ID_SCHEMA.encode("ascii") + b"\0" + secret.encode("ascii")
    ).hexdigest()


def _history_signing(environment: Mapping[str, str]) -> dict[str, object]:
    secret = environment.get("FPA_HISTORY_HMAC_KEY", "")
    declared = environment.get("FPA_HISTORY_HMAC_KEY_ID", "")
    if secret == "":
        if declared != "":
            raise ReleaseIdentityError(
                "FPA_HISTORY_HMAC_KEY_ID cannot be declared when history signing is disabled"
            )
        return {"enabled": False, "key_id": None}
    derived = history_key_id(secret)
    if declared:
        _sha256(declared, "FPA_HISTORY_HMAC_KEY_ID")
        if not hmac.compare_digest(declared, derived):
            raise ReleaseIdentityError(
                "declared history signing key ID does not match the configured secret"
            )
    return {"enabled": True, "key_id": derived}


def _disabled_documents(environment: Mapping[str, str]) -> list[str]:
    raw = environment.get("FPA_DISABLED_DOC_IDS", "")
    if not isinstance(raw, str):
        raise ReleaseIdentityError("FPA_DISABLED_DOC_IDS must be a string")
    normalized = sorted({item.strip() for item in raw.split(",") if item.strip()})
    for index, document in enumerate(normalized):
        _document_id(document, f"disabled_document_ids[{index}]")
    return normalized


def _staleness_budget(environment: Mapping[str, str]) -> int:
    raw = environment.get(
        "FPA_STALENESS_BUDGET_DAYS",
        str(config.DEFAULT_STALENESS_BUDGET_DAYS),
    )
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
        raise ReleaseIdentityError("FPA_STALENESS_BUDGET_DAYS must be a positive integer")
    value = int(raw)
    if value < 1:
        raise ReleaseIdentityError("FPA_STALENESS_BUDGET_DAYS must be a positive integer")
    return value


def _embed_ancestors(environment: Mapping[str, str]) -> list[str]:
    raw = environment.get("FPA_EMBED_ANCESTORS", config.DEFAULT_EMBED_ANCESTORS)
    if not isinstance(raw, str):
        raise ReleaseIdentityError("FPA_EMBED_ANCESTORS must be a string")
    values = sorted(set(raw.split()))
    if not values:
        raise ReleaseIdentityError("FPA_EMBED_ANCESTORS must not be empty")
    if "'none'" in values and len(values) != 1:
        raise ReleaseIdentityError("'none' cannot be combined with other embed ancestors")
    for value in values:
        if value in {"'self'", "'none'", "*", "http:", "https:"}:
            continue
        if not _CSP_ORIGIN.fullmatch(value):
            raise ReleaseIdentityError("FPA_EMBED_ANCESTORS contains an invalid CSP source")
        if value.rsplit(":", 1)[-1].isdigit():
            port = int(value.rsplit(":", 1)[-1])
            if port > 65535:
                raise ReleaseIdentityError("FPA_EMBED_ANCESTORS contains an invalid port")
    return values


def _validate_embed_ancestors(values: Sequence[str], context: str) -> None:
    if "'none'" in values and len(values) != 1:
        raise ReleaseIdentityError(f"{context} cannot combine 'none' with other sources")
    for value in values:
        if value in {"'self'", "'none'", "*", "http:", "https:"}:
            continue
        if not _CSP_ORIGIN.fullmatch(value):
            raise ReleaseIdentityError(f"{context} contains an invalid CSP source")
        if value.rsplit(":", 1)[-1].isdigit() and int(value.rsplit(":", 1)[-1]) > 65535:
            raise ReleaseIdentityError(f"{context} contains an invalid port")


def _domain_payload(environment: Mapping[str, str]) -> dict[str, object]:
    requested = environment.get("FPA_DOMAIN", "transit")
    if not isinstance(requested, str) or requested != requested.strip():
        raise ReleaseIdentityError("FPA_DOMAIN must be a trimmed string")
    key = requested.lower()
    if not _DOMAIN_KEY.fullmatch(key) or key not in domain._REGISTRY:
        raise ReleaseIdentityError(f"unknown domain profile key: {key!r}")
    profile = domain._REGISTRY[key]
    behavior: dict[str, object] = {
        "key": key,
        "name": profile.name,
        "scopes": list(profile.scopes),
        "aliases": dict(profile.aliases),
        "fallback_contact": profile.fallback_contact,
        "scope_topics": {
            topic: {"pattern": pattern.pattern, "flags": pattern.flags}
            for topic, pattern in profile.scope_topics.items()
        },
    }
    return {
        **behavior,
        "profile_version": _canonical_digest(DOMAIN_PROFILE_SCHEMA, behavior),
    }


def _prompt_payload(
    prompts_dir: Path,
    captured_prompt_bytes: Mapping[str, bytes] | None = None,
) -> dict[str, object]:
    if captured_prompt_bytes is not None:
        if not isinstance(captured_prompt_bytes, Mapping):
            raise ReleaseIdentityError("captured_prompt_bytes must be a mapping")
        if set(captured_prompt_bytes) != set(PROMPT_NAMES):
            raise ReleaseIdentityError(
                "captured_prompt_bytes must contain exactly " + ", ".join(PROMPT_NAMES)
            )
        return {
            name: _bytes_receipt(captured_prompt_bytes[name], f"{name} prompt")
            for name in PROMPT_NAMES
        }
    return {
        name: _file_receipt(prompts_dir / f"{name}.txt", f"{name} prompt") for name in PROMPT_NAMES
    }


def _provider_transport(provider: str, environment: Mapping[str, str]) -> dict[str, object]:
    try:
        resolved = config.resolve_provider_transport(provider, environment)
    except (TypeError, ValueError) as exc:
        raise ReleaseIdentityError(str(exc)) from exc
    endpoint_sha256 = (
        hashlib.sha256(resolved.base_url.encode("utf-8")).hexdigest()
        if resolved.base_url is not None
        else None
    )
    return {
        "aws_region": resolved.aws_region,
        "endpoint_sha256": endpoint_sha256,
    }


def _runtime_payload(environment: Mapping[str, str]) -> dict[str, object]:
    return {
        "staleness_budget_days": _staleness_budget(environment),
        "embed_ancestors": _embed_ancestors(environment),
        "history_signing": _history_signing(environment),
        "limits": {
            "max_question_chars": config.MAX_QUESTION_CHARS,
            "max_body_bytes": config.MAX_BODY_BYTES,
            "requests_per_minute": config.REQUESTS_PER_MINUTE,
            "feedback_per_minute": config.FEEDBACK_PER_MINUTE,
            "answer_cache_size": config.ANSWER_CACHE_SIZE,
            "max_history_turns": config.MAX_HISTORY_TURNS,
            "max_history_answer_chars": config.MAX_HISTORY_ANSWER_CHARS,
        },
        "answer_cache_key_schema": config.ANSWER_CACHE_KEY_SCHEMA,
    }


def _resolved_config_payload(
    resolved: config.Config,
    environment: Mapping[str, str],
    *,
    prompts_dir: Path,
    answer_schema_path: Path,
    captured_prompt_bytes: Mapping[str, bytes] | None = None,
    captured_answer_schema_bytes: bytes | None = None,
) -> dict[str, object]:
    models = resolved.models
    retrieval = resolved.retrieval
    answer_contract_raw = (
        _file_bytes(answer_schema_path, "answer contract")
        if captured_answer_schema_bytes is None
        else captured_answer_schema_bytes
    )
    contract = _bytes_receipt(answer_contract_raw, "answer contract")
    try:
        json.loads(answer_contract_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIdentityError("answer contract must contain valid JSON") from exc
    return {
        "models": {
            "provider": models.provider,
            "transport": _provider_transport(models.provider, environment),
            "answer": {
                "model_id": models.answer_model,
                "max_tokens": models.max_tokens,
                "temperature": models.temperature,
            },
            "judge_model_id": models.judge_model,
        },
        "retrieval": {
            "top_k": retrieval.top_k,
            "language_boost": retrieval.language_boost,
            "decline_z_threshold": retrieval.decline_z_threshold,
            "decline_coverage_floor": retrieval.decline_coverage_floor,
            "confidence_high_z": retrieval.confidence_high_z,
            "use_dense": retrieval.use_dense,
            "dense_model": retrieval.dense_model,
            "dense_weight": retrieval.dense_weight,
        },
        "prompts": _prompt_payload(prompts_dir, captured_prompt_bytes),
        "domain": _domain_payload(environment),
        "source_policy": {
            "disabled_document_ids": _disabled_documents(environment),
        },
        "runtime": _runtime_payload(environment),
        "answer_contract": contract,
        "judge_calls": {
            "groundedness": {
                "max_tokens": config.JUDGE_MAX_TOKENS,
                "temperature": config.JUDGE_TEMPERATURE,
            },
            "helpfulness": {
                "max_tokens": config.JUDGE_MAX_TOKENS,
                "temperature": config.JUDGE_TEMPERATURE,
            },
        },
    }


def _validate_receipt(value: object, context: str) -> None:
    receipt = _exact_fields(value, {"sha256", "bytes"}, context)
    _sha256(receipt["sha256"], f"{context}.sha256")
    _integer(receipt["bytes"], f"{context}.bytes", minimum=1)


def _validate_config_payload(value: object) -> Mapping[str, object]:
    payload = _exact_fields(value, _CONFIG_FIELDS, "config")

    models = _exact_fields(
        payload["models"],
        {"provider", "transport", "answer", "judge_model_id"},
        "models",
    )
    provider = _string(models["provider"], "models.provider")
    if provider not in config._DEFAULT_MODELS:
        raise ReleaseIdentityError(f"models.provider is unsupported: {provider!r}")
    transport = _exact_fields(
        models["transport"],
        {"aws_region", "endpoint_sha256"},
        "models.transport",
    )
    aws_region = _optional_string(transport["aws_region"], "models.transport.aws_region")
    endpoint = transport["endpoint_sha256"]
    if endpoint is not None:
        _sha256(endpoint, "models.transport.endpoint_sha256")
    if provider == "bedrock":
        if not config.is_canonical_aws_region(aws_region) or endpoint is None:
            raise ReleaseIdentityError(
                "bedrock transport must contain a valid AWS region and endpoint digest"
            )
    elif provider in {"anthropic", "local"}:
        if aws_region is not None or endpoint is None:
            raise ReleaseIdentityError(f"{provider} transport must contain only an endpoint digest")
    elif aws_region is not None or endpoint is not None:
        raise ReleaseIdentityError(f"{provider} transport fields must be null")
    _string(models["judge_model_id"], "models.judge_model_id")
    answer = _exact_fields(
        models["answer"],
        {"model_id", "max_tokens", "temperature"},
        "models.answer",
    )
    _string(answer["model_id"], "models.answer.model_id")
    _integer(answer["max_tokens"], "models.answer.max_tokens", minimum=1)
    _float(answer["temperature"], "models.answer.temperature")

    retrieval = _exact_fields(
        payload["retrieval"],
        {
            "top_k",
            "language_boost",
            "decline_z_threshold",
            "decline_coverage_floor",
            "confidence_high_z",
            "use_dense",
            "dense_model",
            "dense_weight",
        },
        "retrieval",
    )
    _integer(retrieval["top_k"], "retrieval.top_k", minimum=1)
    for name in (
        "language_boost",
        "decline_z_threshold",
        "decline_coverage_floor",
        "confidence_high_z",
        "dense_weight",
    ):
        _float(retrieval[name], f"retrieval.{name}")
    _boolean(retrieval["use_dense"], "retrieval.use_dense")
    _string(retrieval["dense_model"], "retrieval.dense_model")

    prompts = _exact_fields(payload["prompts"], set(PROMPT_NAMES), "prompts")
    for name in PROMPT_NAMES:
        _validate_receipt(prompts[name], f"prompts.{name}")

    domain_payload = _exact_fields(
        payload["domain"],
        {
            "key",
            "name",
            "scopes",
            "aliases",
            "fallback_contact",
            "scope_topics",
            "profile_version",
        },
        "domain",
    )
    key = _string(domain_payload["key"], "domain.key")
    if not _DOMAIN_KEY.fullmatch(key):
        raise ReleaseIdentityError("domain.key has an invalid format")
    _string(domain_payload["name"], "domain.name")
    scopes = _array(domain_payload["scopes"], "domain.scopes")
    if not scopes:
        raise ReleaseIdentityError("domain.scopes must not be empty")
    normalized_scopes = [
        _string(item, f"domain.scopes[{index}]") for index, item in enumerate(scopes)
    ]
    if len(set(normalized_scopes)) != len(normalized_scopes):
        raise ReleaseIdentityError("domain.scopes must not contain duplicates")
    aliases = _mapping(domain_payload["aliases"], "domain.aliases")
    if not aliases:
        raise ReleaseIdentityError("domain.aliases must not be empty")
    for alias, scope in aliases.items():
        _string(alias, "domain.aliases key")
        selected_scope = _string(scope, f"domain.aliases.{alias}")
        if selected_scope not in normalized_scopes:
            raise ReleaseIdentityError(f"domain.aliases.{alias} names an unknown scope")
    _string(domain_payload["fallback_contact"], "domain.fallback_contact")
    topics = _mapping(domain_payload["scope_topics"], "domain.scope_topics")
    for topic, rule_value in topics.items():
        _string(topic, "domain.scope_topics key")
        rule = _exact_fields(rule_value, {"pattern", "flags"}, f"domain.scope_topics.{topic}")
        pattern = _string(rule["pattern"], f"domain.scope_topics.{topic}.pattern")
        flags = _integer(rule["flags"], f"domain.scope_topics.{topic}.flags")
        try:
            re.compile(pattern, flags)
        except re.error as exc:
            raise ReleaseIdentityError(
                f"domain.scope_topics.{topic}.pattern is not a valid regular expression"
            ) from exc
    behavior = {
        name: _plain(domain_payload[name])
        for name in ("key", "name", "scopes", "aliases", "fallback_contact", "scope_topics")
    }
    expected_profile = _canonical_digest(DOMAIN_PROFILE_SCHEMA, behavior)
    if not hmac.compare_digest(
        _sha256(domain_payload["profile_version"], "domain.profile_version"),
        expected_profile,
    ):
        raise ReleaseIdentityError("domain.profile_version does not match domain behavior")

    source_policy = _exact_fields(
        payload["source_policy"],
        {"disabled_document_ids"},
        "source_policy",
    )
    disabled = _array(
        source_policy["disabled_document_ids"],
        "source_policy.disabled_document_ids",
    )
    normalized_disabled = [
        _document_id(item, f"source_policy.disabled_document_ids[{index}]")
        for index, item in enumerate(disabled)
    ]
    if normalized_disabled != sorted(set(normalized_disabled)):
        raise ReleaseIdentityError("source_policy.disabled_document_ids must be sorted and unique")

    runtime = _exact_fields(
        payload["runtime"],
        {
            "staleness_budget_days",
            "embed_ancestors",
            "history_signing",
            "limits",
            "answer_cache_key_schema",
        },
        "runtime",
    )
    _integer(runtime["staleness_budget_days"], "runtime.staleness_budget_days", minimum=1)
    ancestors = _array(runtime["embed_ancestors"], "runtime.embed_ancestors")
    if not ancestors:
        raise ReleaseIdentityError("runtime.embed_ancestors must not be empty")
    ancestor_values = [
        _string(item, f"runtime.embed_ancestors[{index}]") for index, item in enumerate(ancestors)
    ]
    if ancestor_values != sorted(set(ancestor_values)):
        raise ReleaseIdentityError("runtime.embed_ancestors must be sorted and unique")
    _validate_embed_ancestors(ancestor_values, "runtime.embed_ancestors")
    signing = _exact_fields(
        runtime["history_signing"],
        {"enabled", "key_id"},
        "runtime.history_signing",
    )
    enabled = _boolean(signing["enabled"], "runtime.history_signing.enabled")
    key_id = signing["key_id"]
    if enabled:
        _sha256(key_id, "runtime.history_signing.key_id")
    elif key_id is not None:
        raise ReleaseIdentityError(
            "runtime.history_signing.key_id must be null when signing is disabled"
        )
    limits = _exact_fields(
        runtime["limits"],
        {
            "max_question_chars",
            "max_body_bytes",
            "requests_per_minute",
            "feedback_per_minute",
            "answer_cache_size",
            "max_history_turns",
            "max_history_answer_chars",
        },
        "runtime.limits",
    )
    for name in limits:
        _integer(limits[name], f"runtime.limits.{name}", minimum=1)
    _string(runtime["answer_cache_key_schema"], "runtime.answer_cache_key_schema")

    _validate_receipt(payload["answer_contract"], "answer_contract")
    judge_calls = _exact_fields(
        payload["judge_calls"],
        {"groundedness", "helpfulness"},
        "judge_calls",
    )
    for name in ("groundedness", "helpfulness"):
        call = _exact_fields(
            judge_calls[name],
            {"max_tokens", "temperature"},
            f"judge_calls.{name}",
        )
        _integer(call["max_tokens"], f"judge_calls.{name}.max_tokens", minimum=1)
        _float(call["temperature"], f"judge_calls.{name}.temperature")

    _canonical_json(payload)
    return payload


@dataclass(frozen=True)
class ConfigIdentity:
    """Immutable public configuration payload and its full digest."""

    config_version: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        supplied = _sha256(self.config_version, "config_version")
        validated = _validate_config_payload(self.payload)
        expected = _canonical_digest(CONFIG_SCHEMA, validated)
        if not hmac.compare_digest(supplied, expected):
            raise ReleaseIdentityError("config_version does not match the public configuration")
        object.__setattr__(self, "payload", _freeze(validated))

    def to_json_dict(self) -> dict[str, object]:
        return _plain(self.payload)  # type: ignore[return-value]


@dataclass(frozen=True)
class WebPolicyEvidence:
    """The first release schema's one supported evidence scope."""

    content_version: str
    snapshot_version: str
    scope: str = WEB_POLICY_SCOPE

    def __post_init__(self) -> None:
        if self.scope != WEB_POLICY_SCOPE:
            raise ReleaseIdentityError(
                f"{RELEASE_SCHEMA} supports only {WEB_POLICY_SCOPE!r} evidence"
            )
        _sha256(self.content_version, "evidence.content_version")
        _sha256(self.snapshot_version, "evidence.snapshot_version")

    def to_json_dict(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "content_version": self.content_version,
            "snapshot_version": self.snapshot_version,
        }


EvidenceIdentity = WebPolicyEvidence


def _release_payload(
    source_revision: str,
    config_version: str,
    evidence: Sequence[WebPolicyEvidence],
) -> dict[str, object]:
    return {
        "source_revision": source_revision,
        "config_version": config_version,
        "evidence": [
            item.to_json_dict() for item in sorted(evidence, key=lambda candidate: candidate.scope)
        ],
    }


@dataclass(frozen=True)
class ReleaseIdentity:
    """A validated logical release, independent of deployment realization."""

    release_version: str
    source_revision: str
    config_version: str
    evidence: tuple[WebPolicyEvidence, ...]

    def __post_init__(self) -> None:
        supplied = _sha256(self.release_version, "release_version")
        revision = _source_revision(self.source_revision)
        config_digest = _sha256(self.config_version, "config_version")
        if type(self.evidence) is not tuple or len(self.evidence) != 1:
            raise ReleaseIdentityError(
                f"{RELEASE_SCHEMA} requires exactly one web_policy evidence entry"
            )
        if not all(isinstance(item, WebPolicyEvidence) for item in self.evidence):
            raise ReleaseIdentityError("release evidence contains an invalid value object")
        scopes = [item.scope for item in self.evidence]
        if scopes != sorted(set(scopes)):
            raise ReleaseIdentityError("release evidence must be sorted with unique scopes")
        expected = _canonical_digest(
            RELEASE_SCHEMA,
            _release_payload(revision, config_digest, self.evidence),
        )
        if not hmac.compare_digest(supplied, expected):
            raise ReleaseIdentityError("release_version does not match the release tuple")

    @property
    def web_policy(self) -> WebPolicyEvidence:
        return self.evidence[0]

    def to_json_dict(self) -> dict[str, object]:
        return _release_payload(self.source_revision, self.config_version, self.evidence)


@dataclass(frozen=True)
class ReleaseDescriptor:
    """The deterministic, strictly validated bundle descriptor."""

    config: ConfigIdentity
    release: ReleaseIdentity
    corpus_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.config, ConfigIdentity):
            raise ReleaseIdentityError("descriptor config must be a ConfigIdentity")
        if not isinstance(self.release, ReleaseIdentity):
            raise ReleaseIdentityError("descriptor release must be a ReleaseIdentity")
        if self.config.config_version != self.release.config_version:
            raise ReleaseIdentityError("descriptor config and release identities disagree")
        _legacy_corpus_version(self.corpus_version)

    @property
    def source_revision(self) -> str:
        return self.release.source_revision

    @property
    def config_version(self) -> str:
        return self.config.config_version

    @property
    def content_version(self) -> str:
        return self.release.web_policy.content_version

    @property
    def snapshot_version(self) -> str:
        return self.release.web_policy.snapshot_version

    @property
    def release_version(self) -> str:
        return self.release.release_version

    def to_json_dict(self) -> dict[str, object]:
        return {
            "descriptor_schema": DESCRIPTOR_SCHEMA,
            "config_schema": CONFIG_SCHEMA,
            "release_schema": RELEASE_SCHEMA,
            "source_state": "clean",
            "source_revision": self.source_revision,
            "config_version": self.config_version,
            "release_version": self.release_version,
            "config": self.config.to_json_dict(),
            "evidence": [item.to_json_dict() for item in self.release.evidence],
            "compatibility": {"corpus_version": self.corpus_version},
        }


def build_config_identity(
    environment: Mapping[str, str] | None = None,
    resolved_config: config.Config | None = None,
    prompts_dir: Path | None = None,
    answer_schema_path: Path | None = None,
    *,
    captured_prompt_bytes: Mapping[str, bytes] | None = None,
    captured_answer_schema_bytes: bytes | None = None,
) -> ConfigIdentity:
    """Resolve and hash the complete public application configuration."""
    values = os.environ if environment is None else environment
    if not isinstance(values, Mapping):
        raise ReleaseIdentityError("environment must be a string mapping")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise ReleaseIdentityError("environment must contain only string keys and values")
    try:
        resolved = resolved_config or config.Config.from_environment(values)
    except (TypeError, ValueError) as exc:
        raise ReleaseIdentityError("model or retrieval environment is invalid") from exc
    if not isinstance(resolved, config.Config):
        raise ReleaseIdentityError("resolved_config must be an assistant.config.Config")
    payload = _resolved_config_payload(
        resolved,
        values,
        prompts_dir=prompts_dir or config.PROMPTS_DIR,
        answer_schema_path=answer_schema_path or config.ANSWER_SCHEMA_PATH,
        captured_prompt_bytes=captured_prompt_bytes,
        captured_answer_schema_bytes=captured_answer_schema_bytes,
    )
    version = _canonical_digest(CONFIG_SCHEMA, payload)
    return ConfigIdentity(version, payload)


def build_release_identity(
    source_revision: str,
    config_version: str,
    *,
    content_version: str,
    snapshot_version: str,
) -> ReleaseIdentity:
    """Build the first release schema's web-policy release identity."""
    revision = _source_revision(source_revision)
    config_digest = _sha256(config_version, "config_version")
    evidence = (WebPolicyEvidence(content_version, snapshot_version),)
    version = _canonical_digest(
        RELEASE_SCHEMA,
        _release_payload(revision, config_digest, evidence),
    )
    return ReleaseIdentity(version, revision, config_digest, evidence)


def build_release_descriptor(
    source_revision: str,
    config_identity: ConfigIdentity,
    *,
    content_version: str,
    snapshot_version: str,
    corpus_version: str,
) -> ReleaseDescriptor:
    """Compose one deterministic descriptor from validated constituent IDs."""
    if not isinstance(config_identity, ConfigIdentity):
        raise ReleaseIdentityError("config_identity must be a ConfigIdentity")
    release = build_release_identity(
        source_revision,
        config_identity.config_version,
        content_version=content_version,
        snapshot_version=snapshot_version,
    )
    return ReleaseDescriptor(config_identity, release, corpus_version)


def descriptor_bytes(descriptor: ReleaseDescriptor) -> bytes:
    """Serialize a descriptor canonically with one terminal newline."""
    if not isinstance(descriptor, ReleaseDescriptor):
        raise ReleaseIdentityError("descriptor must be a ReleaseDescriptor")
    return _canonical_json(descriptor.to_json_dict()) + b"\n"


def _reject_constant(value: str) -> None:
    raise ReleaseIdentityError(f"descriptor contains invalid JSON number literal {value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseIdentityError(f"descriptor contains duplicate object key: {key}")
        result[key] = value
    return result


def parse_release_descriptor(data: bytes | str) -> ReleaseDescriptor:
    """Strictly parse, structurally validate, and re-hash a descriptor."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseIdentityError("descriptor must be valid UTF-8") from exc
    elif isinstance(data, str):
        text = data
    else:
        raise ReleaseIdentityError("descriptor input must be bytes or text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ReleaseIdentityError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ReleaseIdentityError("descriptor must contain valid JSON") from exc

    raw = _exact_fields(value, _DESCRIPTOR_FIELDS, "descriptor")
    if raw["descriptor_schema"] != DESCRIPTOR_SCHEMA:
        raise ReleaseIdentityError("descriptor has an unsupported descriptor_schema")
    if raw["config_schema"] != CONFIG_SCHEMA:
        raise ReleaseIdentityError("descriptor has an unsupported config_schema")
    if raw["release_schema"] != RELEASE_SCHEMA:
        raise ReleaseIdentityError("descriptor has an unsupported release_schema")
    if raw["source_state"] != "clean":
        raise ReleaseIdentityError("descriptor source_state must be 'clean'")

    embedded_config = _validate_config_payload(raw["config"])
    config_identity = ConfigIdentity(
        _sha256(raw["config_version"], "descriptor.config_version"),
        embedded_config,
    )

    evidence_rows = _array(raw["evidence"], "descriptor.evidence")
    if len(evidence_rows) != 1:
        raise ReleaseIdentityError(
            f"{RELEASE_SCHEMA} requires exactly one web_policy evidence entry"
        )
    evidence_row = _exact_fields(
        evidence_rows[0],
        {"scope", "content_version", "snapshot_version"},
        "descriptor.evidence[0]",
    )
    evidence = WebPolicyEvidence(
        content_version=_sha256(
            evidence_row["content_version"],
            "descriptor.evidence[0].content_version",
        ),
        snapshot_version=_sha256(
            evidence_row["snapshot_version"],
            "descriptor.evidence[0].snapshot_version",
        ),
        scope=_string(evidence_row["scope"], "descriptor.evidence[0].scope"),
    )
    source = _source_revision(raw["source_revision"], "descriptor.source_revision")
    release = ReleaseIdentity(
        release_version=_sha256(raw["release_version"], "descriptor.release_version"),
        source_revision=source,
        config_version=config_identity.config_version,
        evidence=(evidence,),
    )
    compatibility = _exact_fields(
        raw["compatibility"],
        {"corpus_version"},
        "descriptor.compatibility",
    )
    return ReleaseDescriptor(
        config=config_identity,
        release=release,
        corpus_version=_legacy_corpus_version(
            compatibility["corpus_version"],
            "descriptor.compatibility.corpus_version",
        ),
    )


def load_release_descriptor(path: Path | None = None) -> ReleaseDescriptor:
    """Read and strictly validate a descriptor from disk."""
    selected = path or config.RELEASE_DESCRIPTOR_PATH
    if selected.is_symlink() or not selected.is_file():
        raise ReleaseIdentityError(
            f"release descriptor is missing or is not a regular file: {selected}"
        )
    try:
        data = selected.read_bytes()
    except OSError as exc:
        raise ReleaseIdentityError(f"release descriptor could not be read: {selected}") from exc
    return parse_release_descriptor(data)


def write_release_descriptor(
    descriptor: ReleaseDescriptor,
    path: Path | None = None,
) -> Path:
    """Atomically write canonical descriptor bytes without adding wall-clock data."""
    selected = path or config.RELEASE_DESCRIPTOR_PATH
    if selected.exists() and selected.is_symlink():
        raise ReleaseIdentityError(f"refusing to replace a descriptor symlink: {selected}")
    selected.parent.mkdir(parents=True, exist_ok=True)
    data = descriptor_bytes(descriptor)
    descriptor_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{selected.name}.",
        dir=selected.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor_fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, selected)
        directory_fd = os.open(selected.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ReleaseIdentityError(f"release descriptor could not be written: {selected}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return selected


def resolve_current_snapshot(
    *,
    chunks_path: Path | None = None,
    manifest_path: Path | None = None,
    raw_dir: Path | None = None,
    snapshots_dir: Path | None = None,
    chunks: Sequence[Chunk] | None = None,
    manifest: Mapping[str, object] | None = None,
) -> SnapshotIdentity:
    """Resolve only the exact schema-2 archive for the current bundled inputs."""
    selected_chunks = chunks_path or config.CHUNKS_PATH
    selected_manifest = manifest_path or config.MANIFEST_PATH
    selected_raw = raw_dir or config.RAW_DIR
    selected_snapshots = snapshots_dir or config.SNAPSHOTS_DIR
    if manifest is None and (selected_manifest.is_symlink() or not selected_manifest.is_file()):
        raise ReleaseIdentityError(
            f"manifest is missing or is not a regular file: {selected_manifest}"
        )
    try:
        selected_chunk_rows = load_chunks(selected_chunks) if chunks is None else list(chunks)
        selected_manifest_data = (
            yaml.safe_load(selected_manifest.read_bytes()) if manifest is None else manifest
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        KeyError,
        yaml.YAMLError,
    ) as exc:
        raise ReleaseIdentityError("current chunks or manifest are malformed") from exc
    if not isinstance(selected_manifest_data, Mapping):
        raise ReleaseIdentityError("current manifest must be an object")
    try:
        material = collect_snapshot_material(
            selected_chunk_rows,
            selected_manifest_data,
            selected_raw,
        )
        archive = selected_snapshots / material.identity.snapshot_version
        archived_identity = validate_snapshot_archive(archive)
        archived_chunks = load_snapshot_chunks(
            material.identity.snapshot_version,
            selected_snapshots,
        )
    except (OSError, SnapshotArchiveError, FileNotFoundError) as exc:
        raise ReleaseIdentityError(
            "exact current source snapshot is not archived and valid"
        ) from exc
    if archived_identity != material.identity:
        raise ReleaseIdentityError(
            "archived snapshot identity does not match current source material"
        )
    if archived_chunks != selected_chunk_rows:
        raise ReleaseIdentityError("archived snapshot chunks do not equal current bundled chunks")
    return material.identity


def verify_release_descriptor(
    descriptor: ReleaseDescriptor,
    *,
    environment: Mapping[str, str] | None = None,
    resolved_config: config.Config | None = None,
    prompts_dir: Path | None = None,
    answer_schema_path: Path | None = None,
    chunks_path: Path | None = None,
    require_environment: bool = False,
    config_identity: ConfigIdentity | None = None,
    chunks: Sequence[Chunk] | None = None,
) -> ReleaseDescriptor:
    """Recompute all runtime-verifiable inputs and compare the release tuple.

    ``snapshot_version`` is pre-bundle evidence because raw source bytes are not
    shipped to the rider Lambda.  Its descriptor value is still bound into and
    re-hashed as part of ``release_version``.
    """
    if not isinstance(descriptor, ReleaseDescriptor):
        raise ReleaseIdentityError("descriptor must be a ReleaseDescriptor")
    values = os.environ if environment is None else environment
    if config_identity is not None and not isinstance(config_identity, ConfigIdentity):
        raise ReleaseIdentityError("config_identity must be a ConfigIdentity")
    recomputed_config = config_identity or build_config_identity(
        values,
        resolved_config,
        prompts_dir,
        answer_schema_path,
    )
    if recomputed_config != descriptor.config:
        raise ReleaseIdentityError("bundled/runtime configuration does not match descriptor")
    selected_chunks = chunks_path or config.CHUNKS_PATH
    try:
        selected_chunk_rows = load_chunks(selected_chunks) if chunks is None else list(chunks)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ReleaseIdentityError("bundled chunks are malformed or unreadable") from exc
    if content_version(selected_chunk_rows) != descriptor.content_version:
        raise ReleaseIdentityError("bundled content identity does not match descriptor")
    if corpus_version(selected_chunk_rows) != descriptor.corpus_version:
        raise ReleaseIdentityError(
            "bundled compatibility corpus identity does not match descriptor"
        )
    recomputed_release = build_release_identity(
        descriptor.source_revision,
        descriptor.config_version,
        content_version=descriptor.content_version,
        snapshot_version=descriptor.snapshot_version,
    )
    if recomputed_release != descriptor.release:
        raise ReleaseIdentityError("recomputed release identity does not match descriptor")

    present = {
        field: values.get(variable)
        for field, variable in _IDENTITY_ENVIRONMENT.items()
        if values.get(variable) not in {None, ""}
    }
    function_version = values.get("AWS_LAMBDA_FUNCTION_VERSION", "")
    numeric_lambda = bool(re.fullmatch(r"[1-9][0-9]*", function_version))
    if present and len(present) != len(_IDENTITY_ENVIRONMENT):
        missing = sorted(set(_IDENTITY_ENVIRONMENT) - set(present))
        raise ReleaseIdentityError(
            "release identity environment is partial; missing " + ", ".join(missing)
        )
    if (require_environment or numeric_lambda) and not present:
        raise ReleaseIdentityError("complete release identity environment is required")
    if present:
        expected = {
            "source_revision": descriptor.source_revision,
            "config_version": descriptor.config_version,
            "content_version": descriptor.content_version,
            "snapshot_version": descriptor.snapshot_version,
            "release_version": descriptor.release_version,
            "corpus_version": descriptor.corpus_version,
        }
        mismatched = sorted(
            field for field, expected_value in expected.items() if present[field] != expected_value
        )
        if mismatched:
            raise ReleaseIdentityError(
                "release identity environment does not match descriptor: " + ", ".join(mismatched)
            )
    return descriptor


__all__ = [
    "CONFIG_SCHEMA",
    "DESCRIPTOR_SCHEMA",
    "DOMAIN_PROFILE_SCHEMA",
    "HISTORY_KEY_ID_SCHEMA",
    "PROMPT_NAMES",
    "RELEASE_SCHEMA",
    "WEB_POLICY_SCOPE",
    "ConfigIdentity",
    "EvidenceIdentity",
    "ReleaseDescriptor",
    "ReleaseIdentity",
    "ReleaseIdentityError",
    "ReleaseIdentityValidationError",
    "WebPolicyEvidence",
    "build_config_identity",
    "build_release_descriptor",
    "build_release_identity",
    "descriptor_bytes",
    "history_key_id",
    "load_release_descriptor",
    "parse_release_descriptor",
    "resolve_current_snapshot",
    "verify_release_descriptor",
    "write_release_descriptor",
]
