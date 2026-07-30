"""Immutable Lambda release-control and rollback regression tests."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from assistant import config

DEPLOY = config.REPO_ROOT / "infra" / "deploy.sh"
VERSION_HEALTH = config.REPO_ROOT / "infra" / "check-lambda-version.sh"
ROLLBACK = config.REPO_ROOT / "infra" / "rollback.sh"


def _install_fake_aws(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(
        """#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import sys

args = sys.argv[1:]
state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
state = json.loads(state_path.read_text())

def value(flag):
    return args[args.index(flag) + 1]

def save():
    state_path.write_text(json.dumps(state))

if args[:2] == ["lambda", "invoke"]:
    qualifier = value("--qualifier")
    event_path = pathlib.Path(value("--payload").removeprefix("fileb://"))
    output_path = pathlib.Path(args[-1])
    event = json.loads(event_path.read_text())
    path = event["rawPath"]
    body = json.loads(event.get("body") or "{}")
    disabled_documents = [
        item
        for item in os.environ.get("FAKE_DISABLED_DOC_IDS", "yolobus-fares").split(",")
        if item
    ]
    state.setdefault("invocations", []).append({
        "path": path,
        "question": body.get("question", ""),
        "health_marker": event.get("fare_assistant_health"),
        "log_tail": value("--log-type") == "Tail" if "--log-type" in args else False,
    })
    save()
    headers = {
        "cache-control": "no-store",
        "content-type": "application/json",
        "x-content-type-options": "nosniff",
    }
    if path == "/":
        headers["content-type"] = "text/html"
        result_body = "<html><title>Transit Fare Policy Assistant</title></html>"
    elif path == "/version":
        result_body = json.dumps({
            "corpus_version": "0938fff0539a",
            "matches_pin": True,
            "disabled_documents": disabled_documents,
        })
    elif "Social Security" in body.get("question", ""):
        result_body = json.dumps({
            "kind": "refused_input",
            "answer": "Leave personal details out.",
            "citations": [],
        })
    elif "Yolobus" in body.get("question", ""):
        if "yolobus-fares" in disabled_documents:
            result_body = json.dumps({
                "kind": "refused_no_support",
                "answer": "No current source support.",
                "citations": [],
            })
        else:
            result_body = json.dumps({
                "kind": "answered",
                "answer": "The reviewed source is active.",
                "citations": [{
                    "agency": "Yolobus",
                    "title": "Fares",
                    "url": "https://yolobus.com/fares/",
                    "fetch_date": "2026-07-29",
                }],
            })
    else:
        result_body = json.dumps({
            "kind": "answered",
            "answer": "Bring published proof.",
            "corpus_version": "0938fff0539a",
            "as_of_date": "2026-07-29",
            "citations": [{
                "agency": "MST",
                "title": "Veteran fares",
                "url": "https://mst.org/fares/",
                "fetch_date": "2026-07-29",
            }],
        })
    output_path.write_text(json.dumps({
        "statusCode": 200,
        "headers": headers,
        "body": result_body,
    }))
    executed = os.environ.get("FAKE_EXECUTED_VERSION", qualifier)
    metadata = {"StatusCode": 200, "ExecutedVersion": executed}
    if "--log-type" in args and value("--log-type") == "Tail":
        request_id = f"fake-request-{qualifier}"
        genai_event = {
            "timestamp": "2026-07-29T00:00:00.000Z",
            "level": "INFO",
            "message": "genai_call",
            "logger": "fare_assistant",
            "requestId": request_id,
            "event": "genai_call",
            "runtime_request_id": request_id,
            "function_version": qualifier,
            "gen_ai.system": "anthropic",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "claude-haiku-4-5",
            "gen_ai.response.model": "claude-haiku-4-5",
            "gen_ai.usage.input_tokens": 48,
            "gen_ai.usage.output_tokens": 12,
            "gen_ai.client.operation.duration": 0.125,
            "portfolio.gen_ai.cost.usd": 0.000123,
            "input_tokens": 48,
            "output_tokens": 12,
            "model_duration_ms": 125,
            "estimated_cost_usd": 0.000123,
            "cost_estimate_available": True,
            "completion_recorded": True,
            "error_type": None,
        }
        answer_event = {
            "timestamp": "2026-07-29T00:00:00.126Z",
            "level": "INFO",
            "message": "answer_request",
            "logger": "fare_assistant",
            "requestId": request_id,
            "event": "answer_request",
            "runtime_request_id": request_id,
            "function_version": qualifier,
            "kind": "answered",
            "language": "en",
            "question_chars": 49,
            "turns": 0,
            "duration_ms": 126,
            "request_duration_ms": 126,
            "cache": "bypass",
            "model_called": True,
            "structured_ok": True,
            "status_code": 200,
            "direct_health": True,
            "input_tokens": 48,
            "output_tokens": 12,
            "completion_recorded": True,
        }
        mode = os.environ.get("FAKE_STRUCTURED_LOG_MODE", "valid")
        if mode == "missing-answer":
            log_events = [genai_event]
        elif mode == "mismatched-request":
            answer_event["requestId"] = "different-request"
            answer_event["runtime_request_id"] = "different-request"
            log_events = [genai_event, answer_event]
        elif mode == "content-leak":
            genai_event["question"] = body.get("question", "")
            log_events = [genai_event, answer_event]
        else:
            log_events = [genai_event, answer_event]
        log_text = "\\n".join(json.dumps(item, separators=(",", ":")) for item in log_events)
        metadata["LogResult"] = base64.b64encode((log_text + "\\n").encode()).decode()
    print(json.dumps(metadata))
