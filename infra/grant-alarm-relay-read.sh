#!/usr/bin/env bash
# Let CI read the demo stack's alarm state, so an alarm can reach a person.
#
#   ./infra/grant-alarm-relay-read.sh          # attach the read-only policy
#   ./infra/grant-alarm-relay-read.sh --check  # report whether it is attached
#
# Why this exists
#
# infra/deploy.sh creates the SNS topic "$FN-alerts", points six alarms at it,
# and when the topic has no confirmed subscriber prints:
#
#   WARNING: ... alarms are configured but cannot page an operator
#
# Measured against the live account on 2026-09-12, that topic has zero
# subscriptions, confirmed or pending, and has had since it was created. The
# warning has been correct and unread the whole time. infra/deploy-cutoff.sh
# makes it sharper: the spend breaker's "page a human" leg publishes to the same
# topic, so the half of the cost control that is supposed to tell somebody has
# never worked.
#
# .github/workflows/alarm-relay.yml is the channel that needs no address: it
# reads alarm state directly and reports into a GitHub issue. To do that it has
# to be able to read, and the CI role today can invoke two Bedrock models and
# nothing else. This attaches exactly the two read actions it needs.
#
# What it grants, and what it does not
#
#   cloudwatch:DescribeAlarms   Resource "*" -- this action has no
#                               resource-level permissions in IAM. That is an
#                               AWS limitation on a read, not a widened grant.
#   sns:GetTopicAttributes      pinned to the one alerts topic.
#
# No publish, no subscribe, no write of any kind, nothing outside those two
# actions. The role's trust policy is untouched: it already accepts this
# repository through the account's GitHub OIDC provider, which is what CI uses
# for Bedrock today.
#
# This is deliberately a separate script rather than a step in deploy.sh. It
# touches IAM, it needs to run once, and a deploy that silently edits a role's
# permissions is not a deploy anybody can review by reading its output.
set -euo pipefail

REGION="${FPA_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
ROLE="${FPA_CI_ROLE_NAME:-fare-policy-assistant-ci}"
POLICY_NAME="alarm-relay-read"

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=true
fi

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT}:${FN}-alerts"

POLICY_DOCUMENT="$(
  cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadAlarmState",
      "Effect": "Allow",
      "Action": "cloudwatch:DescribeAlarms",
      "Resource": "*"
    },
    {
      "Sid": "ReadAlertsTopicSubscriberCount",
      "Effect": "Allow",
      "Action": "sns:GetTopicAttributes",
      "Resource": "${TOPIC_ARN}"
    }
  ]
}
JSON
)"

if [[ "$CHECK_ONLY" == true ]]; then
  # Reports, and says which of the two states it found. "Not attached" and
  # "could not look" are different answers and must not share an exit code:
  # this script exists because a warning nobody reads is indistinguishable from
  # a working channel, and repeating that shape here would be absurd.
  if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    echo "could not read IAM role $ROLE: this is not evidence either way" >&2
    exit 2
  fi
  if aws iam get-role-policy --role-name "$ROLE" --policy-name "$POLICY_NAME" \
    >/dev/null 2>&1; then
    echo "attached: $ROLE carries the $POLICY_NAME inline policy"
    exit 0
  fi
  echo "NOT attached: $ROLE has no $POLICY_NAME policy, so the alarm relay cannot read alarm state" >&2
  exit 1
fi

aws iam put-role-policy \
  --role-name "$ROLE" \
  --policy-name "$POLICY_NAME" \
  --policy-document "$POLICY_DOCUMENT"

# Read it back rather than trusting the exit code above.
aws iam get-role-policy --role-name "$ROLE" --policy-name "$POLICY_NAME" \
  --query PolicyDocument --output json

cat <<EOF

Attached $POLICY_NAME to $ROLE.

The relay workflow uses the AWS_OIDC_ROLE_ARN repository variable, which
already points at this role, so nothing else needs configuring. Its next
scheduled run will read alarm state and file or update one issue.

Subscribing a real endpoint to $TOPIC_ARN is still worth doing. The two paths
fail independently; this one just does not need an address.
EOF
