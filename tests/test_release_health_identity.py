"""Focused contract tests for numeric Lambda release-identity health checks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from assistant import config

VERSION_HEALTH = config.REPO_ROOT / "infra" / "check-lambda-version.sh"

SOURCE = "a" * 40
CONFIG = "b" * 64
CONTENT = "c" * 64
SNAPSHOT = "d" * 64
RELEASE = "e" * 64
ARTIFACT = "A" * 43 + "="
CORPUS = "0938fff0539a"
QUALIFIER = "6"

IDENTITY_ENV = {
    "FPA_SOURCE_REVISION": SOURCE,
    "FPA_CONFIG_VERSION": CONFIG,
    "FPA_PINNED_CONTENT_VERSION": CONTENT,
    "FPA_PINNED_SNAPSHOT_VERSION": SNAPSHOT,
    "FPA_RELEASE_VERSION": RELEASE,
    "FPA_ARTIFACT_CODE_SHA256": ARTIFACT,
}

FAKE_AWS = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))


def value(flag, default=None):
    if flag not in args:
        return default
    return args[args.index(flag) + 1]


def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")


if args[:2] == ["lambda", "get-function-configuration"]:
    print(os.environ["FAKE_QUALIFIED_CONFIG"])
    raise SystemExit(0)

if args[:2] != ["lambda", "invoke"]:
    print("unsupported fake aws call: " + " ".join(args), file=sys.stderr)
    raise SystemExit(2)

qualifier = value("--qualifier")
event_path = pathlib.Path(value("--payload").removeprefix("fileb://"))
event = json.loads(event_path.read_text(encoding="utf-8"))
path = event["rawPath"]
request = json.loads(event.get("body") or "{}")
output_path = pathlib.Path(args[-1])
state.setdefault("invocations", []).append(path)
save()

headers = {
    "cache-control": "no-store",
    "content-type": "application/json",
    "x-content-type-options": "nosniff",
}
if path == "/":
    headers["content-type"] = "text/html"
    body = "<html><title>Transit Fare Policy Assistant</title></html>"
elif path == "/version":
    body = os.environ["FAKE_VERSION_BODY"]
elif "Social Security" in request.get("question", ""):
    body = json.dumps(
        {
            "kind": "refused_input",
            "answer": "Leave personal details out.",
            "citations": [],
        }
    )
elif "Yolobus" in request.get("question", ""):
    body = json.dumps(
        {
            "kind": "refused_no_support",
            "answer": "No current source support.",
            "citations": [],
        }
    )
else:
    body = json.dumps(
        {
            "kind": "answered",
            "answer": "Bring published proof.",
            "corpus_version": "0938fff0539a",
            "as_of_date": "2026-07-29",
            "citations": [
                {
                    "agency": "MST",
                    "title": "Veteran fares",
                    "url": "https://mst.org/fares/",
                    "fetch_date": "2026-07-29",
                }
            ],
        }
    )

output_path.write_text(
    json.dumps({"statusCode": 200, "headers": headers, "body": body}),
    encoding="utf-8",
)
print(json.dumps({"StatusCode": 200, "ExecutedVersion": qualifier}))
"""


def _install_fake_aws(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(FAKE_AWS, encoding="utf-8")
    fake_aws.chmod(0o755)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"invocations": []}), encoding="utf-8")
    return bin_dir, state_path


def _qualified_config(environment: dict[str, str] | None = None) -> dict:
    return {
        "FunctionName": "fare-policy-assistant-demo",
        "Version": QUALIFIER,
        "CodeSha256": ARTIFACT,
        "Environment": {
            "Variables": {
                "FPA_PINNED_CORPUS_VERSION": CORPUS,
                "FPA_DISABLED_DOC_IDS": "yolobus-fares",
                **(IDENTITY_ENV if environment is None else environment),
            }
        },
    }


def _version_body(**overrides: object) -> dict:
    body: dict[str, object] = {
        "corpus_version": CORPUS,
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
        "identity_status": "verified",
        "function_version": QUALIFIER,
        "source_revision": SOURCE,
        "config_version": CONFIG,
        "content_version": CONTENT,
        "snapshot_version": SNAPSHOT,
        "release_version": RELEASE,
        "artifact_code_sha256": ARTIFACT,
    }
    body.update(overrides)
    return body


def _strict_args() -> list[str]:
    return [
        "--require-release-identity",
        "--expected-source",
        SOURCE,
        "--expected-config",
        CONFIG,
        "--expected-content",
        CONTENT,
        "--expected-snapshot",
        SNAPSHOT,
        "--expected-release",
        RELEASE,
        "--expected-artifact",
        ARTIFACT,
    ]


def _run_health(
    tmp_path: Path,
    *,
    mode_args: list[str],
    qualified_config: dict | None = None,
    version_body: dict | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    fake_bin, state_path = _install_fake_aws(tmp_path)
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--function-name",
            "fare-policy-assistant-demo",
            "--qualifier",
            QUALIFIER,
            "--expected-corpus",
            CORPUS,
            "--expected-disabled-docs",
            "yolobus-fares",
            *mode_args,
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_QUALIFIED_CONFIG": json.dumps(
                qualified_config if qualified_config is not None else _qualified_config()
            ),
            "FAKE_VERSION_BODY": json.dumps(
                version_body if version_body is not None else _version_body()
            ),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, json.loads(state_path.read_text(encoding="utf-8"))


def _without_option(arguments: list[str], option: str) -> list[str]:
    index = arguments.index(option)
    return arguments[:index] + arguments[index + 2 :]


