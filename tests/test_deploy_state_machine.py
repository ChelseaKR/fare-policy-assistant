"""Behavioral tests for the immutable Lambda deployment state machine."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from assistant import config
from assistant.corpus import corpus_version

DEPLOY = config.REPO_ROOT / "infra" / "deploy.sh"
ACCOUNT = "123456789012"
REGION = "us-west-2"
FUNCTION = "fare-policy-assistant-demo"
API_ID = "test-api"
# Derived from the tree rather than hardcoded: deploy.sh computes the pinned
# corpus identity from the working corpus at deploy time, so a legitimate
# corpus refresh (e.g. the scheduled freshness loop) must not break these
# simulations. Failure-path tests that need a *wrong* version construct their
# own mismatched strings.
CORPUS_VERSION = corpus_version()
HISTORY_KEY = "0" * 64
SOURCE_REVISION = "a" * 40
IDENTITY_ARTIFACT = "A" * 43 + "="
ALIAS_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}:live"
ALIAS_URI = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{ALIAS_ARN}/invocations"
IDENTITY_ENV_KEYS = {
    "FPA_SOURCE_REVISION",
    "FPA_CONFIG_VERSION",
    "FPA_PINNED_CONTENT_VERSION",
    "FPA_PINNED_SNAPSHOT_VERSION",
    "FPA_RELEASE_VERSION",
    "FPA_ARTIFACT_CODE_SHA256",
}


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


def shorthand_object(raw):
    return {
        key: value
        for item in raw.split(",")
        for key, value in [item.split("=", 1)]
    }


def metric_pattern_matches(pattern, event):
    expression = pattern.strip()
    if expression.startswith("{") and expression.endswith("}"):
        expression = expression[1:-1].strip()
    for raw_clause in expression.split("&&"):
        clause = raw_clause.strip()
        if " IS TRUE" in clause:
            field = clause.split(" IS TRUE", 1)[0].strip().removeprefix("$.")
            if event.get(field) is not True:
                return False
            continue
        if " IS FALSE" in clause:
            field = clause.split(" IS FALSE", 1)[0].strip().removeprefix("$.")
            if event.get(field) is not False:
                return False
            continue
        if " = " not in clause:
            return False
        field, expected = (part.strip() for part in clause.split(" = ", 1))
        field = field.removeprefix("$.")
        if expected == "*":
            if field not in event or event[field] is None:
                return False
            continue
        if expected.startswith('"') and expected.endswith('"'):
            expected = json.loads(expected)
        if event.get(field) != expected:
            return False
    return True


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


def version_identity(qualifier):
    function_config = state["configs"][qualifier]
    environment = function_config["Environment"]["Variables"]
    field_map = {
        "source_revision": "FPA_SOURCE_REVISION",
        "config_version": "FPA_CONFIG_VERSION",
        "content_version": "FPA_PINNED_CONTENT_VERSION",
        "snapshot_version": "FPA_PINNED_SNAPSHOT_VERSION",
        "release_version": "FPA_RELEASE_VERSION",
        "artifact_code_sha256": "FPA_ARTIFACT_CODE_SHA256",
    }
    present = {
        response_key: environment[environment_key]
        for response_key, environment_key in field_map.items()
        if environment_key in environment
    }
    if not present:
        return {}
    if len(present) != len(field_map):
        return {"identity_status": "invalid", **present}
    return {
        "identity_status": "verified",
        "function_version": qualifier,
        **present,
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
        identity = version_identity(qualifier)
        if (
            state.get("candidate_version_identity_mismatch")
            and identity.get("identity_status") == "verified"
        ):
            identity["release_version"] = "f" * 64
        if (
            state.get("candidate_version_partial_identity")
            and identity.get("identity_status") == "verified"
        ):
            identity.pop("snapshot_version")
            identity["identity_status"] = "invalid"
        body = json.dumps(
            {
                "corpus_version": state["corpus_version"],
                "matches_pin": True,
                "disabled_documents": disabled_documents,
                **identity,
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


def structured_log_tail(qualifier):
    request_id = f"fake-request-{qualifier}"
    events = [
        {
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
        },
        {
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
        },
    ]
    text = "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
    return base64.b64encode((text + "\n").encode()).decode()


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
    logging_config = value("--logging-config")
    if logging_config is not None:
        latest["LoggingConfig"] = shorthand_object(logging_config)
    latest["RevisionId"] = next_revision("latest")
    response = copy.deepcopy(latest)
    if state.get("config_revision_changes_on_wait"):
        state["settle_config_revision_pending"] = True
    record(
        "lambda.update-function-configuration",
        from_revision=before,
        to_revision=latest["RevisionId"],
        logging_config=copy.deepcopy(latest["LoggingConfig"]),
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
    if state.get("transient_code_response"):
        response.pop("Environment", None)
        state["settle_code_revision_pending"] = True
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
    response = invoke_payload(event, qualifier)
    output_path.write_text(json.dumps(response), encoding="utf-8")
    log_tail = value("--log-type") == "Tail"
    response_body = json.loads(response["body"]) if event["rawPath"] == "/version" else {}
    record(
        "lambda.invoke",
        qualifier=qualifier,
        path=event["rawPath"],
        health_marker=event.get("fare_assistant_health"),
        log_tail=log_tail,
        identity_status=response_body.get("identity_status"),
    )
    metadata = {"StatusCode": 200, "ExecutedVersion": qualifier}
    if log_tail:
        metadata["LogResult"] = structured_log_tail(qualifier)
    finish(metadata)

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
        description=alias["Description"],
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
    if (
        args[2] == "function-updated"
        and state.pop("settle_code_revision_pending", False)
    ):
        latest = state["configs"]["$LATEST"]
        before = latest["RevisionId"]
        latest["RevisionId"] = next_revision("latest-code-settled")
        record(
            "lambda.code-settled",
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
    if os.environ.get("FAKE_PROMOTION_BUNDLE_TAMPER"):
        pointer_path = pathlib.Path(
            os.environ["FPA_BUILD_DIR"]
        ) / "promotion-evidence-pointer.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        summary_path = pathlib.Path(pointer["bundle_path"]) / "summary.json"
        summary_path.chmod(0o644)
        summary_path.write_text('{"tampered":true}\n', encoding="utf-8")
        record("promotion.bundle-tamper")
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

if args[:2] == ["sns", "list-subscriptions-by-topic"]:
    record("sns.list-subscriptions-by-topic")
    finish({"Subscriptions": state.get("subscriptions", [])})

if args[:2] == ["logs", "put-metric-filter"]:
    filter_name = value("--filter-name")
    transformation = shorthand_object(value("--metric-transformations"))
    if "defaultValue" in transformation:
        transformation["defaultValue"] = float(transformation["defaultValue"])
    metric_filter = {
        "filterName": filter_name,
        "filterPattern": value("--filter-pattern"),
        "metricTransformations": [transformation],
        "logGroupName": value("--log-group-name"),
    }
    state.setdefault("metric_filters", {})[filter_name] = metric_filter
    record(
        "logs.put-metric-filter",
        filter_name=filter_name,
        filter_pattern=metric_filter["filterPattern"],
    )
    finish({})

if args[:2] == ["logs", "describe-metric-filters"]:
    prefix = value("--filter-name-prefix", "")
    metric_filters = [
        metric_filter
        for filter_name, metric_filter in state.get("metric_filters", {}).items()
        if filter_name.startswith(prefix)
    ]
    record("logs.describe-metric-filters", filter_name_prefix=prefix)
    finish({"metricFilters": metric_filters})

if args[:2] == ["logs", "test-metric-filter"]:
    pattern = value("--filter-pattern")
    messages = json.loads(value("--log-event-messages"))
    matches = []
    for index, message in enumerate(messages, start=1):
        try:
            event = message if isinstance(message, dict) else json.loads(message)
        except (TypeError, json.JSONDecodeError):
            continue
        if metric_pattern_matches(pattern, event):
            matches.append(
                {
                    "eventNumber": index,
                    "eventMessage": message,
                    "extractedValues": {},
                }
            )
    record(
        "logs.test-metric-filter",
        filter_pattern=pattern,
        match_count=len(matches),
    )
    finish({"matches": matches})

allowed_noop_operations = {
    ("logs", "create-log-group"),
    ("logs", "put-retention-policy"),
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


def version_identity(state, version):
    function_config = state["configs"][version]
    environment = function_config["Environment"]["Variables"]
    field_map = {
        "source_revision": "FPA_SOURCE_REVISION",
        "config_version": "FPA_CONFIG_VERSION",
        "content_version": "FPA_PINNED_CONTENT_VERSION",
        "snapshot_version": "FPA_PINNED_SNAPSHOT_VERSION",
        "release_version": "FPA_RELEASE_VERSION",
        "artifact_code_sha256": "FPA_ARTIFACT_CODE_SHA256",
    }
    present = {
        response_key: environment[environment_key]
        for response_key, environment_key in field_map.items()
        if environment_key in environment
    }
    if not present:
        return {}
    if len(present) != len(field_map):
        return {"identity_status": "invalid", **present}
    return {
        "identity_status": "verified",
        "function_version": version,
        **present,
    }


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
public_event = {"op": "public.request", "live_version": live_version, "path": path}
if path == "/version":
    public_event["identity_status"] = version_identity(state, live_version).get(
        "identity_status"
    )
state["events"].append(public_event)
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
    identity = version_identity(state, live_version)
    if (
        state.get("public_version_identity_mismatch")
        and identity.get("identity_status") == "verified"
    ):
        identity["release_version"] = "f" * 64
    body = json.dumps(
        {
            "corpus_version": state["corpus_version"],
            "as_of": "2026-07-29",
            "agencies": ["MST"],
            "matches_pin": True,
            "disabled_documents": disabled_documents,
            **identity,
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
import hashlib
import json
import os
import pathlib
import sys

args = sys.argv[1:]


def record(operation):
    state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["events"].append({"op": operation})
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def event_count(operation):
    state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return sum(event.get("op") == operation for event in state["events"])


if args[:2] == ["pip", "install"]:
    target = pathlib.Path(args[args.index("--target") + 1])
    target.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0)
if args[:4] == ["run", "python", "-m", "evals.runner"] and "--promotion" in args:
    record("promotion.eval")
    if os.environ.get("FAKE_PROMOTION_EVAL_FAIL"):
        print("fake promotion evaluation failed", file=sys.stderr)
        raise SystemExit(1)
    runs_root = pathlib.Path(os.environ["FPA_PROMOTION_RUNS_ROOT"])
    runs_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(runs_root.iterdir())
    suffix = "" if not existing else f"-{len(existing):02d}"
    run_dir = runs_root / f"20260730T120000Z{suffix}"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "results.jsonl").write_text("{}\n", encoding="utf-8")
    summary_sha256 = hashlib.sha256((run_dir / "summary.json").read_bytes()).hexdigest()
    results_sha256 = hashlib.sha256((run_dir / "results.jsonl").read_bytes()).hexdigest()
    manifest = {
        "results_sha256": results_sha256,
        "schema": "fare-assistant.eval-run-bundle.v1",
        "summary_sha256": summary_sha256,
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    content_address = hashlib.sha256(manifest_bytes).hexdigest()
    bundle = run_dir / "bundles" / content_address
    bundle.mkdir(parents=True)
    (bundle / "summary.json").write_bytes((run_dir / "summary.json").read_bytes())
    (bundle / "results.jsonl").write_bytes((run_dir / "results.jsonl").read_bytes())
    (bundle / "bundle.json").write_bytes(manifest_bytes)
    pointer = pathlib.Path(args[args.index("--run-path-output") + 1])
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer_payload = {
        "bundle_path": str(bundle),
        "content_address": content_address,
        "results_sha256": results_sha256,
        "run_dir": str(run_dir),
        "schema": "fare-assistant.eval-run-bundle-pointer.v1",
        "summary_sha256": summary_sha256,
    }
    pointer.write_text(
        json.dumps(pointer_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
if (
    args[:2] == ["run", "python"]
    and len(args) > 2
    and args[2].endswith("scripts/build_promotion_attestation.py")
):
    record("promotion.attestation")
    if os.environ.get("FAKE_PROMOTION_ATTESTATION_FAIL"):
        print("fake promotion attestation failed", file=sys.stderr)
        raise SystemExit(1)
    output = pathlib.Path(args[args.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("{}\n", encoding="utf-8")
    attestation_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "attestation_sha256": attestation_sha256,
                "output_path": str(output),
            }
        )
    )
    raise SystemExit(0)
if (
    args[:3] == ["run", "python", "-c"]
    and "verify_promotion_evidence" in args[3]
):
    prior_verifications = event_count("promotion.verify")
    record("promotion.verify")
    if os.environ.get("FAKE_PROMOTION_VERIFY_FAIL"):
        print("fake promotion evidence verification failed", file=sys.stderr)
        raise SystemExit(1)
    if (
        os.environ.get("FAKE_PROMOTION_FINAL_VERIFY_FAIL")
        and prior_verifications >= 1
    ):
        print("fake final promotion evidence verification failed", file=sys.stderr)
        raise SystemExit(1)
    evidence = pathlib.Path(os.environ["FPA_DEPLOY_PROMOTION_DIR"])
    digest = lambda name: hashlib.sha256((evidence / name).read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "verified",
                "summary_sha256": digest("summary.json"),
                "results_sha256": digest("results.jsonl"),
                "promotion_sha256": digest("promotion.json"),
                "runtime_release": {
                    "source_revision": os.environ["FPA_DEPLOY_EXPECTED_SOURCE"],
                    "config_version": os.environ["FPA_DEPLOY_EXPECTED_CONFIG"],
                    "content_version": os.environ["FPA_DEPLOY_EXPECTED_CONTENT"],
                    "snapshot_version": os.environ["FPA_DEPLOY_EXPECTED_SNAPSHOT"],
                    "release_version": os.environ["FPA_DEPLOY_EXPECTED_RELEASE"],
                    "corpus_version": os.environ["FPA_DEPLOY_EXPECTED_CORPUS"],
                    "artifact_code_sha256": os.environ[
                        "FPA_DEPLOY_EXPECTED_ARTIFACT"
                    ],
                    "function_version": os.environ[
                        "FPA_DEPLOY_EXPECTED_FUNCTION_VERSION"
                    ],
                },
            }
        )
    )
    raise SystemExit(0)
if args[:2] == ["run", "python"]:
    python = os.environ["FAKE_REAL_PYTHON"]
    os.execv(python, [python, *args[2:]])
print("unsupported fake uv call: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""".lstrip()


