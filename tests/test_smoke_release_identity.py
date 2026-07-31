"""Focused public-smoke tests for strict and legacy release identity modes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from assistant import config

SMOKE = config.REPO_ROOT / "scripts" / "smoke-production.sh"

SOURCE = "a" * 40
CONFIG = "b" * 64
CONTENT = "c" * 64
SNAPSHOT = "d" * 64
RELEASE = "e" * 64
ARTIFACT = "A" * 43 + "="
FUNCTION_VERSION = "10"

FAKE_CURL = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
headers_path = pathlib.Path(args[args.index("--dump-header") + 1])
body_path = pathlib.Path(args[args.index("--output") + 1])
url = args[-1]
payload = args[args.index("--data") + 1] if "--data" in args else ""
state_path = pathlib.Path(os.environ["FAKE_CURL_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
state.setdefault("requests", []).append(url)
state_path.write_text(json.dumps(state), encoding="utf-8")

security = [
    "cache-control: no-store",
    "content-security-policy: default-src 'none'; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'self'",
    "referrer-policy: no-referrer",
    "x-content-type-options: nosniff",
]

if url.endswith("/version"):
    content_type = "application/json"
    body = os.environ["FAKE_VERSION_BODY"]
    response_headers = security + ["x-frame-options: DENY"]
elif url.endswith("/api/ask"):
    content_type = "application/json"
    question = json.loads(payload)["question"]
    if "Social Security" in question:
        body = json.dumps(
            {
                "answer": "Please leave personal details out.",
                "kind": "refused_input",
                "citations": [],
            }
        )
    elif "Yolobus" in question:
        body = json.dumps(
            {
                "answer": "No current source support.",
                "kind": "refused_no_support",
                "citations": [],
            }
        )
    else:
        body = json.dumps(
            {
                "answer": "Bring published proof.",
                "kind": "answered",
                "corpus_version": "test-corpus",
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
    response_headers = security + ["x-frame-options: DENY"]
else:
    content_type = "text/html"
    markers = {
        "/": "Transit Fare Policy Assistant",
        "/offline": "Offline fare reference",
        "/guide": "Which fare applies to me?",
        "/embed": "Transit fare policy assistant",
    }
    path = "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else "/"
    body = f"<html><title>{markers[path]}</title></html>"
    response_headers = security
    if path != "/embed":
        response_headers.append("x-frame-options: DENY")

headers_path.write_text(
    "\r\n".join(
        ["HTTP/1.1 200 OK", f"content-type: {content_type}", *response_headers, "", ""]
    ),
    encoding="utf-8",
)
body_path.write_text(body, encoding="utf-8")
sys.stdout.write("200")
"""


def _install_fake_curl(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(FAKE_CURL, encoding="utf-8")
    fake_curl.chmod(0o755)
    state_path = tmp_path / "curl-state.json"
    state_path.write_text(json.dumps({"requests": []}), encoding="utf-8")
    return bin_dir, state_path


def _version_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "corpus_version": "test-corpus",
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
        "identity_status": "verified",
        "source_revision": SOURCE,
        "config_version": CONFIG,
        "content_version": CONTENT,
        "snapshot_version": SNAPSHOT,
        "release_version": RELEASE,
        "artifact_code_sha256": ARTIFACT,
        "function_version": FUNCTION_VERSION,
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
        "--expected-function-version",
        FUNCTION_VERSION,
    ]


def _without_option(arguments: list[str], option: str) -> list[str]:
    index = arguments.index(option)
    return arguments[:index] + arguments[index + 2 :]


