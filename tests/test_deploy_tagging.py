"""Guards the `project` cost-allocation tag applied by the deploy scripts.

98.4% of the AWS account's spend was landing in the untagged bucket, so a
per-project budget (`fare-demo`, see `infra/README.md`) could see nothing this
project actually costs. `project` is the cost-allocation tag key activated in
Cost Explorer; a resource created without it is invisible to every per-project
report and budget filter.

There is no CDK/Terraform layer here -- `infra/deploy.sh` is the whole
deployment (ADR 0004) -- so the tag is applied by the scripts themselves, and
this file is the regression test for that, in the same spirit as
`tests/test_deploy_rate_limit.py`. These tests call neither AWS nor the
network; they assert the scripts still declare the tagging correctly, so a
future edit cannot silently drop a resource back into the untagged bucket.

Two invariants matter beyond "the word project appears somewhere":

1. The tag VALUE is `fare-assistant`. It is deliberately not the repo name
   (`fare-policy-assistant`) and not the function name -- it is the portfolio
   project key the budget and the cross-repo cost report group on, so it must
   survive a rename of either.
2. Tagging is re-applied on every deploy, not only at resource-creation time.
   The billable resources already exist in the account, so create-time tags
   alone would never reach them.
"""

from __future__ import annotations

import re

from assistant import config

DEPLOY_SH = config.REPO_ROOT / "infra" / "deploy.sh"
DEPLOY_CONSOLE_SH = config.REPO_ROOT / "infra" / "deploy-console.sh"

PROJECT_TAG = "fare-assistant"


def _deploy_text() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8")


def _console_text() -> str:
    return DEPLOY_CONSOLE_SH.read_text(encoding="utf-8")


class TestProjectTagValue:
    def test_rider_deploy_declares_the_project_tag(self):
        assert re.search(
            rf"^PROJECT_TAG={re.escape(PROJECT_TAG)}\s*$", _deploy_text(), re.MULTILINE
        ), (
            "infra/deploy.sh must declare PROJECT_TAG=fare-assistant -- the value "
            "the fare-demo budget and the cross-repo cost report group on"
        )

    def test_console_deploy_declares_the_same_project_tag(self):
        assert re.search(
            rf"^PROJECT_TAG={re.escape(PROJECT_TAG)}\s*$", _console_text(), re.MULTILINE
        ), (
            "infra/deploy-console.sh must use the same project tag value as the "
            "rider deploy; the console is part of the same project's spend"
        )

    def test_both_tag_shorthands_derive_from_the_one_value(self):
        # The AWS CLI wants `key=value` for Lambda/Logs/API Gateway and
        # `Key=,Value=` for IAM/SNS/CloudWatch. Both forms must be built from
        # PROJECT_TAG so the two spellings cannot drift to different values.
        for text, script in ((_deploy_text(), "deploy.sh"), (_console_text(), "deploy-console.sh")):
            assert 'PROJECT_TAG_MAP="project=$PROJECT_TAG"' in text, script
            assert 'PROJECT_TAG_LIST="Key=project,Value=$PROJECT_TAG"' in text, script

    def test_no_literal_tag_value_restated_after_the_declaration(self):
        # A hardcoded second copy of the value is exactly how the two spellings
        # would drift; every other use must go through the variables.
        for text, script in ((_deploy_text(), "deploy.sh"), (_console_text(), "deploy-console.sh")):
            occurrences = [
                line
                for line in text.splitlines()
                if f"project={PROJECT_TAG}" in line
                and not line.strip().startswith("#")
                and "PROJECT_TAG" not in line
            ]
            assert not occurrences, (
                f"{script} restates the tag value literally instead of using "
                f"$PROJECT_TAG_MAP/$PROJECT_TAG_LIST: {occurrences}"
            )


class TestResourcesAreTaggedOnCreate:
    def test_rider_creates_are_tagged(self):
        text = _deploy_text()
        # Each of these is a billable resource (or the parent that billing rolls
        # up to) that this script creates from scratch on a fresh account.
        assert re.search(r"aws lambda create-function.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)
        assert re.search(r"aws iam create-role.*?--tags \"\$PROJECT_TAG_LIST\"", text, re.S)
        assert re.search(r"aws apigatewayv2 create-api.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)
        assert re.search(r"aws logs create-log-group.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)

    def test_console_creates_are_tagged(self):
        text = _console_text()
        assert re.search(r"aws lambda create-function.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)
        assert re.search(r"aws iam create-role.*?--tags \"\$PROJECT_TAG_LIST\"", text, re.S)
        assert re.search(r"aws apigatewayv2 create-api.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)
        assert re.search(r"aws logs create-log-group.*?--tags \"\$PROJECT_TAG_MAP\"", text, re.S)


class TestTaggingIsReappliedEveryDeploy:
    """Create-time tags never reach resources that already exist untagged."""

    def test_rider_deploy_retags_every_taggable_resource(self):
        text = _deploy_text()
        for call in (
            "aws lambda tag-resource",
            "aws iam tag-role",
            "aws logs tag-resource",
            "aws apigatewayv2 tag-resource",
            "aws sns tag-resource",
            "aws cloudwatch tag-resource",
        ):
            assert call in text, (
                f"infra/deploy.sh must re-apply the project tag with `{call}` on "
                "every deploy; the billable resources already exist untagged, so "
                "create-time tags alone would never reach them"
            )

    def test_console_deploy_retags_its_resources(self):
        text = _console_text()
        for call in (
            "aws lambda tag-resource",
            "aws iam tag-role",
            "aws logs tag-resource",
            "aws apigatewayv2 tag-resource",
        ):
            assert call in text, f"infra/deploy-console.sh must re-apply the tag with `{call}`"

    def test_every_alarm_is_tagged(self):
        # The alarms are created by the `_alarm` helper and a direct
        # put-metric-alarm; the retag loop must cover the whole set or some
        # CloudWatch spend stays unattributed.
        text = _deploy_text()
        created = set(re.findall(r"^_alarm ([a-z0-9-]+) ", text, re.MULTILINE))
        created.add("latency-p99")  # the extended-statistic alarm, written out longhand
        loop = re.search(r"for alarm_suffix in (.*?); do", text, re.S)
        assert loop, "expected a loop that retags each alarm by suffix"
        tagged = set(loop.group(1).split())
        tagged.discard("\\")
        assert created <= tagged, f"alarms created but never tagged: {sorted(created - tagged)}"

    def test_tagging_failure_does_not_fail_a_live_deploy(self):
        # The sweep runs after the alias has been promoted and smoke-verified.
        # A billing label must not tear down a working deploy -- but it must be
        # reported, because untagged spend is invisible spend.
        text = _deploy_text()
        assert 'UNTAGGED="$UNTAGGED${UNTAGGED:+, }$label"' in text
        assert re.search(r"WARNING: could not apply project=\$PROJECT_TAG to: \$UNTAGGED", text), (
            "a resource that could not be tagged must be reported loudly"
        )
