"""Behavioral tests for the immutable Lambda deployment state machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from assistant import config

DEPLOY = config.REPO_ROOT / "infra" / "deploy.sh"
ACCOUNT = "123456789012"
REGION = "us-west-2"
FUNCTION = "fare-policy-assistant-demo"
API_ID = "test-api"
CORPUS_VERSION = "0938fff0539a"
HISTORY_KEY = "0" * 64
ALIAS_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:live"
ALIAS_URI = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{ALIAS_ARN}/invocations"


FAKE_AWS = r"""
#!/usr/bin/env python3
import copy
import base64
import hashlib
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


def record(op, **details):
    state["events"].append({"op": op, **details})


def save():
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, state_path)


def finish(payload=None, *, text=False):
    save()
    if payload is not None:
        print(payload if text else json.dumps(payload))
    raise SystemExit(0)


def fail(message, *, status=2, op="fake.error"):
    record(op, command=args, message=message)
    save()
    print(message, file=sys.stderr)
    raise SystemExit(status)


def next_revision(prefix):
    state["revision_counter"] += 1
    return f"{prefix}-r{state['revision_counter']}"


def alias_policy():
    source = (
        f"arn:aws:execute-api:{state['region']}:{state['account']}:"
        f"{state['api_id']}/*"
    )
    resource = (
        f"arn:aws:lambda:{state['region']}:{state['account']}:"
        f"function:{state['function_name']}:live"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "apigw-live",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": resource,
                "Principal": {"Service": "apigateway.amazonaws.com"},
                "Condition": {"ArnLike": {"AWS:SourceArn": source}},
            }
        ],
    }


def role_policy():
    region = state["region"]
    account = state["account"]
    function_name = state["function_name"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": (
                    f"arn:aws:logs:{region}:{account}:"
                    f"log-group:/aws/lambda/{function_name}*"
                ),
            },
            {
                "Effect": "Allow",
                "Action": "bedrock:InvokeModel",
                "Resource": [
                    (
                        f"arn:aws:bedrock:{region}:{account}:inference-profile/"
                        "us.anthropic.claude-haiku-4-5-20251001-v1:0"
                    ),
                    (
                        "arn:aws:bedrock:*::foundation-model/"
                        "anthropic.claude-haiku-4-5-20251001-v1:0"
                    ),
                ],
            },
        ],
    }