def _run_smoke(
    tmp_path: Path,
    *,
    mode_args: list[str],
    version_body: dict[str, object] | None = None,
    disabled_docs: str = "yolobus-fares",
) -> tuple[subprocess.CompletedProcess[str], dict]:
    fake_bin, state_path = _install_fake_curl(tmp_path)
    body = version_body if version_body is not None else _version_body()
    result = subprocess.run(
        [
            str(SMOKE),
            "--assistant-only",
            "--assistant-base-url",
            "http://assistant.test",
            "--connect-timeout",
            "1",
            "--max-time",
            "2",
            "--expected-disabled-docs",
            disabled_docs,
            *mode_args,
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "LC_ALL": "C",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_CURL_STATE": str(state_path),
            "FAKE_VERSION_BODY": json.dumps(body),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, json.loads(state_path.read_text(encoding="utf-8"))


def test_strict_public_smoke_requires_the_exact_verified_release(tmp_path: Path) -> None:
    result, state = _run_smoke(tmp_path, mode_args=_strict_args())

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("smoke: PASS")
    assert any(url.endswith("/version") for url in state["requests"])
    assert sum(url.endswith("/api/ask") for url in state["requests"]) == 3


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
def test_public_smoke_identity_mode_is_explicit_and_exclusive(
    tmp_path: Path,
    mode_args: list[str],
    message: str,
) -> None:
    result, state = _run_smoke(tmp_path, mode_args=mode_args)

    assert result.returncode != 0
    assert message in result.stderr
    assert state["requests"] == []


@pytest.mark.parametrize(
    "option",
    [
        "--expected-source",
        "--expected-config",
        "--expected-content",
        "--expected-snapshot",
        "--expected-release",
        "--expected-artifact",
        "--expected-function-version",
    ],
)
def test_strict_public_smoke_requires_every_expected_release_field(
    tmp_path: Path,
    option: str,
) -> None:
    result, state = _run_smoke(
        tmp_path,
        mode_args=_without_option(_strict_args(), option),
    )

    assert result.returncode != 0
    assert "requires every expected release identity argument" in result.stderr
    assert state["requests"] == []


@pytest.mark.parametrize(
    ("option", "invalid", "message"),
    [
        ("--expected-source", "A" * 40, "40-character lowercase"),
        ("--expected-config", "g" * 64, "64-character lowercase"),
        ("--expected-content", "c" * 63, "64-character lowercase"),
        ("--expected-snapshot", "D" * 64, "64-character lowercase"),
        ("--expected-release", "e" * 65, "64-character lowercase"),
        ("--expected-artifact", "f" * 64, "AWS-style base64"),
        ("--expected-function-version", "0", "numeric published version"),
    ],
)
def test_strict_public_smoke_rejects_malformed_expected_release_fields(
    tmp_path: Path,
    option: str,
    invalid: str,
    message: str,
) -> None:
    arguments = _strict_args()
    arguments[arguments.index(option) + 1] = invalid
    result, state = _run_smoke(tmp_path, mode_args=arguments)

    assert result.returncode != 0
    assert message in result.stderr
    assert state["requests"] == []


@pytest.mark.parametrize(
    "field",
    [
        "identity_status",
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "artifact_code_sha256",
        "function_version",
    ],
)
def test_strict_public_smoke_rejects_runtime_release_drift_before_paid_health(
    tmp_path: Path,
    field: str,
) -> None:
    body = _version_body()
    body[field] = "wrong"

    result, state = _run_smoke(
        tmp_path,
        mode_args=_strict_args(),
        version_body=body,
    )

    assert result.returncode != 0
    assert "invalid verified release identity" in result.stderr
    assert not any(url.endswith("/api/ask") for url in state["requests"])


def test_strict_public_smoke_preserves_disabled_document_containment_check(
    tmp_path: Path,
) -> None:
    body = _version_body(disabled_documents=[])

    result, state = _run_smoke(
        tmp_path,
        mode_args=_strict_args(),
        version_body=body,
    )

    assert result.returncode != 0
    assert "invalid verified release identity" in result.stderr
    assert not any(url.endswith("/api/ask") for url in state["requests"])


def test_explicit_legacy_smoke_allows_content_identity_without_release_tuple(
    tmp_path: Path,
) -> None:
    legacy_body = {
        "corpus_version": "test-corpus",
        "content_version": CONTENT,
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
    }

    result, state = _run_smoke(
        tmp_path,
        mode_args=["--allow-legacy-release-identity"],
        version_body=legacy_body,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("smoke: PASS")
    assert sum(url.endswith("/api/ask") for url in state["requests"]) == 3


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "source_revision",
        "config_version",
        "snapshot_version",
        "release_version",
        "artifact_code_sha256",
    ],
)
def test_legacy_smoke_rejects_identity_bearing_runtime_fields(
    tmp_path: Path,
    forbidden_field: str,
) -> None:
    legacy_body = {
        "corpus_version": "test-corpus",
        "content_version": CONTENT,
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
        forbidden_field: "",
    }

    result, state = _run_smoke(
        tmp_path,
        mode_args=["--allow-legacy-release-identity"],
        version_body=legacy_body,
    )

    assert result.returncode != 0
    assert "invalid explicit legacy release identity" in result.stderr
    assert not any(url.endswith("/api/ask") for url in state["requests"])


def test_legacy_smoke_rejects_verified_identity_status(tmp_path: Path) -> None:
    legacy_body = {
        "corpus_version": "test-corpus",
        "content_version": CONTENT,
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": ["yolobus-fares"],
        "identity_status": "verified",
    }

    result, state = _run_smoke(
        tmp_path,
        mode_args=["--allow-legacy-release-identity"],
        version_body=legacy_body,
    )

    assert result.returncode != 0
    assert "invalid explicit legacy release identity" in result.stderr
    assert not any(url.endswith("/api/ask") for url in state["requests"])


def test_legacy_smoke_rejects_expected_release_arguments(tmp_path: Path) -> None:
    result, state = _run_smoke(
        tmp_path,
        mode_args=[
            "--allow-legacy-release-identity",
            "--expected-content",
            CONTENT,
        ],
    )

    assert result.returncode != 0
    assert "does not accept expected release identity arguments" in result.stderr
    assert state["requests"] == []
