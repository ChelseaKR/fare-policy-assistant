"""The CI role's alarm-relay grant must match what the relay actually calls.

`.github/workflows/alarm-relay.yml` fails with AccessDenied until the CI role
carries the policy that `infra/grant-alarm-relay-read.sh` attaches. Nothing
applies that grant automatically, so the two files can drift apart in silence:
a new `aws` call in the workflow would fail in production, and a widened grant
would pass review unnoticed. These tests read both files and compare them.

They never call AWS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "alarm-relay.yml"
GRANT = ROOT / "infra" / "grant-alarm-relay-read.sh"

#: aws CLI subcommand -> the IAM action it needs. sts get-caller-identity is
#: deliberately absent: it needs no permission, so it must not be granted.
CLI_TO_ACTION = {
    ("cloudwatch", "describe-alarms"): "cloudwatch:DescribeAlarms",
    ("sns", "get-topic-attributes"): "sns:GetTopicAttributes",
}
NO_PERMISSION_NEEDED = {("sts", "get-caller-identity")}


def _workflow_calls() -> set[tuple[str, str]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(r"\baws\s+([a-z0-9-]+)\s+([a-z0-9-]+)", code))


def _rendered_policy() -> dict:
    text = GRANT.read_text(encoding="utf-8")
    body = text.split("<<JSON\n", 1)[1].split("\nJSON\n", 1)[0]
    body = body.replace("${TOPIC_ARN}", "arn:aws:sns:us-west-2:111122223333:TOPIC")
    return json.loads(body)


def _statements() -> list[dict]:
    return _rendered_policy()["Statement"]


def test_workflow_calls_are_exactly_the_known_set() -> None:
    # A new aws call fails here first, so the grant is reviewed with it.
    assert _workflow_calls() == set(CLI_TO_ACTION) | NO_PERMISSION_NEEDED


def test_grant_allows_exactly_the_actions_the_workflow_calls() -> None:
    needed = {CLI_TO_ACTION[c] for c in _workflow_calls() if c in CLI_TO_ACTION}
    granted = set()
    for s in _statements():
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        granted.update(actions)
    assert granted == needed


def test_grant_is_read_only_allow_with_no_wildcard_actions() -> None:
    for s in _statements():
        assert s["Effect"] == "Allow"
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        for a in actions:
            assert "*" not in a, a
            assert re.search(r":(Describe|Get)[A-Za-z]+$", a), a


def test_sns_read_is_pinned_to_the_one_alerts_topic() -> None:
    (sns,) = [s for s in _statements() if s["Action"] == "sns:GetTopicAttributes"]
    assert sns["Resource"].startswith("arn:aws:sns:us-west-2:")
    assert "*" not in sns["Resource"]
    # The workflow reads the same topic the grant is pinned to: <FN>-alerts.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "fare-policy-assistant-demo-alerts" in workflow
    assert 'FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"' in GRANT.read_text(
        encoding="utf-8"
    )
    assert 'TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT}:${FN}-alerts"' in GRANT.read_text(
        encoding="utf-8"
    )


def test_describe_alarms_is_the_only_wildcard_resource() -> None:
    wild = [s for s in _statements() if s["Resource"] == "*"]
    assert [s["Action"] for s in wild] == ["cloudwatch:DescribeAlarms"]


def test_grant_targets_the_ci_role_not_the_lambda_role() -> None:
    text = GRANT.read_text(encoding="utf-8")
    assert 'ROLE="${FPA_CI_ROLE_NAME:-fare-policy-assistant-ci}"' in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_grant_script_parses() -> None:
    subprocess.run(["bash", "-n", str(GRANT)], check=True)