def invoke_payload(event, qualifier):
    path = event["rawPath"]
    request_body = json.loads(event.get("body") or "{}")
    disabled_documents = [
        item
        for item in state["configs"][qualifier]["Environment"]["Variables"]
        .get("FPA_DISABLED_DOC_IDS", "")
        .split(",")
        if item
    ]
    headers = {
        "cache-control": "no-store",
        "content-type": "application/json",
        "x-content-type-options": "nosniff",
    }
    if path == "/":
        headers["content-type"] = "text/html"
        body = "<html><title>Transit Fare Policy Assistant</title></html>"
    elif path == "/version":
        body = json.dumps(
            {
                "corpus_version": state["corpus_version"],
                "matches_pin": True,
                "disabled_documents": disabled_documents,
            }
        )
    elif "Social Security" in request_body.get("question", ""):
        body = json.dumps(
            {
                "kind": "refused_input",
                "answer": "Leave personal details out.",
                "citations": [],
            }
        )
    elif "Yolobus" in request_body.get("question", ""):
        if "yolobus-fares" in disabled_documents:
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
                    "answer": "The reviewed source is active.",
                    "citations": [
                        {
                            "agency": "Yolobus",
                            "title": "Fares",
                            "url": "https://yolobus.com/fares/",
                            "fetch_date": "2026-07-29",
                        }
                    ],
                }
            )
    else:
        body = json.dumps(
            {
                "kind": "answered",
                "answer": "Bring published proof.",
                "corpus_version": state["corpus_version"],
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
    return {"statusCode": 200, "headers": headers, "body": body}


if args[:2] == ["sts", "get-caller-identity"]:
    record("sts.get-caller-identity")
    if value("--query") == "Account" and value("--output") == "text":
        finish(state["account"], text=True)
    finish({"Account": state["account"]})

if args[:2] == ["lambda", "get-function-configuration"]:
    qualifier = value("--qualifier", "$LATEST")
    if qualifier not in state["configs"]:
        fail("ResourceNotFoundException", status=1, op="lambda.get-config.missing")
    if qualifier == "$LATEST":
        state["latest_config_reads"] = state.get("latest_config_reads", 0) + 1
        trigger = state.get("competing_bootstrap_env_on_read", 0)
        if trigger and state["latest_config_reads"] == trigger:
            latest = state["configs"]["$LATEST"]
            latest["Environment"]["Variables"]["FPA_EMBED_ANCESTORS"] = (
                "https://concurrent.example"
            )
            latest["RevisionId"] = next_revision("latest-bootstrap-race")
            record("concurrent.bootstrap-environment-update")
    function_config = state["configs"][qualifier]
    record("lambda.get-function-configuration", qualifier=qualifier, query=value("--query"))
    if value("--query") == "Environment.Variables":
        finish(function_config["Environment"]["Variables"])
    if value("--query") == "RevisionId" and value("--output") == "text":
        finish(function_config["RevisionId"], text=True)
    finish(function_config)

if args[:2] == ["lambda", "get-function"]:
    qualifier = value("--qualifier", "$LATEST")
    record("lambda.get-function", qualifier=qualifier)
    if value("--query") == "Code.Location" and value("--output") == "text":
        finish("https://fake.invalid/function.zip", text=True)
    finish({"Code": {"Location": "https://fake.invalid/function.zip"}})

if args[:2] == ["lambda", "get-alias"]:
    alias_name = value("--name")
    if alias_name not in state["aliases"]:
        fail("ResourceNotFoundException", status=1, op="lambda.get-alias.missing")
    if alias_name == "live":
        state["live_alias_reads"] = state.get("live_alias_reads", 0) + 1
        trigger = state.get("competing_live_on_read", 0)
        if trigger and state["live_alias_reads"] == trigger:
            state["aliases"]["live"] = {
                **state["aliases"]["live"],
                "FunctionVersion": "7",
                "Description": "concurrent reviewed release",
                "RevisionId": next_revision("live-concurrent"),
            }
            record("concurrent.live-promotion", to_version="7")
    alias = state["aliases"][alias_name]
    record("lambda.get-alias", alias=alias_name)
    if value("--query") == "FunctionVersion" and value("--output") == "text":
        finish(alias["FunctionVersion"], text=True)
    finish(alias)

if args[:2] == ["lambda", "update-function-configuration"]:
    latest = state["configs"]["$LATEST"]
    expected_revision = value("--revision-id")
    if latest["RevisionId"] != expected_revision:
        fail(
            "PreconditionFailedException",
            status=1,
            op="lambda.update-function-configuration.conflict",
        )
    before = latest["RevisionId"]
    latest["Runtime"] = value("--runtime")
    latest["Handler"] = value("--handler")
    latest["Timeout"] = int(value("--timeout"))
    latest["MemorySize"] = int(value("--memory-size"))
    latest["Role"] = value("--role")
    latest["Environment"] = json.loads(value("--environment"))
    latest["RevisionId"] = next_revision("latest")
    response = copy.deepcopy(latest)
    if state.get("config_revision_changes_on_wait"):
        state["settle_config_revision_pending"] = True
    record(
        "lambda.update-function-configuration",
        from_revision=before,
        to_revision=latest["RevisionId"],
    )
    finish(response)

if args[:2] == ["lambda", "update-function-code"]:
    latest = state["configs"]["$LATEST"]
    expected_revision = value("--revision-id")
    if latest["RevisionId"] != expected_revision:
        fail(
            "PreconditionFailedException",
            status=1,
            op="lambda.update-function-code.conflict",
        )
    before = latest["RevisionId"]
    bundle_path = pathlib.Path(value("--zip-file").removeprefix("fileb://"))
    local_code_sha = base64.b64encode(
        hashlib.sha256(bundle_path.read_bytes()).digest()
    ).decode("ascii")
    latest["Architectures"] = [value("--architectures")]
    latest["CodeSha256"] = local_code_sha
    latest["CodeSize"] = 4242
    latest["RevisionId"] = next_revision("latest")
    response = copy.deepcopy(latest)
    record(
        "lambda.update-function-code",
        from_revision=before,
        to_revision=latest["RevisionId"],
    )
    if state.get("competing_latest_after_code"):
        latest["CodeSha256"] = "foreign-code-sha"
        latest["Environment"]["Variables"]["FPA_DISABLED_DOC_IDS"] = ""
        latest["RevisionId"] = next_revision("latest-foreign")
        record("concurrent.latest-update", revision=latest["RevisionId"])
    finish(response)

if args[:2] == ["lambda", "list-versions-by-function"]:
    versions = []
    for version, function_config in state["configs"].items():
        versions.append(
            {"Version": version, "CodeSha256": function_config["CodeSha256"]}
        )
    record("lambda.list-versions-by-function")
    finish({"Versions": versions})

if args[:2] == ["lambda", "publish-version"]:
    latest = state["configs"]["$LATEST"]
    if latest["RevisionId"] != value("--revision-id"):
        fail("PreconditionFailedException", status=1, op="lambda.publish-version.conflict")
    if latest["CodeSha256"] != value("--code-sha256"):
        fail("CodeSha256Mismatch", status=1, op="lambda.publish-version.sha-mismatch")
    version = str(state["next_version"])
    state["next_version"] += 1
    published = copy.deepcopy(latest)
    published["Version"] = version
    published["FunctionArn"] = (
        f"arn:aws:lambda:{state['region']}:{state['account']}:"
        f"function:{state['function_name']}:{version}"
    )
    published["Description"] = value("--description")
    published["RevisionId"] = next_revision(f"version-{version}")
    state["configs"][version] = published
    record("lambda.publish-version", version=version)
    if value("--query") == "Version" and value("--output") == "text":
        finish(version, text=True)
    finish(published)

if args[:2] == ["lambda", "put-runtime-management-config"]:
    qualifier = value("--qualifier")
    state["runtime_modes"][qualifier] = value("--update-runtime-on")
    record(
        "lambda.put-runtime-management-config",
        qualifier=qualifier,
        mode=state["runtime_modes"][qualifier],
    )
    finish({})

if args[:2] == ["lambda", "get-runtime-management-config"]:
    qualifier = value("--qualifier")
    mode = state["runtime_modes"].get(qualifier)
    if mode is None:
        fail("ResourceNotFoundException", status=1, op="lambda.get-runtime.missing")
    record("lambda.get-runtime-management-config", qualifier=qualifier)
    if value("--query") == "UpdateRuntimeOn" and value("--output") == "text":
        finish(mode, text=True)
    finish({"UpdateRuntimeOn": mode})

if args[:2] == ["lambda", "invoke"]:
    qualifier = value("--qualifier")
    if qualifier not in state["configs"] or qualifier == "$LATEST":
        fail("InvalidParameterValueException", status=1, op="lambda.invoke.invalid")
    event_path = pathlib.Path(value("--payload").removeprefix("fileb://"))
    event = json.loads(event_path.read_text(encoding="utf-8"))
    output_path = pathlib.Path(args[-1])
    output_path.write_text(json.dumps(invoke_payload(event, qualifier)), encoding="utf-8")
    record("lambda.invoke", qualifier=qualifier, path=event["rawPath"])
    finish({"StatusCode": 200, "ExecutedVersion": qualifier})

if args[:2] == ["lambda", "get-policy"]:
    qualifier = value("--qualifier")
    record("lambda.get-policy", qualifier=qualifier)
    if qualifier == "live" and state["alias_permission"]:
        finish(
            {
                "Policy": json.dumps(alias_policy()),
                "RevisionId": state["aliases"]["live"]["RevisionId"],
            }
        )
    fail("ResourceNotFoundException", status=1, op="lambda.get-policy.missing")

if args[:2] == ["lambda", "add-permission"]:
    if value("--qualifier") != "live" or value("--statement-id") != "apigw-live":
        fail("unexpected permission scope", op="lambda.add-permission.invalid")
    alias = state["aliases"]["live"]
    if alias["RevisionId"] != value("--revision-id"):
        fail(
            "PreconditionFailedException",
            status=1,
            op="lambda.add-permission.conflict",
        )
    before = alias["RevisionId"]
    alias["RevisionId"] = next_revision("live-policy")
    state["alias_permission"] = True
    record(
        "lambda.add-permission",
        qualifier="live",
        statement_id="apigw-live",
        from_revision=before,
        to_revision=alias["RevisionId"],
    )
    finish(
        {
            "Statement": json.dumps(alias_policy()["Statement"][0]),
            "RevisionId": alias["RevisionId"],
        }
    )

if args[:2] == ["lambda", "remove-permission"]:
    statement_id = value("--statement-id")
    record("lambda.remove-permission", statement_id=statement_id)
    fail("ResourceNotFoundException", status=1, op="lambda.remove-permission.missing")

if args[:2] == ["lambda", "update-alias"]:
    alias_name = value("--name")
    if alias_name not in state["aliases"]:
        fail("ResourceNotFoundException", status=1, op="lambda.update-alias.missing")
    alias = state["aliases"][alias_name]
    if alias["RevisionId"] != value("--revision-id"):
        fail("PreconditionFailedException", status=1, op="lambda.update-alias.conflict")
    routing_raw = value("--routing-config")
    if routing_raw is None:
        fail("update-alias omitted routing config", op="lambda.update-alias.weighted")
    routing = json.loads(routing_raw)
    if routing != {"AdditionalVersionWeights": {}}:
        fail("update-alias did not clear weights", op="lambda.update-alias.weighted")
    from_version = alias["FunctionVersion"]
    alias["FunctionVersion"] = value("--function-version")
    alias["RevisionId"] = next_revision(alias_name)
    alias["Description"] = value("--description", alias.get("Description", ""))
    alias["RoutingConfig"] = routing
    record(
        "lambda.update-alias",
        alias=alias_name,
        from_version=from_version,
        to_version=alias["FunctionVersion"],
        revision=alias["RevisionId"],
        routing=routing,
    )
    finish(alias)

if args[:2] == ["lambda", "create-alias"]:
    alias_name = value("--name")
    if alias_name in state["aliases"]:
        fail("ResourceConflictException", status=1, op="lambda.create-alias.conflict")
    routing = json.loads(value("--routing-config"))
    if routing != {"AdditionalVersionWeights": {}}:
        fail("create-alias did not clear weights", op="lambda.create-alias.weighted")
    alias = {
        "AliasArn": (
            f"arn:aws:lambda:{state['region']}:{state['account']}:"
            f"function:{state['function_name']}:{alias_name}"
        ),
        "Name": alias_name,
        "FunctionVersion": value("--function-version"),
        "Description": value("--description", ""),
        "RevisionId": next_revision(alias_name),
        "RoutingConfig": routing,
    }
    state["aliases"][alias_name] = alias
    record("lambda.create-alias", alias=alias_name, to_version=alias["FunctionVersion"])
    finish(alias)

if args[:2] == ["lambda", "wait"]:
    if (
        args[2] == "function-updated"
        and state.pop("settle_config_revision_pending", False)
    ):
        latest = state["configs"]["$LATEST"]
        before = latest["RevisionId"]
        latest["RevisionId"] = next_revision("latest-settled")
        record(
            "lambda.configuration-settled",
            from_revision=before,
            to_revision=latest["RevisionId"],
        )
    record("lambda.wait", waiter=args[2])
    finish()

if args[:2] == ["lambda", "put-function-concurrency"]:
    record(
        "lambda.put-function-concurrency",
        value=value("--reserved-concurrent-executions"),
    )
    finish({})

if args[:2] == ["lambda", "delete-function-url-config"]:
    record("lambda.delete-function-url-config")
    fail(
        "ResourceNotFoundException",
        status=1,
        op="lambda.delete-function-url-config.missing",
    )

if args[:2] == ["apigatewayv2", "get-api"]:
    if value("--api-id") != state["api_id"]:
        fail("NotFoundException", status=1, op="apigatewayv2.get-api.missing")
    record("apigatewayv2.get-api")
    finish({"ApiId": state["api_id"], "Name": state["function_name"]})

if args[:2] == ["apigatewayv2", "get-apis"]:
    record("apigatewayv2.get-apis")
    finish([state["api_id"]])

if args[:2] == ["apigatewayv2", "get-integrations"]:
    record("apigatewayv2.get-integrations")
    finish([state["integration"]])

if args[:2] == ["apigatewayv2", "get-integration"]:
    record("apigatewayv2.get-integration")
    if value("--query") == "IntegrationUri" and value("--output") == "text":
        finish(state["integration"]["IntegrationUri"], text=True)
    finish(state["integration"])

if args[:2] == ["apigatewayv2", "update-integration"]:
    old_uri = state["integration"]["IntegrationUri"]
    state["integration"]["IntegrationUri"] = value("--integration-uri")
    record(
        "apigatewayv2.update-integration",
        from_uri=old_uri,
        to_uri=state["integration"]["IntegrationUri"],
    )
    finish(state["integration"])

if args[:2] == ["apigatewayv2", "update-stage"]:
    record("apigatewayv2.update-stage")
    finish({})

if args[:2] == ["iam", "get-role"]:
    record("iam.get-role")
    finish({"Role": {"Arn": state["role_arn"]}})

if args[:2] == ["iam", "get-role-policy"]:
    record("iam.get-role-policy")
    finish({"PolicyDocument": role_policy()})

if args[:2] == ["sns", "create-topic"]:
    topic_arn = (
        f"arn:aws:sns:{state['region']}:{state['account']}:"
        f"{state['function_name']}-alerts"
    )
    record("sns.create-topic")
    if value("--query") == "TopicArn" and value("--output") == "text":
        finish(topic_arn, text=True)
    finish({"TopicArn": topic_arn})

allowed_noop_operations = {
    ("logs", "create-log-group"),
    ("logs", "put-retention-policy"),
    ("logs", "put-metric-filter"),
    ("cloudwatch", "put-metric-alarm"),
    ("cloudwatch", "put-dashboard"),
}
if tuple(args[:2]) in allowed_noop_operations:
    operation = ".".join(args[:2])
    record(operation)
    finish({})

fail("unsupported fake aws call: " + " ".join(args))
""".lstrip()


FAKE_CURL = r"""
#!/usr/bin/env python3
import json
import os
import pathlib
import sys
from urllib.parse import urlparse

args = sys.argv[1:]
state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])