elif args[:2] == ["lambda", "get-alias"]:
    alias = value("--name")
    response = state["aliases"][alias]
    if "--query" in args and value("--query") == "FunctionVersion":
        print(response["FunctionVersion"])
    else:
        print(json.dumps(response))
elif args[:2] == ["lambda", "get-function-configuration"]:
    disabled = os.environ.get("FAKE_DISABLED_DOC_IDS", "yolobus-fares")
    print(json.dumps({
        "FunctionName": value("--function-name"),
        "Version": value("--qualifier") if "--qualifier" in args else "$LATEST",
        "Environment": {"Variables": {
            "FPA_PINNED_CORPUS_VERSION": "0938fff0539a",
            "FPA_DISABLED_DOC_IDS": disabled,
            "FPA_HISTORY_HMAC_KEY": "0" * 64,
        }},
    }))
elif args[:2] == ["lambda", "get-runtime-management-config"]:
    if "--query" in args and value("--query") == "UpdateRuntimeOn":
        print("FunctionUpdate")
    else:
        print(json.dumps({"UpdateRuntimeOn": "FunctionUpdate"}))
elif args[:2] == ["lambda", "update-alias"]:
    alias = value("--name")
    expected_revision = value("--revision-id")
    if "--routing-config" not in args:
        print("missing explicit routing config", file=sys.stderr)
        raise SystemExit(2)
    routing = json.loads(value("--routing-config"))
    if routing.get("AdditionalVersionWeights") != {}:
        print("routing weights were not cleared", file=sys.stderr)
        raise SystemExit(2)
    if state["aliases"][alias]["RevisionId"] != expected_revision:
        print("PreconditionFailedException", file=sys.stderr)
        raise SystemExit(1)
    state["counter"] += 1
    state["aliases"][alias] = {
        "AliasArn": f"arn:test:{alias}",
        "Name": alias,
        "FunctionVersion": value("--function-version"),
        "RevisionId": f"{alias}-r{state['counter']}",
        "Description": value("--description"),
        "RoutingConfig": {"AdditionalVersionWeights": {}},
    }
    save()
    print(json.dumps(state["aliases"][alias]))
elif args[:2] == ["apigatewayv2", "get-apis"]:
    print(json.dumps(["test-api"]))