FAKE_INSTALL = r"""
#!/usr/bin/env python3
import json
import os
import pathlib
import shutil
import sys

args = sys.argv[1:]
mode = int(args[args.index("-m") + 1], 8)
source = pathlib.Path(args[-2])
destination = pathlib.Path(args[-1])
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source, destination)
destination.chmod(mode)

if (
    os.environ.get("FAKE_EVAL_BUNDLE_SWAP_BETWEEN_COPIES")
    and source.name == "summary.json"
    and source.parent.parent.name == "bundles"
):
    results = source.parent / "results.jsonl"
    results.chmod(0o644)
    results.write_text('{"tampered":true}\n', encoding="utf-8")
    state_path = pathlib.Path(os.environ["FAKE_AWS_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["events"].append({"op": "promotion.eval-bundle-swap"})
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
""".lstrip()


FAKE_GIT = r"""
#!/usr/bin/env python3
import hashlib
import os
import pathlib
import stat
import sys

args = sys.argv[1:]
repo_root = None
if args[:1] == ["-C"] and len(args) >= 2:
    repo_root = pathlib.Path(args[1]).resolve()
    args = args[2:]

if args == ["rev-parse", "HEAD"]:
    print(os.environ["FAKE_GIT_SOURCE_REVISION"])
    raise SystemExit(0)
if args == ["rev-parse", "--show-toplevel"] and repo_root is not None:
    print(repo_root)
    raise SystemExit(0)
if args in (
    ["status", "--porcelain", "--untracked-files=normal"],
    ["status", "--porcelain=v1", "--untracked-files=normal", "-z"],
):
    status = os.environ.get("FAKE_GIT_STATUS", "")
    if status:
        terminator = "\0" if "-z" in args else "\n"
        sys.stdout.buffer.write(status.encode() + terminator.encode())
    raise SystemExit(0)

def fixture_entries():
    if repo_root is None:
        return []
    selected = []
    for tree in ("src/assistant", "prompts"):
        for path in (repo_root / tree).rglob("*"):
            relative = path.relative_to(repo_root)
            if (
                path.is_file()
                and "__pycache__" not in relative.parts
                and path.suffix != ".pyc"
            ):
                selected.append(relative)
    for name in (
        "corpus/processed/chunks.jsonl",
        "docs/answer-contract.schema.json",
        "web/__init__.py",
        "web/handler.py",
        "web/index.html",
        "web/offline.py",
        "web/guide.py",
        "web/embed.py",
        "web/csp.py",
    ):
        selected.append(pathlib.Path(name))

    entries = []
    for relative in sorted(set(selected), key=lambda path: path.as_posix()):
        source = repo_root / relative
        payload = source.read_bytes()
        header = f"blob {len(payload)}\0".encode()
        object_id = hashlib.sha1(header + payload).hexdigest()
        mode = "100755" if source.stat().st_mode & stat.S_IXUSR else "100644"
        entries.append((mode, object_id, relative.as_posix(), payload))
    return entries

if args == ["ls-files", "--stage", "-z"]:
    for mode, object_id, path, _payload in fixture_entries():
        sys.stdout.buffer.write(f"{mode} {object_id} 0\t{path}\0".encode())
    raise SystemExit(0)
if args[:2] == ["cat-file", "blob"] and len(args) == 3:
    requested = args[2]
    for _mode, object_id, _path, payload in fixture_entries():
        if object_id == requested:
            sys.stdout.buffer.write(payload)
            raise SystemExit(0)
    print(f"unknown fake blob: {requested}", file=sys.stderr)
    raise SystemExit(1)

print("unsupported fake git call: " + " ".join(args), file=sys.stderr)
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
    transient_code_response: bool = False,
    partial_retry_unknown_drift: bool = False,
    candidate_version_identity_mismatch: bool = False,
    candidate_version_partial_identity: bool = False,
    public_version_identity_mismatch: bool = False,
    rollback_already_old_live: bool = False,
    identity_route_repair: str = "",
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
        "transient_code_response": transient_code_response,
        "candidate_version_identity_mismatch": candidate_version_identity_mismatch,
        "candidate_version_partial_identity": candidate_version_partial_identity,
        "public_version_identity_mismatch": public_version_identity_mismatch,
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
    if identity_route_repair:
        if identity_route_repair not in {"verified", "partial", "mismatch"}:
            raise ValueError(f"unsupported identity route fixture: {identity_route_repair}")
        identity_variables = {
            "FPA_SOURCE_REVISION": SOURCE_REVISION,
            "FPA_CONFIG_VERSION": "1" * 64,
            "FPA_PINNED_CONTENT_VERSION": "2" * 64,
            "FPA_PINNED_SNAPSHOT_VERSION": "3" * 64,
            "FPA_RELEASE_VERSION": "4" * 64,
            "FPA_ARTIFACT_CODE_SHA256": IDENTITY_ARTIFACT,
        }
        if identity_route_repair == "partial":
            identity_variables.pop("FPA_PINNED_SNAPSHOT_VERSION")
        elif identity_route_repair == "mismatch":
            identity_variables["FPA_ARTIFACT_CODE_SHA256"] = "B" * 43 + "="
        for version in ("$LATEST", "5"):
            state["configs"][version]["CodeSha256"] = IDENTITY_ARTIFACT
            state["configs"][version]["Environment"]["Variables"].update(identity_variables)
        state["integration"]["IntegrationUri"] = (
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
        )
    if rollback_already_old_live:
        state["aliases"]["rollback"]["FunctionVersion"] = "5"
        state["aliases"]["rollback"]["Description"] = "operator-retained rollback marker"
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
    transient_code_response: bool = False,
    partial_retry_unknown_drift: bool = False,
    candidate_version_identity_mismatch: bool = False,
    candidate_version_partial_identity: bool = False,
    public_version_identity_mismatch: bool = False,
    rollback_already_old_live: bool = False,
    identity_route_repair: str = "",
    deploy_runs: int = 1,
    git_status: str = "",
    promotion_eval_fail: bool = False,
    promotion_attestation_fail: bool = False,
    promotion_verify_fail: bool = False,
    promotion_final_verify_fail: bool = False,
    promotion_bundle_tamper: bool = False,
    eval_bundle_swap_between_copies: bool = False,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "aws", FAKE_AWS)
    _write_executable(bin_dir / "curl", FAKE_CURL)
    _write_executable(bin_dir / "git", FAKE_GIT)
    _write_executable(bin_dir / "install", FAKE_INSTALL)
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
                transient_code_response=transient_code_response,
                partial_retry_unknown_drift=partial_retry_unknown_drift,
                candidate_version_identity_mismatch=candidate_version_identity_mismatch,
                candidate_version_partial_identity=candidate_version_partial_identity,
                public_version_identity_mismatch=public_version_identity_mismatch,
                rollback_already_old_live=rollback_already_old_live,
                identity_route_repair=identity_route_repair,
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
        "FAKE_GIT_SOURCE_REVISION": SOURCE_REVISION,
        "FAKE_GIT_STATUS": git_status,
        "FAKE_PROMOTION_EVAL_FAIL": "1" if promotion_eval_fail else "",
        "FAKE_PROMOTION_ATTESTATION_FAIL": ("1" if promotion_attestation_fail else ""),
        "FAKE_PROMOTION_VERIFY_FAIL": "1" if promotion_verify_fail else "",
        "FAKE_PROMOTION_FINAL_VERIFY_FAIL": ("1" if promotion_final_verify_fail else ""),
        "FAKE_PROMOTION_BUNDLE_TAMPER": "1" if promotion_bundle_tamper else "",
        "FAKE_EVAL_BUNDLE_SWAP_BETWEEN_COPIES": ("1" if eval_bundle_swap_between_copies else ""),
        "FPA_API_ID": API_ID,
        "FPA_BUILD_DIR": str(tmp_path / "rider-build"),
        "FPA_PINNED_CORPUS_VERSION": CORPUS_VERSION,
        "FPA_DISABLED_DOC_IDS": disabled_docs,
        "FPA_HISTORY_HMAC_KEY": HISTORY_KEY,
        "FPA_LEGACY_IDENTITY_ROLLBACK_VERSION": "5",
        "FPA_SMOKE_CONNECT_TIMEOUT": "1",
        "FPA_SMOKE_MAX_TIME": "1",
        "FPA_PROMOTION_RUNS_ROOT": str(tmp_path / "promotion-runs"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TMPDIR": str(task_tmpdir),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }
    result: subprocess.CompletedProcess[str] | None = None
    for run_number in range(1, deploy_runs + 1):
        current_state = json.loads(state_path.read_text(encoding="utf-8"))
        current_state["events"].append({"op": "deploy.start", "run": run_number})
        state_path.write_text(json.dumps(current_state), encoding="utf-8")
        result = subprocess.run(
            [str(DEPLOY)],
            cwd=config.REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode != 0:
            break
    assert result is not None
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


def _assert_verified_release_config(function_config: dict[str, Any]) -> None:
    variables = function_config["Environment"]["Variables"]
    assert IDENTITY_ENV_KEYS <= variables.keys()
    assert variables["FPA_SOURCE_REVISION"] == SOURCE_REVISION
    for name in (
        "FPA_CONFIG_VERSION",
        "FPA_PINNED_CONTENT_VERSION",
        "FPA_PINNED_SNAPSHOT_VERSION",
        "FPA_RELEASE_VERSION",
    ):
        assert len(variables[name]) == 64
        assert set(variables[name]) <= set("0123456789abcdef")
    assert variables["FPA_ARTIFACT_CODE_SHA256"] == function_config["CodeSha256"]
    assert len(variables["FPA_ARTIFACT_CODE_SHA256"]) == 44


def test_dirty_source_checkout_aborts_before_any_aws_operation(tmp_path: Path) -> None:
    result, state = _run_deploy(tmp_path, git_status=" M reviewed-source.py")

    assert result.returncode == 2
    assert "working tree is dirty; refusing a false source/release identity" in result.stderr
    assert state["events"] == [{"op": "deploy.start", "run": 1}]
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"


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
    _assert_verified_release_config(state["configs"]["6"])
    assert not (IDENTITY_ENV_KEYS & state["configs"]["5"]["Environment"]["Variables"].keys())
    assert state["configs"]["6"]["LoggingConfig"] == {
        "LogFormat": "JSON",
        "ApplicationLogLevel": "INFO",
        "SystemLogLevel": "WARN",
        "LogGroup": f"/aws/lambda/{FUNCTION}",
    }
    assert state["configs"]["5"]["LoggingConfig"]["LogFormat"] == "Text"
    assert "live Lambda version: 6" in result.stdout
    assert "retained rollback version: 5" in result.stdout
    assert "has no confirmed subscriber" in result.stderr

    events = state["events"]
    update_config = _event_index(events, "lambda.update-function-configuration")
    update_code = _event_index(events, "lambda.update-function-code")
    publish = _event_index(events, "lambda.publish-version", version="6")
    promotion_eval = _event_index(events, "promotion.eval")
    promotion_attestation = _event_index(events, "promotion.attestation")
    promotion_verifications = [
        index for index, event in enumerate(events) if event.get("op") == "promotion.verify"
    ]
    concurrency = _event_index(events, "lambda.put-function-concurrency")
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
    subscription_check = _event_index(events, "sns.list-subscriptions-by-topic")
    structured_filter_checks = [
        event for event in events if event.get("op") == "logs.test-metric-filter"
    ]
    tailed_candidate = [
        event
        for event in events
        if event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "6"
        and event.get("log_tail")
    ]

    assert len(direct_candidate) == 5
    assert tailed_candidate == [
        {
            "op": "lambda.invoke",
            "qualifier": "6",
            "path": "/api/ask",
            "health_marker": "release-v1",
            "log_tail": True,
            "identity_status": None,
        }
    ]
    version_health = [
        event
        for event in events
        if event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "6"
        and event.get("path") == "/version"
    ]
    assert version_health == [
        {
            "op": "lambda.invoke",
            "qualifier": "6",
            "path": "/version",
            "health_marker": None,
            "log_tail": False,
            "identity_status": "verified",
        }
    ]
    public_version = [
        event
        for event in events
        if event.get("op") == "public.request"
        and event.get("live_version") == "6"
        and event.get("path") == "/version"
    ]
    assert public_version == [
        {
            "op": "public.request",
            "live_version": "6",
            "path": "/version",
            "identity_status": "verified",
        }
    ]
    assert structured_filter_checks
    assert len(promotion_verifications) == 2
    assert [event["match_count"] for event in structured_filter_checks] == [
        1,
        1,
        0,
        1,
        0,
        1,
        1,
    ]
    assert {
        f"{FUNCTION}-genai-calls",
        f"{FUNCTION}-estimated-model-cost",
        f"{FUNCTION}-unpriced-model-calls",
        f"{FUNCTION}-model-duration",
        f"{FUNCTION}-answer-duration",
        f"{FUNCTION}-feedback-down-v2",
    }.issubset(state["metric_filters"])
    assert (
        "defaultValue"
        not in state["metric_filters"][f"{FUNCTION}-model-duration"]["metricTransformations"][0]
    )
    assert (
        "defaultValue"
        not in state["metric_filters"][f"{FUNCTION}-answer-duration"]["metricTransformations"][0]
    )
    assert (
        update_config
        < update_code
        < publish
        < min(direct_candidate)
        <= max(direct_candidate)
        < promotion_eval
        < promotion_attestation
        < promotion_verifications[0]
        < concurrency
        < promotion_verifications[1]
        < rollback_pointer
        < live_promotion
        < candidate_public_smoke
        < subscription_check
        < dashboard
    )
    runtime_evidence = json.loads(
        (tmp_path / "rider-build" / "promotion-runtime.json").read_text(encoding="utf-8")
    )
    assert set(runtime_evidence) == {
        "source_revision",
        "config_version",
        "content_version",
        "snapshot_version",
        "release_version",
        "corpus_version",
        "artifact_code_sha256",
        "function_version",
    }
    assert HISTORY_KEY not in json.dumps(runtime_evidence)

    pointer_path = tmp_path / "rider-build" / "promotion-evidence-pointer.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["schema"] == "fare-assistant.eval-bundle-pointer.v1"
    assert pointer["function_version"] == "6"
    assert pointer["content_address"] == pointer["promotion_sha256"]
    bundle = Path(pointer["bundle_path"])
    assert bundle == (tmp_path / "rider-build" / "promotions" / "6" / pointer["content_address"])
    assert set(path.name for path in bundle.iterdir()) == {
        "summary.json",
        "results.jsonl",
        "promotion.json",
    }
    for filename, digest_field in (
        ("summary.json", "summary_sha256"),
        ("results.jsonl", "results_sha256"),
        ("promotion.json", "promotion_sha256"),
    ):
        assert hashlib.sha256((bundle / filename).read_bytes()).hexdigest() == pointer[digest_field]


def test_promotion_eval_failure_leaves_both_aliases_untouched(tmp_path: Path) -> None:
    result, state = _run_deploy(tmp_path, promotion_eval_fail=True)

    assert result.returncode != 0
    assert "fake promotion evaluation failed" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(event["op"] == "promotion.eval" for event in state["events"])
    assert not any(
        event["op"] in {"promotion.attestation", "lambda.put-function-concurrency"}
        for event in state["events"]
    )
    assert not any(
        event["op"] in {"lambda.update-alias", "lambda.create-alias"} for event in state["events"]
    )


def test_promotion_attestation_failure_leaves_both_aliases_untouched(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, promotion_attestation_fail=True)

    assert result.returncode != 0
    assert "fake promotion attestation failed" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(event["op"] == "promotion.eval" for event in state["events"])
    assert any(event["op"] == "promotion.attestation" for event in state["events"])
    assert not any(
        event["op"]
        in {
            "lambda.put-function-concurrency",
            "lambda.update-alias",
            "lambda.create-alias",
        }
        for event in state["events"]
    )


def test_full_staging_verifier_failure_leaves_both_aliases_untouched(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, promotion_verify_fail=True)

    assert result.returncode != 0
    assert "fake promotion evidence verification failed" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert [event["op"] for event in state["events"]].count("promotion.verify") == 1
    assert not any(
        event["op"]
        in {
            "lambda.put-function-concurrency",
            "lambda.update-alias",
            "lambda.create-alias",
        }
        for event in state["events"]
    )


def test_final_pointer_reverification_failure_leaves_aliases_untouched(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, promotion_final_verify_fail=True)

    assert result.returncode != 0
    assert "fake final promotion evidence verification failed" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert [event["op"] for event in state["events"]].count("promotion.verify") == 2
    assert any(event["op"] == "lambda.put-function-concurrency" for event in state["events"])
    assert not any(
        event["op"] in {"lambda.update-alias", "lambda.create-alias"} for event in state["events"]
    )


def test_content_addressed_bundle_tamper_is_caught_before_aliases(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, promotion_bundle_tamper=True)

    assert result.returncode != 0
    assert "promotion evidence bundle digest does not match its pointer" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(event["op"] == "promotion.bundle-tamper" for event in state["events"])
    assert [event["op"] for event in state["events"]].count("promotion.verify") == 1
    assert not any(
        event["op"] in {"lambda.update-alias", "lambda.create-alias"} for event in state["events"]
    )


def test_eval_bundle_swap_between_staging_copies_fails_closed(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, eval_bundle_swap_between_copies=True)

    assert result.returncode != 0
    assert "staged promotion evidence differs from the atomic eval-bundle pointer" in (
        result.stderr
    )
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(event["op"] == "promotion.eval-bundle-swap" for event in state["events"])
    assert not any(
        event["op"]
        in {
            "promotion.attestation",
            "promotion.verify",
            "lambda.put-function-concurrency",
            "lambda.update-alias",
            "lambda.create-alias",
        }
        for event in state["events"]
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
    legacy_version_health = [
        event
        for event in state["events"]
        if event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "5"
        and event.get("path") == "/version"
    ]
    assert legacy_version_health
    assert all(event["identity_status"] is None for event in legacy_version_health)
    assert permission < route_cutover < candidate_publish


def test_identity_bearing_live_repairs_unqualified_route_with_strict_health(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, identity_route_repair="verified")

    assert result.returncode == 0, result.stderr
    assert state["integration"]["IntegrationUri"] == ALIAS_URI
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    strict_version_health = _event_index(
        state["events"],
        "lambda.invoke",
        qualifier="5",
        path="/version",
    )
    route_cutover = _event_index(
        state["events"],
        "apigatewayv2.update-integration",
        to_uri=ALIAS_URI,
    )
    candidate_staging = _event_index(
        state["events"],
        "lambda.update-function-configuration",
    )
    assert state["events"][strict_version_health]["identity_status"] == "verified"
    assert strict_version_health < route_cutover < candidate_staging
    assert "version health: ok: qualified release identity" in result.stdout


def test_identity_bearing_bootstrap_uses_strict_health_before_alias_route(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(
        tmp_path,
        bootstrap_mode=True,
        identity_route_repair="verified",
    )

    assert result.returncode == 0, result.stderr
    assert state["integration"]["IntegrationUri"] == ALIAS_URI
    strict_version_health = [
        event
        for event in state["events"]
        if event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "5"
        and event.get("path") == "/version"
    ]
    assert strict_version_health
    assert {event["identity_status"] for event in strict_version_health} == {"verified"}
    assert "version health: ok: qualified release identity" in result.stdout
    assert "version health: ok: explicit legacy release identity" not in result.stdout


def test_partial_identity_route_repair_fails_before_integration_change(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, identity_route_repair="partial")

    assert result.returncode != 0
    assert (
        "qualified release 5 has a partial identity tuple; refusing direct health" in result.stderr
    )
    assert state["integration"]["IntegrationUri"] == (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
    )
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert not any(
        event.get("op")
        in {
            "apigatewayv2.update-integration",
            "lambda.update-function-configuration",
            "lambda.update-function-code",
            "lambda.publish-version",
            "lambda.update-alias",
        }
        for event in state["events"]
    )


def test_artifact_mismatch_route_repair_fails_before_direct_health(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, identity_route_repair="mismatch")

    assert result.returncode != 0
    assert "qualified release 5 and identity artifact disagree" in result.stderr
    assert state["integration"]["IntegrationUri"] == (
        f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}"
    )
    assert not any(
        event.get("op")
        in {
            "lambda.invoke",
            "apigatewayv2.update-integration",
            "lambda.update-function-configuration",
            "lambda.update-function-code",
            "lambda.publish-version",
            "lambda.update-alias",
        }
        for event in state["events"]
    )


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


def test_code_staging_validates_settled_candidate_not_transient_response(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, transient_code_response=True)

    assert result.returncode == 0, result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    _assert_verified_release_config(state["configs"]["6"])
    for name in (
        "FPA_PINNED_CORPUS_VERSION",
        "FPA_DISABLED_DOC_IDS",
        "FPA_HISTORY_HMAC_KEY",
    ):
        assert (
            state["configs"]["6"]["Environment"]["Variables"][name]
            == state["configs"]["5"]["Environment"]["Variables"][name]
        )
    publish = _event_index(
        state["events"],
        "lambda.publish-version",
        version="6",
    )
    code = _event_index(state["events"], "lambda.update-function-code")
    settled = _event_index(state["events"], "lambda.code-settled")
    assert code < settled < publish


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


def test_public_failure_restores_rollback_description_when_target_was_unchanged(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(
        tmp_path,
        fail_public_version="6",
        rollback_already_old_live=True,
    )

    assert result.returncode != 0
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["live"]["Description"] == "reviewed live release 5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["Description"] == "operator-retained rollback marker"
    rollback_updates = [
        event
        for event in state["events"]
        if event.get("op") == "lambda.update-alias" and event.get("alias") == "rollback"
    ]
    assert [event["description"] for event in rollback_updates] == [
        "reviewed live release 5",
        "operator-retained rollback marker",
    ]


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


def test_identical_retry_reuses_exact_numeric_release_without_alias_moves(
    tmp_path: Path,
) -> None:
    result, state = _run_deploy(tmp_path, deploy_runs=2)

    assert result.returncode == 0, result.stderr
    assert "reusing exact published candidate version 6" in result.stdout
    assert "candidate is already the live immutable version 6" in result.stdout
    assert state["aliases"]["live"]["FunctionVersion"] == "6"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "5"
    _assert_verified_release_config(state["configs"]["6"])
    assert [event for event in state["events"] if event.get("op") == "lambda.publish-version"] == [
        {"op": "lambda.publish-version", "version": "6"}
    ]

    second_start = _event_index(state["events"], "deploy.start", run=2)
    assert not any(
        event.get("op") in {"lambda.update-alias", "lambda.create-alias"}
        for event in state["events"][second_start + 1 :]
    )
    assert [
        event.get("identity_status")
        for event in state["events"][second_start + 1 :]
        if event.get("op") == "lambda.invoke" and event.get("path") == "/version"
    ] == ["verified"]
    public_identity_statuses = [
        event.get("identity_status")
        for event in state["events"][second_start + 1 :]
        if event.get("op") == "public.request" and event.get("path") == "/version"
    ]
    assert public_identity_statuses
    assert set(public_identity_statuses) == {"verified"}


def test_mismatched_candidate_identity_aborts_before_alias_moves(tmp_path: Path) -> None:
    result, state = _run_deploy(
        tmp_path,
        candidate_version_identity_mismatch=True,
    )

    assert result.returncode != 0
    assert "/version did not match the verified numeric release identity" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(
        event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "6"
        and event.get("path") == "/version"
        and event.get("identity_status") == "verified"
        for event in state["events"]
    )
    assert not any(
        event.get("op") in {"lambda.update-alias", "lambda.create-alias"}
        for event in state["events"]
    )


def test_partial_candidate_identity_aborts_before_alias_moves(tmp_path: Path) -> None:
    result, state = _run_deploy(
        tmp_path,
        candidate_version_partial_identity=True,
    )

    assert result.returncode != 0
    assert "/version did not match the verified numeric release identity" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    assert any(
        event.get("op") == "lambda.invoke"
        and event.get("qualifier") == "6"
        and event.get("path") == "/version"
        and event.get("identity_status") == "invalid"
        for event in state["events"]
    )
    assert not any(
        event.get("op") in {"lambda.update-alias", "lambda.create-alias"}
        for event in state["events"]
    )


def test_public_identity_mismatch_restores_both_alias_pointers(tmp_path: Path) -> None:
    result, state = _run_deploy(
        tmp_path,
        public_version_identity_mismatch=True,
    )

    assert result.returncode != 0
    assert "assistant /version returned an invalid verified release identity" in result.stderr
    assert "candidate 6 failed public smoke" in result.stderr
    assert state["aliases"]["live"]["FunctionVersion"] == "5"
    assert state["aliases"]["rollback"]["FunctionVersion"] == "4"
    events = state["events"]
    promote_live = _event_index(
        events,
        "lambda.update-alias",
        alias="live",
        to_version="6",
    )
    public_version = _event_index(
        events,
        "public.request",
        live_version="6",
        path="/version",
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
    assert promote_live < public_version < restore_live < restore_rollback