def value(flag, default=None):
    if flag not in args:
        return default
    return args[args.index(flag) + 1]


def load_state():
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state):
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, state_path)


if "--dump-header" not in args:
    output_path = pathlib.Path(value("--output"))
    output_path.write_bytes(b"synthetic encrypted-at-rest rollback artifact")
    state = load_state()
    state["events"].append({"op": "artifact.download"})
    save_state(state)
    raise SystemExit(0)

headers_path = pathlib.Path(value("--dump-header"))
body_path = pathlib.Path(value("--output"))
url = args[-1]
path = urlparse(url).path or "/"
payload = value("--data", "")
state = load_state()
if state["integration"]["IntegrationUri"].endswith(":live/invocations"):
    live_version = state["aliases"]["live"]["FunctionVersion"]
else:
    live_version = "$LATEST"
disabled_documents = [
    item
    for item in state["configs"][live_version]["Environment"]["Variables"]
    .get("FPA_DISABLED_DOC_IDS", "")
    .split(",")
    if item
]
state["events"].append(
    {"op": "public.request", "live_version": live_version, "path": path}
)
save_state(state)

if live_version == os.environ.get("FAKE_PUBLIC_FAIL_VERSION"):
    headers_path.write_text(
        "HTTP/1.1 500 Internal Server Error\r\n"
        "content-type: text/plain\r\n\r\n",
        encoding="utf-8",
    )
    body_path.write_text("synthetic failed release", encoding="utf-8")
    print("500", end="")
    raise SystemExit(0)

