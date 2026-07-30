#!/usr/bin/env python3
"""Build the deterministic release descriptor from the exact current inputs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from assistant import config
from assistant.corpus import corpus_version
from assistant.ingest import load_chunks
from assistant.release_identity import (
    ReleaseDescriptor,
    ReleaseIdentityError,
    build_config_identity,
    build_release_descriptor,
    resolve_current_snapshot,
    write_release_descriptor,
)

_EFFECTIVE_ENVIRONMENT_JSON = "FPA_RELEASE_EFFECTIVE_ENVIRONMENT_JSON"


def _git(repo_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseIdentityError("could not inspect the Git source state") from exc
    return result.stdout.strip()


def clean_source_revision(repo_root: Path) -> str:
    """Return HEAD only when the complete source checkout is clean."""
    revision = _git(repo_root, "rev-parse", "HEAD")
    dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=normal")
    if dirty:
        raise ReleaseIdentityError(
            "working tree is dirty; commit the complete release before building a descriptor"
        )
    return revision


def build_current_descriptor(
    source_revision: str,
    *,
    environment: Mapping[str, str] | None = None,
    chunks_path: Path | None = None,
    manifest_path: Path | None = None,
    raw_dir: Path | None = None,
    snapshots_dir: Path | None = None,
    prompts_dir: Path | None = None,
    answer_schema_path: Path | None = None,
) -> ReleaseDescriptor:
    """Pure, injectable descriptor build after an external clean-source check."""
    selected_chunks = chunks_path or config.CHUNKS_PATH
    chunks = load_chunks(selected_chunks)
    identity = resolve_current_snapshot(
        chunks_path=selected_chunks,
        manifest_path=manifest_path,
        raw_dir=raw_dir,
        snapshots_dir=snapshots_dir,
    )
    config_identity = build_config_identity(
        environment,
        prompts_dir=prompts_dir,
        answer_schema_path=answer_schema_path,
    )
    return build_release_descriptor(
        source_revision,
        config_identity,
        content_version=identity.content_version,
        snapshot_version=identity.snapshot_version,
        corpus_version=corpus_version(chunks),
    )


def effective_runtime_environment() -> Mapping[str, str]:
    """Read the deployer's final customer environment without printing secrets."""
    encoded = os.environ.get(_EFFECTIVE_ENVIRONMENT_JSON)
    if encoded is None:
        return os.environ
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ReleaseIdentityError(
            f"{_EFFECTIVE_ENVIRONMENT_JSON} must contain valid JSON"
        ) from exc
    if isinstance(decoded, Mapping) and set(decoded) == {"Variables"}:
        decoded = decoded["Variables"]
    if not isinstance(decoded, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()
    ):
        raise ReleaseIdentityError(
            f"{_EFFECTIVE_ENVIRONMENT_JSON} must contain a string environment mapping"
        )
    values = dict(decoded)
    values["AWS_REGION"] = os.environ.get("AWS_REGION", config.DEFAULT_AWS_REGION)
    return values


def _secret_free_environment_summary(
    descriptor: ReleaseDescriptor,
    output: Path,
) -> dict[str, str]:
    signing = descriptor.config.payload["runtime"]
    assert isinstance(signing, Mapping)
    signing = signing["history_signing"]
    assert isinstance(signing, Mapping)
    key_id = signing["key_id"]
    return {
        "descriptor_path": str(output),
        "FPA_SOURCE_REVISION": descriptor.source_revision,
        "FPA_CONFIG_VERSION": descriptor.config_version,
        "FPA_PINNED_CONTENT_VERSION": descriptor.content_version,
        "FPA_PINNED_SNAPSHOT_VERSION": descriptor.snapshot_version,
        "FPA_RELEASE_VERSION": descriptor.release_version,
        "FPA_PINNED_CORPUS_VERSION": descriptor.corpus_version,
        "FPA_HISTORY_HMAC_KEY_ID": key_id if isinstance(key_id, str) else "",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=config.RELEASE_DESCRIPTOR_PATH,
        help="canonical descriptor output path",
    )
    parser.add_argument(
        "--source-revision",
        help="must equal clean HEAD when supplied (primarily for explicit CI wiring)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        revision = clean_source_revision(config.REPO_ROOT)
        if args.source_revision is not None and args.source_revision != revision:
            raise ReleaseIdentityError("--source-revision does not equal clean Git HEAD")
        descriptor = build_current_descriptor(
            revision,
            environment=effective_runtime_environment(),
        )
        output = write_release_descriptor(descriptor, args.output)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        print(f"release descriptor build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            _secret_free_environment_summary(descriptor, output),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