elif args[:2] == ["apigatewayv2", "get-integrations"]:
    print(json.dumps(state["integrations"]))
else:
    print("unsupported fake aws call: " + " ".join(args), file=sys.stderr)
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    fake_aws.chmod(0o755)
    return bin_dir


def _install_fake_curl(bin_dir: Path) -> None:
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
headers_path = pathlib.Path(args[args.index("--dump-header") + 1])
body_path = pathlib.Path(args[args.index("--output") + 1])
url = args[-1]
payload = args[args.index("--data") + 1] if "--data" in args else ""
state = json.loads(pathlib.Path(os.environ["FAKE_AWS_STATE"]).read_text())
live = state["aliases"]["live"]["FunctionVersion"]
fail_version = os.environ.get("FAKE_PUBLIC_FAIL_VERSION")
disabled_documents = [
    item
    for item in os.environ.get("FAKE_DISABLED_DOC_IDS", "yolobus-fares").split(",")
    if item
]
question = json.loads(payload).get("question", "") if payload else ""
state.setdefault("public_requests", []).append({"url": url, "question": question})
pathlib.Path(os.environ["FAKE_AWS_STATE"]).write_text(json.dumps(state))

if live == os.environ.get("FAKE_PUBLIC_SLEEP_VERSION"):
    time.sleep(float(os.environ.get("FAKE_PUBLIC_SLEEP_SECONDS", "30")))

if live == fail_version:
    concurrent_version = os.environ.get("FAKE_CONCURRENT_LIVE_VERSION")
    if concurrent_version:
        state["counter"] += 1
        state["aliases"]["live"] = {
            **state["aliases"]["live"],
            "FunctionVersion": concurrent_version,
            "RevisionId": f"concurrent-r{state['counter']}",
            "Description": "concurrent operator change",
        }
        pathlib.Path(os.environ["FAKE_AWS_STATE"]).write_text(json.dumps(state))
    headers_path.write_text(
        "HTTP/1.1 500 Internal Server Error\\r\\n"
        "content-type: text/plain\\r\\n\\r\\n"
    )
    body_path.write_text("synthetic failed release")
    print("500", end="")
    raise SystemExit(0)

security = [
    "cache-control: no-store",
    "content-security-policy: default-src 'none'; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'self'",
    "referrer-policy: no-referrer",
    "x-content-type-options: nosniff",
]
if url.endswith("/version"):
    content_type = "application/json"
    body = json.dumps({
        "corpus_version": "0938fff0539a",
        "as_of": "2026-07-29",
        "agencies": ["MST"],
        "matches_pin": True,
        "disabled_documents": disabled_documents,
    })
    response_headers = security + ["x-frame-options: DENY"]
elif url.endswith("/api/ask"):
    content_type = "application/json"
    question = json.loads(payload)["question"]
    if "Social Security" in question:
        body = json.dumps({
            "answer": "Leave details out.",
            "kind": "refused_input",
            "citations": [],
        })
    elif "Yolobus" in question:
        if "yolobus-fares" in disabled_documents:
            body = json.dumps({
                "answer": "No support.",
                "kind": "refused_no_support",
                "citations": [],
            })
        else:
            body = json.dumps({
                "answer": "The reviewed source is active.",
                "kind": "answered",
                "citations": [{
                    "agency": "Yolobus",
                    "title": "Fares",
                    "url": "https://yolobus.com/fares/",
                    "fetch_date": "2026-07-29",
                }],
            })
    else:
        body = json.dumps({
            "answer": "Bring proof.",
            "kind": "answered",
            "corpus_version": "0938fff0539a",
            "as_of_date": "2026-07-29",
            "citations": [{
                "agency": "MST",
                "title": "Veteran fares",
                "url": "https://mst.org/fares/",
                "fetch_date": "2026-07-29",
            }],
        })
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
    "\\r\\n".join(["HTTP/1.1 200 OK", f"content-type: {content_type}", *response_headers, "", ""])
)
body_path.write_text(body)
print("200", end="")
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)