security_headers = [
    "cache-control: no-store",
    (
        "content-security-policy: default-src 'none'; connect-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'self'"
    ),
    "referrer-policy: no-referrer",
    "x-content-type-options: nosniff",
]
if path == "/version":
    content_type = "application/json"
    body = json.dumps(
        {
            "corpus_version": state["corpus_version"],
            "as_of": "2026-07-29",
            "agencies": ["MST"],
            "matches_pin": True,
            "disabled_documents": disabled_documents,
        }
    )
    response_headers = [*security_headers, "x-frame-options: DENY"]
elif path == "/api/ask":
    content_type = "application/json"
    question = json.loads(payload)["question"]
    if "Social Security" in question:
        body = json.dumps(
            {
                "answer": "Leave personal details out.",
                "kind": "refused_input",
                "citations": [],
            }
        )
    elif "Yolobus" in question:
        if "yolobus-fares" in disabled_documents:
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
                    "answer": "The reviewed source is active.",
                    "kind": "answered",
                    "citations": [
                        {
                            "agency": "Yolobus",
                            "title": "Fares",
                            "url": "https://yolobus.com/fares/",
                            "fetch_date": "2026-07-29",
                        }
                    ],
                }
            )
    else:
        body = json.dumps(
            {
                "answer": "Bring published proof.",
                "kind": "answered",
                "corpus_version": state["corpus_version"],
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
    response_headers = [*security_headers, "x-frame-options: DENY"]
else:
    content_type = "text/html"
    markers = {
        "/": "Transit Fare Policy Assistant",
        "/offline": "Offline fare reference",
        "/guide": "Which fare applies to me?",
        "/embed": "Transit fare policy assistant",
    }
    if path not in markers:
        print(f"unsupported fake curl path: {path}", file=sys.stderr)
        raise SystemExit(2)
    body = f"<html><title>{markers[path]}</title></html>"
    response_headers = list(security_headers)
    if path != "/embed":
        response_headers.append("x-frame-options: DENY")

headers_path.write_text(
    "\r\n".join(
        [
            "HTTP/1.1 200 OK",
            f"content-type: {content_type}",
            *response_headers,
            "",
            "",
        ]
    ),
    encoding="utf-8",
)
body_path.write_text(body, encoding="utf-8")
print("200", end="")
""".lstrip()


FAKE_UV = r"""
#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ["pip", "install"]:
    target = pathlib.Path(args[args.index("--target") + 1])
    target.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0)
if args[:2] == ["run", "python"]:
    python = os.environ["FAKE_REAL_PYTHON"]
    os.execv(python, [python, *args[2:]])
print("unsupported fake uv call: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""".lstrip()


def _lambda_config(version: str, code_sha: str, revision: str) -> dict[str, Any]:
    function_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
    if version != "$LATEST":
        function_arn = f"{function_arn}:{version}"
    return {
        "FunctionName": FUNCTION,
        "FunctionArn": function_arn,
        "Runtime": "python3.12",
        "Role": f"arn:aws:iam::{ACCOUNT}:role/{FUNCTION}-role",
        "Handler": "web.handler.handler",
        "CodeSize": 4096,
        "Description": f"release {version}",
        "Timeout": 25,
        "MemorySize": 512,
        "LastModified": "2026-07-29T00:00:00.000+0000",
        "CodeSha256": code_sha,
        "Version": version,
        "VpcConfig": {
            "SubnetIds": [],
            "SecurityGroupIds": [],
            "VpcId": "",
            "Ipv6AllowedForDualStack": False,
        },
        "DeadLetterConfig": {},
        "Environment": {
            "Variables": {
                "FPA_PINNED_CORPUS_VERSION": CORPUS_VERSION,
                "FPA_DISABLED_DOC_IDS": "yolobus-fares",
                "FPA_HISTORY_HMAC_KEY": HISTORY_KEY,
            }
        },
        "TracingConfig": {"Mode": "PassThrough"},
        "RevisionId": revision,
        "Layers": [],
        "State": "Active",
        "LastUpdateStatus": "Successful",
        "PackageType": "Zip",
        "Architectures": ["arm64"],
        "EphemeralStorage": {"Size": 512},
        "SnapStart": {"ApplyOn": "None", "OptimizationStatus": "Off"},
        "RuntimeVersionConfig": {"RuntimeVersionArn": "arn:aws:lambda::runtime:python3.12-test"},
        "LoggingConfig": {
            "LogFormat": "Text",
            "LogGroup": f"/aws/lambda/{FUNCTION}",
        },
        "FileSystemConfigs": [],
        "KMSKeyArn": "",
        "RecursiveLoop": "Terminate",
        "FutureBehaviorConfig": {"Mode": "reviewed"},
    }


def _initial_state(
    *,
    latest_layer_drift: bool = False,
    competing_latest_after_code: bool = False,
    competing_live_on_read: int = 0,
    bootstrap_mode: bool = False,
    bootstrap_env_race_read: int = 0,
    config_revision_changes_on_wait: bool = False,
    partial_retry_unknown_drift: bool = False,
) -> dict[str, Any]:
    version_five = _lambda_config("5", "old-five-sha", "version-5-r1")
    latest = _lambda_config("$LATEST", "old-five-sha", "latest-r1")
    if latest_layer_drift:
        latest["Layers"] = [
            {
                "Arn": (f"arn:aws:lambda:{REGION}:{ACCOUNT}:layer:unreviewed-manual-layer:1"),
                "CodeSize": 123,
            }
        ]
    state = {
        "account": ACCOUNT,
        "region": REGION,
        "function_name": FUNCTION,
        "api_id": API_ID,
        "corpus_version": CORPUS_VERSION,
        "next_version": 6,
        "revision_counter": 10,
        "competing_latest_after_code": competing_latest_after_code,
        "competing_live_on_read": competing_live_on_read,
        "config_revision_changes_on_wait": config_revision_changes_on_wait,
        "role_arn": f"arn:aws:iam::{ACCOUNT}:role/{FUNCTION}-role",
        "alias_permission": True,
        "aliases": {
            "live": {
                "AliasArn": ALIAS_ARN,
                "Name": "live",
                "FunctionVersion": "5",
                "Description": "reviewed live release 5",
                "RevisionId": "live-r1",
                "RoutingConfig": {"AdditionalVersionWeights": {}},
            },
            "rollback": {
                "AliasArn": (f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:rollback"),
                "Name": "rollback",
                "FunctionVersion": "4",
                "Description": "reviewed prior release 4",
                "RevisionId": "rollback-r1",
                "RoutingConfig": {"AdditionalVersionWeights": {}},
            },
        },
        "configs": {
            "$LATEST": latest,
            "4": _lambda_config("4", "old-four-sha", "version-4-r1"),
            "5": version_five,
        },
        "runtime_modes": {"4": "FunctionUpdate", "5": "FunctionUpdate"},
        "integration": {
            "IntegrationId": "integration-1",
            "IntegrationUri": ALIAS_URI,
        },
        "events": [],
    }
    if bootstrap_mode or bootstrap_env_race_read:
        state["aliases"] = {}
        state["alias_permission"] = False
        state["integration"]["IntegrationUri"] = (
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
        )
        state["competing_bootstrap_env_on_read"] = bootstrap_env_race_read
    if partial_retry_unknown_drift:
        state["integration"]["IntegrationUri"] = (
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
        )
        state["configs"]["$LATEST"]["FutureBehaviorConfig"] = {"Mode": "unreviewed-new-setting"}
    return state


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _run_deploy(
    tmp_path: Path,
    *,
    fail_public_version: str = "",
    latest_layer_drift: bool = False,
    competing_latest_after_code: bool = False,
    competing_live_on_read: int = 0,
    disabled_docs: str = "yolobus-fares",
    bootstrap_mode: bool = False,
    bootstrap_env_race_read: int = 0,
    config_revision_changes_on_wait: bool = False,
    partial_retry_unknown_drift: bool = False,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "aws", FAKE_AWS)
    _write_executable(bin_dir / "curl", FAKE_CURL)
    _write_executable(bin_dir / "uv", FAKE_UV)
    _write_executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")

    state_path = tmp_path / "aws-state.json"
    state_path.write_text(
        json.dumps(
            _initial_state(
                latest_layer_drift=latest_layer_drift,
                competing_latest_after_code=competing_latest_after_code,
                competing_live_on_read=competing_live_on_read,
                bootstrap_mode=bootstrap_mode,
                bootstrap_env_race_read=bootstrap_env_race_read,
                config_revision_changes_on_wait=config_revision_changes_on_wait,
                partial_retry_unknown_drift=partial_retry_unknown_drift,
            )
        ),
        encoding="utf-8",
    )
    task_tmpdir = tmp_path / "tmp"
    task_tmpdir.mkdir()
    environment = {
        **os.environ,
        "AWS_REGION": REGION,
        "FAKE_AWS_STATE": str(state_path),
        "FAKE_PUBLIC_FAIL_VERSION": fail_public_version,
        "FAKE_REAL_PYTHON": sys.executable,
        "FPA_ALLOW_DIRTY_DEPLOY": "1",
        "FPA_API_ID": API_ID,
        "FPA_PINNED_CORPUS_VERSION": CORPUS_VERSION,
        "FPA_DISABLED_DOC_IDS": disabled_docs,
        "FPA_HISTORY_HMAC_KEY": HISTORY_KEY,
        "FPA_SMOKE_CONNECT_TIMEOUT": "1",
        "FPA_SMOKE_MAX_TIME": "1",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMPDIR": str(task_tmpdir),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }
    result = subprocess.run(
        [str(DEPLOY)],
        cwd=config.REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return result, json.loads(state_path.read_text(encoding="utf-8"))


def _event_index(
    events: list[dict[str, Any]],
    operation: str,
    **details: str,
) -> int:
    for index, event in enumerate(events):
        if event.get("op") != operation:
            continue
        if all(event.get(key) == expected for key, expected in details.items()):
            return index
    raise AssertionError(f"missing event {operation} with {details}: {events}")


def test_successful_deploy_promotes_only_after_candidate_health_and_retains_prior_live(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path)

    assert result.returncode == 0, result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "5"
    assert state["aliases"]["live"]["RoutingConfig"] == {"AdditionalVersionWeights": {}}
    assert state["aliases"]["rollback"]["RoutingConfig"] == {"AdditionalVersionWeights": {}}
    assert state["integration"]["IntegrationUri"] == ALIAS_URI
    assert state["runtime_modes"]["6"] == "FunctionUpdate"
    assert "live Lambda version: 6" in result.stdout
    assert "retained rollback version: 5" in result.stdout

    events = state["events"]
    update_config = _event_index(events, "lambda.update-function-configuration")
    update_code = _event_index(events, "lambda.update-function-code")
    publish = _event_index(events, "lambda.publish-version", version="6")
    direct_candidate = [
        index
        for index, event in enumerate(events)
        if event.get("op") == "lambda.invoke" and event.get("qualifier") == "6"
    ]
    rollback_pointer = _event_index(
        events,
        "lambda.update-alias",
        alias="rollback",
        to_version="5",
    )
    live_promotion = _event_index(
        events,
        "lambda.update-alias",
        alias="live",
        to_version="6",
    )
    candidate_public_smoke = _event_index(
        events,
        "public.request",
        live_version="6",
    )
    dashboard = _event_index(events, "cloudwatch.put-dashboard")

    assert len(direct_candidate) == 5
    assert (
        update_config
        < update_code
        < publish
        < min(direct_candidate)
        <= max(direct_candidate)
        < rollback_pointer
        < live_promotion
        < candidate_public_smoke
        < dashboard
    )


def test_first_bootstrap_accounts_for_alias_permission_revision_change(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, bootstrap_mode=True)

    assert result.returncode == 0, result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "5"
    assert state["integration"]["IntegrationUri"] == ALIAS_URI
    permission = _event_index(
        state["events"],
        "lambda.add-permission",
        qualifier="live",
        statement_id="apigw-live",
    )
    route_cutover = _event_index(state["events"], "apigatewayv2.update-integration")
    candidate_publish = _event_index(
        state["events"],
        "lambda.publish-version",
        version="6",
    )
    assert permission < route_cutover < candidate_publish


def test_code_staging_uses_revision_observed_after_configuration_settles(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, config_revision_changes_on_wait=True)

    assert result.returncode == 0, result.stderr
    settled = _event_index(state["events"], "lambda.configuration-settled")
    code = _event_index(state["events"], "lambda.update-function-code")
    assert settled < code
    settled_event = state["events"][settled]
    code_event = state["events"][code]
    assert code_event["from_revision"] == settled_event["to_revision"]


def test_explicit_empty_containment_is_used_by_candidate_public_smoke(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, disabled_docs="")

    assert result.returncode == 0, result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    assert state["configs"]["6"]["Environment"]["Variables"]["FPA_DISABLED_DOC_IDS"] == ""
    candidate_paths = [
        event["path"]
        for event in state["events"]
        if event.get("op") == "public.request" and event.get("live_version") == "6"
    ]
    assert "/version" in candidate_paths
    assert candidate_paths.count("/api/ask") == 2


def test_public_smoke_failure_restores_live_and_previous_rollback_pointer(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, fail_public_version="6")

    assert result.returncode != 0
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert state["aliases"]["live"]["RoutingConfig"] == {"AdditionalVersionWeights": {}}
    assert state["aliases"]["rollback"]["RoutingConfig"] == {"AdditionalVersionWeights": {}}
    assert "candidate 6 failed public smoke" in result.stderr
    assert "restored unverified live version 6 -> 5" in result.stderr

    events = state["events"]
    rollback_to_old_live = _event_index(
        events,
        "lambda.update-alias",
        alias="rollback",
        to_version="5",
    )
    promote_live = _event_index(
        events,
        "lambda.update-alias",
        alias="live",
        to_version="6",
    )
    failed_public_request = _event_index(
        events,
        "public.request",
        live_version="6",
    )
    restore_live = _event_index(
        events,
        "lambda.update-alias",
        alias="live",
        to_version="5",
    )
    restore_rollback = _event_index(
        events,
        "lambda.update-alias",
        alias="rollback",
        to_version="4",
    )
    assert (
        rollback_to_old_live
        < promote_live
        < failed_public_request
        < restore_live
        < restore_rollback
    )
    assert not any(event["op"] == "cloudwatch.put-dashboard" for event in events)


def test_unmanaged_latest_drift_aborts_before_lambda_staging_or_alias_moves(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, latest_layer_drift=True)

    assert result.returncode != 0
    assert "mutable $LATEST has unmanaged versioned-configuration drift" in result.stderr
    assert "changed unmanaged fields: Layers" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"

    forbidden_operations = {
        "lambda.update-function-configuration",
        "lambda.update-function-code",
        "lambda.publish-version",
        "lambda.update-alias",
        "lambda.create-alias",
    }
    assert not any(event["op"] in forbidden_operations for event in state["events"])


def test_competing_latest_update_cannot_be_published_as_the_local_release(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, competing_latest_after_code=True)

    assert result.returncode != 0
    assert (
        "staged candidate does not match the locally built artifact "
        "and complete managed configuration"
    ) in result.stderr
    assert any(event["op"] == "concurrent.latest-update" for event in state["events"])
    assert not any(event["op"] == "lambda.publish-version" for event in state["events"])
    assert not any(event["op"] == "lambda.update-alias" for event in state["events"])
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"


def test_competing_live_promotion_cannot_mix_release_baselines(tmp_path: Path) -> None:
    result, state = _run_deploy(tmp_path, competing_live_on_read=2)

    assert result.returncode != 0
    assert "refusing to mix release baselines" in result.stderr
    assert any(event["op"] == "concurrent.live-promotion" for event in state["events"])
    assert not any(
        event["op"]
        in {
            "lambda.update-function-configuration",
            "lambda.update-function-code",
            "lambda.publish-version",
            "lambda.update-alias",
        }
        for event in state["events"]
    )
    assert state["aliases"]["live"]["FunctionVersion"] == "7"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"


def test_bootstrap_environment_race_aborts_before_freezing_or_aliasing(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, bootstrap_env_race_read=2)

    assert result.returncode != 0
    assert "environment changed during alias bootstrap" in result.stderr
    assert any(
        event["op"] == "concurrent.bootstrap-environment-update" for event in state["events"]
    )
    assert state["aliases"] == {}
    assert not any(
        event["op"]
        in {
            "lambda.publish-version",
            "lambda.create-alias",
            "lambda.update-function-configuration",
            "lambda.update-function-code",
        }
        for event in state["events"]
    )


def test_bootstrap_source_change_during_cutover_restores_unqualified_route(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, bootstrap_env_race_read=5)

    assert result.returncode != 0
    assert "source changed during route cutover" in result.stderr
    assert state["integration"]["IntegrationUri"] == (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
    )
    migrations = [
        event for event in state["events"] if event.get("op") == "apigatewayv2.update-integration"
    ]
    assert [event["to_uri"] for event in migrations] == [
        ALIAS_URI,
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}",
    ]
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "5"
    assert not any(
        event["op"]
        in {
            "lambda.update-function-configuration",
            "lambda.update-function-code",
        }
        for event in state["events"]
    )


def test_partial_bootstrap_retry_rejects_unknown_latest_configuration_drift(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, partial_retry_unknown_drift=True)

    assert result.returncode != 0
    assert "no longer matches the frozen live alias" in result.stderr
    assert state["integration"]["IntegrationUri"] == (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
    )
    assert not any(event["op"] == "apigatewayv2.update-integration" for event in state["events"])
    assert not any(
        event["op"]
        in {
            "lambda.update-function-configuration",
            "lambda.update-function-code",
            "lambda.publish-version",
            "lambda.update-alias",
        }
        for event in state["events"]
    )
    assert not any(
        event.get("op") == "lambda.remove-permission" and event.get("statement_id") == "apigw"
        for event in state["events"]
    )