def test_strict_identity_mode_verifies_control_plane_and_runtime_tuple(tmp_path: Path) -> None:
    result, state = _run_health(tmp_path, mode_args=_strict_args())

    assert result.returncode == 0, result.stderr
    assert "qualified release identity" in result.stdout
    assert result.stdout.rstrip().endswith("version health: PASS: fare-policy-assistant-demo:6")
    assert state["invocations"] == ["/", "/version", "/api/ask", "/api/ask", "/api/ask"]


@pytest.mark.parametrize(
    ("mode_args", "message"),
    [
        ([], "choose exactly one"),
        (
            ["--allow-legacy-release-identity", *_strict_args()],
            "choose exactly one",
        ),
    ],
)
def test_identity_mode_must_be_explicit_and_exclusive(
    tmp_path: Path,
    mode_args: list[str],
    message: str,
) -> None:
    result, state = _run_health(tmp_path, mode_args=mode_args)

    assert result.returncode != 0
    assert message in result.stderr
    assert state["invocations"] == []


@pytest.mark.parametrize(
    "option",
    [
        "--expected-source",
        "--expected-config",
        "--expected-content",
        "--expected-snapshot",
        "--expected-release",
        "--expected-artifact",
    ],
)
def test_strict_mode_requires_the_complete_expected_tuple(
    tmp_path: Path,
    option: str,
) -> None:
    result, state = _run_health(
        tmp_path,
        mode_args=_without_option(_strict_args(), option),
    )

    assert result.returncode != 0
    assert "requires every --expected-* identity argument" in result.stderr
    assert state["invocations"] == []


@pytest.mark.parametrize(
    ("option", "invalid", "message"),
    [
        ("--expected-source", "A" * 40, "40-character lowercase"),
        ("--expected-config", "g" * 64, "64-character lowercase"),
        ("--expected-content", "c" * 63, "64-character lowercase"),
        ("--expected-snapshot", "D" * 64, "64-character lowercase"),
        ("--expected-release", "e" * 65, "64-character lowercase"),
        ("--expected-artifact", "f" * 64, "AWS-style base64"),
    ],
)
def test_strict_mode_rejects_malformed_expected_identities(
    tmp_path: Path,
    option: str,
    invalid: str,
    message: str,
) -> None:
    arguments = _strict_args()
    arguments[arguments.index(option) + 1] = invalid
    result, state = _run_health(tmp_path, mode_args=arguments)

    assert result.returncode != 0
    assert message in result.stderr
    assert state["invocations"] == []


@pytest.mark.parametrize(
    "field",
    [
        "FPA_SOURCE_REVISION",
        "FPA_CONFIG_VERSION",
        "FPA_PINNED_CONTENT_VERSION",
        "FPA_PINNED_SNAPSHOT_VERSION",
        "FPA_RELEASE_VERSION",
        "FPA_ARTIFACT_CODE_SHA256",
        "CodeSha256",
        "Version",
    ],
)
def test_strict_mode_rejects_qualified_configuration_drift_before_invocation(
    tmp_path: Path,
    field: str,
) -> None:
    qualified = _qualified_config()
    if field in {"CodeSha256", "Version"}:
        qualified[field] = "wrong"
    else:
        qualified["Environment"]["Variables"][field] = "wrong"

    result, state = _run_health(
        tmp_path,
        mode_args=_strict_args(),
        qualified_config=qualified,
    )

    assert result.returncode != 0
    assert "qualified Lambda configuration" in result.stderr
    assert state["invocations"] == []


@pytest.mark.parametrize(
    "field",
    [
        "identity_status",
        "function_version",
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "artifact_code_sha256",
    ],
)
def test_strict_mode_rejects_runtime_version_identity_drift_before_paid_health(
    tmp_path: Path,
    field: str,
) -> None:
    body = _version_body()
    body[field] = "wrong"

    result, state = _run_health(
        tmp_path,
        mode_args=_strict_args(),
        version_body=body,
    )

    assert result.returncode != 0
    assert "verified numeric release identity" in result.stderr
    assert state["invocations"] == ["/", "/version"]


def test_explicit_legacy_mode_allows_content_identity_without_release_tuple(
    tmp_path: Path,
) -> None:
    legacy_config = _qualified_config(environment={})
    legacy_body = {
        "corpus_version": CORPUS,
        "content_version": CONTENT,
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
    }

    result, state = _run_health(
        tmp_path,
        mode_args=["--allow-legacy-release-identity"],
        qualified_config=legacy_config,
        version_body=legacy_body,
    )

    assert result.returncode == 0, result.stderr
    assert "explicit legacy release identity" in result.stdout
    assert state["invocations"] == ["/", "/version", "/api/ask", "/api/ask", "/api/ask"]


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "FPA_SOURCE_REVISION",
        "FPA_CONFIG_VERSION",
        "FPA_PINNED_SNAPSHOT_VERSION",
        "FPA_RELEASE_VERSION",
        "FPA_ARTIFACT_CODE_SHA256",
    ],
)
def test_legacy_mode_rejects_any_identity_capable_target(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    result, state = _run_health(
        tmp_path,
        mode_args=["--allow-legacy-release-identity"],
        qualified_config=_qualified_config(environment={forbidden_key: ""}),
        version_body={
            "corpus_version": CORPUS,
            "matches_pin": True,
            "disabled_documents": ["yolobus-fares"],
        },
    )

    assert result.returncode != 0
    assert "legacy mode requires" in result.stderr
    assert state["invocations"] == []


def test_legacy_mode_rejects_expected_identity_arguments(tmp_path: Path) -> None:
    result, state = _run_health(
        tmp_path,
        mode_args=[
            "--allow-legacy-release-identity",
            "--expected-content",
            CONTENT,
        ],
    )

    assert result.returncode != 0
    assert "does not accept --expected-* identity arguments" in result.stderr
    assert state["invocations"] == []