def _state(tmp_path: Path) -> Path:
    path = tmp_path / "aws-state.json"
    path.write_text(
        json.dumps(
            {
                "counter": 1,
                "aliases": {
                    "live": {
                        "AliasArn": "arn:test:live",
                        "Name": "live",
                        "FunctionVersion": "5",
                        "RevisionId": "live-r1",
                        "Description": "current live",
                        "RoutingConfig": {"AdditionalVersionWeights": {}},
                    },
                    "rollback": {
                        "AliasArn": "arn:test:rollback",
                        "Name": "rollback",
                        "FunctionVersion": "4",
                        "RevisionId": "rollback-r1",
                        "Description": "prior live",
                        "RoutingConfig": {"AdditionalVersionWeights": {}},
                    },
                },
                "integrations": [
                    {
                        "IntegrationId": "integration-1",
                        "IntegrationUri": "arn:test:live",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_direct_version_health_checks_exact_numeric_candidate(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--function-name",
            "fare-policy-assistant-demo",
            "--qualifier",
            "6",
            "--expected-corpus",
            "0938fff0539a",
            "--expected-disabled-docs",
            "yolobus-fares",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("version health: PASS: fare-policy-assistant-demo:6")


def test_direct_version_health_requires_safe_structured_log_tail(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    telemetry_path = tmp_path / "candidate-telemetry.json"
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--function-name",
            "fare-policy-assistant-demo",
            "--qualifier",
            "6",
            "--expected-corpus",
            "0938fff0539a",
            "--expected-disabled-docs",
            "yolobus-fares",
            "--require-structured-telemetry",
            "--telemetry-output",
            str(telemetry_path),
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text())
    tailed = [invocation for invocation in state["invocations"] if invocation["log_tail"]]
    assert tailed == [
        {
            "path": "/api/ask",
            "question": "What proof do I need for the veteran fare on MST?",
            "health_marker": "release-v1",
            "log_tail": True,
        }
    ]
    assert telemetry_path.is_file()
    telemetry = json.loads(telemetry_path.read_text())
    serialized = json.dumps(telemetry, sort_keys=True)
    assert "genai_call" in serialized
    assert "answer_request" in serialized
    assert "What proof" not in serialized


@pytest.mark.parametrize("log_mode", ["missing-answer", "mismatched-request"])
def test_direct_version_health_rejects_incomplete_structured_log_tail(tmp_path, log_mode):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--qualifier",
            "6",
            "--require-structured-telemetry",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_STRUCTURED_LOG_MODE": log_mode,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0


def test_direct_version_health_rejects_content_in_structured_log_tail(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--qualifier",
            "6",
            "--require-structured-telemetry",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_STRUCTURED_LOG_MODE": "content-leak",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0


def test_direct_version_health_rejects_wrong_executed_version(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(VERSION_HEALTH), "--qualifier", "6"],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_EXECUTED_VERSION": "5",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "exact version 6" in result.stderr


def test_direct_version_health_explicit_empty_skips_yolobus_containment(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [
            str(VERSION_HEALTH),
            "--qualifier",
            "6",
            "--expected-disabled-docs",
            "",
        ],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_DISABLED_DOC_IDS": "",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    questions = [
        request["question"] for request in json.loads(state_path.read_text()).get("invocations", [])
    ]
    assert not any("Yolobus" in question for question in questions)
    assert "Yolobus containment" not in result.stdout


def test_direct_version_health_default_detects_missing_containment(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(VERSION_HEALTH), "--qualifier", "6"],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_DISABLED_DOC_IDS": "",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "/version did not match" in result.stderr


def test_rollback_moves_live_alias_to_retained_version_and_smokes(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text())
    assert state["aliases"]["live"]["FunctionVersion"] == "4"
    assert "rollback: PASS: live moved 5 -> 4" in result.stdout


def test_rollback_restores_displaced_live_version_when_public_smoke_fails(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_PUBLIC_FAIL_VERSION": "4",
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    state = json.loads(state_path.read_text())
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert "restoring displaced version 5" in result.stderr


def test_rollback_allows_explicitly_empty_required_disabled_documents(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_DISABLED_DOC_IDS": "",
            "FPA_REQUIRED_DISABLED_DOC_IDS": "",
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(state_path.read_text())
    assert state["aliases"]["live"]["FunctionVersion"] == "4"
    questions = [request["question"] for request in state.get("invocations", [])] + [
        request["question"] for request in state.get("public_requests", [])
    ]
    assert not any("Yolobus" in question for question in questions)


def test_rollback_rejects_unqualified_api_integration_before_alias_move(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    state = json.loads(state_path.read_text())
    state["integrations"][0]["IntegrationUri"] = (
        "arn:aws:lambda:us-west-2:123456789012:function:fare-policy-assistant-demo"
    )
    state_path.write_text(json.dumps(state))
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "does not target the qualified live alias" in result.stderr
    assert json.loads(state_path.read_text())["aliases"]["live"]["FunctionVersion"] == "5"


def test_rollback_rejects_weighted_alias_before_alias_move(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    state = json.loads(state_path.read_text())
    state["aliases"]["live"]["RoutingConfig"]["AdditionalVersionWeights"] = {"4": 0.1}
    state_path.write_text(json.dumps(state))
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "weighted routing" in result.stderr
    assert json.loads(state_path.read_text())["aliases"]["live"]["FunctionVersion"] == "5"


def test_rollback_guard_does_not_overwrite_concurrent_live_change(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_PUBLIC_FAIL_VERSION": "4",
            "FAKE_CONCURRENT_LIVE_VERSION": "7",
            "FPA_API_ID": "test-api",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    state = json.loads(state_path.read_text())
    assert state["aliases"]["live"]["FunctionVersion"] == "7"
    assert "did not overwrite it" in result.stderr


def test_rollback_deadline_bounds_public_smoke_and_restores_live(tmp_path):
    fake_bin = _install_fake_aws(tmp_path)
    _install_fake_curl(fake_bin)
    state_path = _state(tmp_path)
    started = time.monotonic()
    result = subprocess.run(
        [str(ROLLBACK)],
        cwd=config.REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_AWS_STATE": str(state_path),
            "FAKE_PUBLIC_SLEEP_VERSION": "4",
            "FAKE_PUBLIC_SLEEP_SECONDS": "30",
            "FPA_API_ID": "test-api",
            "FPA_ROLLBACK_MAX_SECONDS": "6",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=12,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert elapsed < 8
    state = json.loads(state_path.read_text())
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert "restoring displaced version 5" in result.stderr


class TestDeployReleaseControlStructure:
    def test_bootstrap_precedes_every_latest_mutation(self):
        text = DEPLOY.read_text(encoding="utf-8")
        bootstrap = text.index("bootstrap pre-alias live")
        bundle = text.index("# ── bundle")
        update_config = text.index("aws lambda update-function-configuration")
        assert bootstrap < bundle < update_config

    def test_interrupted_bootstrap_reuses_only_an_exact_numbered_snapshot(self):
        text = DEPLOY.read_text(encoding="utf-8")
        bootstrap = text.split(
            "# Existing deployments originally exposed the mutable, unqualified function.",
            1,
        )[1].split("# ── bundle", 1)[0]
        assert "list-versions-by-function" in bootstrap
        assert "BOOTSTRAP_MATCHING_VERSION" in bootstrap
        assert "exact_published_version" in bootstrap
        matcher = text.split("exact_published_version() {", 1)[1].split("# ── stable alias", 1)[0]
        assert "get-function-configuration" in matcher
        assert "same_versioned_release_config" in matcher
        assert "normalized_release_config" in matcher
        assert "key not in non_behavioral" in matcher
        normalized = text.split("normalized_release_config() {", 1)[1].split(
            "same_versioned_release_config() {", 1
        )[0]
        assert '"RuntimeVersionConfig"' not in normalized

    def test_candidate_is_published_and_checked_before_live_alias_moves(self):
        text = DEPLOY.read_text(encoding="utf-8")
        release = text.split("# ── Lambda", 1)[1]
        update_config = release.index("aws lambda update-function-configuration")
        update_code = release.index("aws lambda update-function-code")
        publish = release.index("aws lambda publish-version")
        health = release.index('"$ROOT/infra/check-lambda-version.sh"')
        live_move = release.index("aws lambda update-alias", health)
        assert update_config < update_code < publish < health < live_move
        assert "--code-sha256" in release
        assert "--revision-id" in release
        assert "--update-runtime-on FunctionUpdate" in release

    def test_api_gateway_targets_only_the_stable_alias(self):
        text = DEPLOY.read_text(encoding="utf-8")
        assert 'ALIAS_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$FN:$LIVE_ALIAS"' in text
        assert "$ALIAS_ARN/invocations" in text
        assert '--qualifier "$LIVE_ALIAS"' in text
        assert "--statement-id apigw-live" in text
        assert '--target "arn:aws:lambda:$REGION:$ACCOUNT:function:$FN"' not in text

    def test_alias_moves_clear_weights_and_exit_guard_is_prearmed(self):
        text = DEPLOY.read_text(encoding="utf-8")
        assert "AdditionalVersionWeights" in text
        for block in text.split("aws lambda update-alias")[1:]:
            invocation = block.split("--output json", 1)[0]
            assert '--routing-config "$EMPTY_ALIAS_ROUTING"' in invocation
        promotion = text.split('PROMOTION_DESCRIPTION="', 1)[1]
        assert promotion.index("PROMOTION_GUARD_ACTIVE=true") < promotion.index(
            "aws lambda update-alias"
        )
        assert "trap 'exit 130' INT" in text
        assert "trap 'exit 143' TERM" in text

    def test_unmanaged_latest_config_is_checked_before_and_after_staging(self):
        text = DEPLOY.read_text(encoding="utf-8")
        assert "unmanaged_config_snapshot" in text
        for field in (
            "Layers",
            "VpcConfig",
            "DeadLetterConfig",
            "TracingConfig",
            "KMSKeyArn",
            "FileSystemConfigs",
            "EphemeralStorage",
            "SnapStart",
        ):
            assert field in text
        unmanaged_snapshot = text.split("unmanaged_config_snapshot() {", 1)[1].split(
            "normalized_release_config() {", 1
        )[0]
        assert '"LoggingConfig",' in unmanaged_snapshot
        assert 'snapshot["LoggingConfig"]' not in unmanaged_snapshot
        early_check = text.index('"$LIVE_REVIEWED_CONFIG" "$LATEST_REVIEW_CONFIG"')
        bundle = text.index("# ── bundle")
        prestage_check = text.index('"$LIVE_REVIEWED_CONFIG" "$PRESTAGE_LATEST_CONFIG"')
        update = text.index("aws lambda update-function-configuration", prestage_check)
        candidate_check = text.index('"$LIVE_REVIEWED_CONFIG" "$CANDIDATE_CONFIG"', update)
        publish = text.index("aws lambda publish-version", candidate_check)
        assert early_check < bundle < prestage_check < update < candidate_check < publish

    def test_advanced_logging_is_exact_managed_release_state(self):
        text = DEPLOY.read_text(encoding="utf-8")
        assert (
            'LOGGING_CONFIG="LogFormat=JSON,ApplicationLogLevel=INFO,'
            'SystemLogLevel=WARN,LogGroup=$LOG_GROUP"'
        ) in text
        managed_assertion = text.split("assert_managed_release_config() {", 1)[1].split(
            "exact_published_version() {", 1
        )[0]
        assert '"LogFormat": "JSON"' in managed_assertion
        assert '"ApplicationLogLevel": "INFO"' in managed_assertion
        assert '"SystemLogLevel": "WARN"' in managed_assertion
        assert '"LogGroup": $log_group' in managed_assertion
        release = text.split("# ── Lambda", 1)[1]
        assert release.count('--logging-config "$LOGGING_CONFIG"') >= 2

    def test_interrupted_bootstrap_repairs_only_a_missing_rollback_alias(self):
        text = DEPLOY.read_text(encoding="utf-8")
        helper = text.split("ensure_rollback_alias_exists() {", 1)[1].split(
            "public_assistant_smoke() {", 1
        )[0]
        assert "get-alias" in helper
        assert "ResourceNotFoundException" in helper
        assert "create-alias" in helper
        assert "update-alias" not in helper
        steady_state = text.split('elif [[ "$HAS_LIVE_ALIAS" == "true" ]]', 1)[1].split(
            "# The immutable live version", 1
        )[0]
        assert 'ensure_rollback_alias_exists "$LIVE_VERSION"' in steady_state

    def test_conflicting_live_permission_sid_fails_closed(self):
        text = DEPLOY.read_text(encoding="utf-8")
        permission = text.split("ensure_alias_permission() {", 1)[1].split(
            "remove_unqualified_api_permission() {", 1
        )[0]
        conflict = permission.index("alias policy statement apigw-live exists but does not match")
        add = permission.index("aws lambda add-permission")
        assert conflict < add

    def test_public_failure_is_covered_by_revision_guarded_exit_restore(self):
        text = DEPLOY.read_text(encoding="utf-8")
        guard = text.split("restore_unverified_live() {", 1)[1].split(
            "restore_previous_rollback_pointer() {", 1
        )[0]
        promotion = text.split('PROMOTION_DESCRIPTION="', 1)[1].split("# The first deploy used", 1)[
            0
        ]
        assert '--function-version "$PROMOTION_GUARD_RESTORE_VERSION"' in guard
        assert '--revision-id "$current_revision"' in guard
        assert '--routing-config "$EMPTY_ALIAS_ROUTING"' in guard
        assert "PROMOTION_GUARD_ACTIVE=true" in promotion
        assert promotion.index("PROMOTION_GUARD_ACTIVE=true") < promotion.index(
            "aws lambda update-alias"
        )
        assert "public_assistant_smoke" in promotion
        assert "PROMOTION_GUARD_ACTIVE=false" in promotion

    def test_rollbacks_never_target_latest(self):
        text = ROLLBACK.read_text(encoding="utf-8")
        assert '[[ "$TARGET_VERSION" =~ ^[1-9][0-9]*$ ]]' in text
        assert "--revision-id" in text
        assert "ELAPSED_SECONDS" in text
        assert "MAX_SECONDS" in text
        assert "get-runtime-management-config" in text
        assert '"$TARGET_RUNTIME_MODE" == "FunctionUpdate"' in text

    def test_shared_iam_drift_requires_explicit_migration_and_live_smoke(self):
        text = DEPLOY.read_text(encoding="utf-8")
        iam = text.split("# ── IAM role", 1)[1].split("# ── Lambda", 1)[0]
        assert "get-role-policy" in iam
        assert "FPA_ALLOW_SHARED_IAM_CHANGE" in iam
        assert "alias rollback cannot recover it" in iam
        mutation = iam.index("aws iam put-role-policy", iam.index("ROLE_POLICY_MATCHES"))
        smoke = iam.index("public_assistant_smoke", mutation)
        restoration = iam.index("restoring the prior policy", smoke)
        assert mutation < smoke < restoration

    def test_integration_failure_restores_route_before_old_permission_removal(self):
        text = DEPLOY.read_text(encoding="utf-8")
        route = text.split("ensure_api_targets_live() {", 1)[1].split(
            "# Existing deployments originally", 1
        )[0]
        restore = route.index('--integration-uri "$original_uri"')
        abort = route.index("exit 1", restore)
        permission_removal = route.index("remove_unqualified_api_permission", abort)
        assert restore < abort < permission_removal
