#!/usr/bin/env bash
# Deploy the spend circuit breaker: the control plane that actually stops spend.
#
#   ./infra/deploy-cutoff.sh          # create/update the breaker and its alarm
#
# This is a one-time deploy, not part of a release. It creates nothing the rider
# function depends on to serve traffic: if every resource here is deleted, the
# rider keeps answering exactly as it does today. Run ./infra/deploy.sh first --
# it owns the DynamoDB table this writes into.
#
# What this wires up:
#
#   EstimatedModelCostUsd alarm ─┐
#   tag-scoped AWS Budget ───────┴─> SNS $FN-spend-cutoff ─> breaker Lambda
#                                                                  │
#                                                       writes "spend-breaker"
#                                                        row in the limiter
#                                                        table, which the rider
#                                                        reads within 30s and
#                                                        stops calling Bedrock
#
# The alarm is the fast path (minutes, from this deployment's own token-derived
# cost estimate). The budget is the slow, billing-authoritative path: AWS
# Budgets refreshes about three times a day and lags real usage by 8-12 hours,
# so it can confirm a runaway but never catch one. Wiring both to the same topic
# means whichever notices first trips the same breaker.
#
# Nothing here resets itself. See "clearing the breaker" at the end of the run.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
BREAKER_FN="${FPA_BREAKER_FUNCTION_NAME:-$FN-spend-breaker}"
BREAKER_ROLE="$BREAKER_FN-role"
TOPIC_NAME="${FPA_CUTOFF_TOPIC_NAME:-$FN-spend-cutoff}"
RATE_LIMIT_TABLE="${FPA_RATE_LIMIT_TABLE:-$FN-limits}"
ALERTS_TOPIC_NAME="${FPA_ALERTS_TOPIC_NAME:-$FN-alerts}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${FPA_BUILD_DIR:-$ROOT/infra/build}"
BREAKER_BUILD="$BUILD/spend-breaker"

# The trip threshold, in application-estimated USD of answer-model spend inside
# one alarm period. Derivation: the documented budget is $20/month, and the
# repository's own measured cost is about $0.0048 per answer. $0.50 in 15
# minutes is therefore roughly 104 answers in a quarter hour -- two orders of
# magnitude above any real portfolio traffic, and a pace that would burn the
# whole monthly budget in about ten hours if sustained. Set it lower if you would
# rather serve the offline guide than pay for a surprise.
#
# This reads EstimatedModelCostUsd, which is an application estimate from
# observed tokens and the pinned price table, not an AWS billing metric
# (ADR 0019). It is the only cost signal available in minutes rather than hours.
CUTOFF_THRESHOLD_USD="${FPA_CUTOFF_THRESHOLD_USD:-0.50}"
CUTOFF_PERIOD_SECONDS="${FPA_CUTOFF_PERIOD_SECONDS:-900}"

PROJECT_TAG=fare-assistant
PROJECT_TAG_MAP="project=$PROJECT_TAG"
PROJECT_TAG_LIST="Key=project,Value=$PROJECT_TAG"

for required_command in aws jq uv; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "$required_command is required" >&2
    exit 2
  }
done

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TABLE_ARN="arn:aws:dynamodb:$REGION:$ACCOUNT:table/$RATE_LIMIT_TABLE"

aws dynamodb describe-table --table-name "$RATE_LIMIT_TABLE" --region "$REGION" \
  >/dev/null 2>&1 || {
  echo "limiter table $RATE_LIMIT_TABLE does not exist; run ./infra/deploy.sh first" >&2
  exit 1
}

# ── the topic every cutoff signal lands on ───────────────────────────────────
# Deliberately NOT the $FN-alerts topic. That one pages a human on handler
# errors, throttles, and latency; subscribing the breaker to it would cut off
# spend because a p99 got slow. This topic carries cost signals only.
TOPIC_ARN="$(
  aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" \
    --query TopicArn --output text
)"
ALERTS_TOPIC_ARN="arn:aws:sns:$REGION:$ACCOUNT:$ALERTS_TOPIC_NAME"

# AWS Budgets publishes as its own service principal, and the default topic
# policy allows only the account owner. Without this statement a budget
# notification is accepted by the Budgets console and then silently discarded,
# which is the worst possible failure mode for a spend control: it looks wired
# up and does nothing. CloudWatch alarms need no such grant, since they publish
# under the account that owns the alarm.
CUTOFF_TOPIC_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Id": "$TOPIC_NAME-policy",
  "Statement": [
    {
      "Sid": "AccountOwner",
      "Effect": "Allow",
      "Principal": {"AWS": "$ACCOUNT"},
      "Action": [
        "SNS:Publish", "SNS:Subscribe", "SNS:GetTopicAttributes",
        "SNS:SetTopicAttributes", "SNS:ListSubscriptionsByTopic",
        "SNS:AddPermission", "SNS:RemovePermission", "SNS:DeleteTopic"
      ],
      "Resource": "$TOPIC_ARN"
    },
    {
      "Sid": "AWSBudgets",
      "Effect": "Allow",
      "Principal": {"Service": "budgets.amazonaws.com"},
      "Action": "SNS:Publish",
      "Resource": "$TOPIC_ARN",
      "Condition": {"StringEquals": {"aws:SourceAccount": "$ACCOUNT"}}
    }
  ]
}
EOF
)
aws sns set-topic-attributes --region "$REGION" --topic-arn "$TOPIC_ARN" \
  --attribute-name Policy --attribute-value "$CUTOFF_TOPIC_POLICY" >/dev/null

# ── the breaker's own role ───────────────────────────────────────────────────
# It may write one item, identified by name, in one table. The
# dynamodb:LeadingKeys condition is what makes that literal rather than
# aspirational: with this policy the function cannot touch a caller counter even
# if its code were changed to try, so the control plane for the breaker can
# never read or clear anyone's rate-limit state.
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
BREAKER_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/$BREAKER_FN*"
    },
    {
      "Effect": "Allow",
      "Action": "dynamodb:PutItem",
      "Resource": "$TABLE_ARN",
      "Condition": {
        "ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["spend-breaker"]}
      }
    }
  ]
}
EOF
)

if ! aws iam get-role --role-name "$BREAKER_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$BREAKER_ROLE" \
    --assume-role-policy-document "$TRUST" \
    --tags "$PROJECT_TAG_LIST" >/dev/null
  echo "created role $BREAKER_ROLE; waiting for IAM propagation"
  sleep 10
fi
aws iam put-role-policy --role-name "$BREAKER_ROLE" \
  --policy-name "$BREAKER_FN-policy" --policy-document "$BREAKER_POLICY"
BREAKER_ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$BREAKER_ROLE"

# ── bundle: one reviewed file, nothing else ──────────────────────────────────
# No dependencies to install: boto3 is present in the Lambda Python runtime, and
# this function imports nothing from the assistant package. Built through the
# same reproducible zip builder the rider uses so the artifact hash is stable
# across machines.
# The bundle keeps the repo's own layout (web/spend_breaker.py), so the handler
# path below reads the same as the file path and nothing is renamed on the way
# in. copy_tracked_bundle.py admits only reviewed, tracked, byte-identical
# files, so this refuses to run from a dirty checkout for the same reason the
# rider deploy does.
rm -rf "$BREAKER_BUILD" "$BUILD/spend-breaker.zip"
mkdir -p "$BREAKER_BUILD/web"
(
  cd "$ROOT"
  uv run python scripts/copy_tracked_bundle.py \
    --repo-root "$ROOT" \
    --destination "$BREAKER_BUILD" \
    --file web/__init__.py \
    --file web/spend_breaker.py
  uv run python scripts/build_lambda_zip.py "$BREAKER_BUILD" "$BUILD/spend-breaker.zip"
)

BREAKER_ENV="Variables={FPA_RATE_LIMIT_TABLE=$RATE_LIMIT_TABLE}"
if aws lambda get-function --function-name "$BREAKER_FN" --region "$REGION" \
  >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$BREAKER_FN" --region "$REGION" \
    --zip-file "fileb://$BUILD/spend-breaker.zip" >/dev/null
  aws lambda wait function-updated --function-name "$BREAKER_FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$BREAKER_FN" \
    --region "$REGION" --environment "$BREAKER_ENV" --role "$BREAKER_ROLE_ARN" >/dev/null
  aws lambda wait function-updated --function-name "$BREAKER_FN" --region "$REGION"
else
  aws lambda create-function --function-name "$BREAKER_FN" --region "$REGION" \
    --runtime python3.12 --architectures arm64 \
    --role "$BREAKER_ROLE_ARN" \
    --handler web.spend_breaker.handler \
    --timeout 15 --memory-size 128 \
    --environment "$BREAKER_ENV" \
    --zip-file "fileb://$BUILD/spend-breaker.zip" \
    --tags "$PROJECT_TAG_MAP" >/dev/null
  aws lambda wait function-active --function-name "$BREAKER_FN" --region "$REGION"
fi
BREAKER_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$BREAKER_FN"

# Reserved concurrency 1: the breaker is idempotent, so a storm of notifications
# should collapse into one write rather than fan out.
aws lambda put-function-concurrency --function-name "$BREAKER_FN" --region "$REGION" \
  --reserved-concurrent-executions 1 >/dev/null

aws logs put-retention-policy --log-group-name "/aws/lambda/$BREAKER_FN" \
  --retention-in-days 14 --region "$REGION" 2>/dev/null || true

# ── subscribe the breaker to the topic ───────────────────────────────────────
aws lambda add-permission --function-name "$BREAKER_FN" --region "$REGION" \
  --statement-id "$TOPIC_NAME-invoke" \
  --action lambda:InvokeFunction --principal sns.amazonaws.com \
  --source-arn "$TOPIC_ARN" >/dev/null 2>&1 || true
EXISTING_SUBSCRIPTION="$(
  aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" --region "$REGION" \
    --output json | jq -r --arg arn "$BREAKER_ARN" \
    '[.Subscriptions[]? | select(.Endpoint == $arn)] | length'
)"
if [[ "$EXISTING_SUBSCRIPTION" == "0" ]]; then
  aws sns subscribe --topic-arn "$TOPIC_ARN" --region "$REGION" \
    --protocol lambda --notification-endpoint "$BREAKER_ARN" >/dev/null
fi

# ── the fast path: alarm on the application's own estimated model spend ──────
# Two alarm actions on purpose: cut the spend off AND page a human on the
# existing alerts topic. A breaker that trips silently is a breaker nobody
# resets. treat-missing-data notBreaching, because a quiet service publishes no
# cost samples and must not be cut off for being idle.
ALARM_NAME="$FN-model-spend-cutoff"
ALARM_ACTIONS=("$TOPIC_ARN")
if aws sns get-topic-attributes --topic-arn "$ALERTS_TOPIC_ARN" --region "$REGION" \
  >/dev/null 2>&1; then
  ALARM_ACTIONS+=("$ALERTS_TOPIC_ARN")
else
  echo "WARNING: alerts topic $ALERTS_TOPIC_ARN not found; the cutoff will not page anyone" >&2
fi
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "$ALARM_NAME" \
  --alarm-description "Application-estimated answer-model spend over $CUTOFF_THRESHOLD_USD USD in $CUTOFF_PERIOD_SECONDS seconds; trips the spend breaker" \
  --namespace "$FN" --metric-name EstimatedModelCostUsd \
  --statistic Sum --period "$CUTOFF_PERIOD_SECONDS" \
  --threshold "$CUTOFF_THRESHOLD_USD" --evaluation-periods 1 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions "${ALARM_ACTIONS[@]}" >/dev/null

UNTAGGED=""
_tag() {
  local label="$1"
  shift
  "$@" >/dev/null 2>&1 || UNTAGGED="$UNTAGGED${UNTAGGED:+, }$label"
}
_tag "sns topic $TOPIC_NAME" \
  aws sns tag-resource --region "$REGION" --resource-arn "$TOPIC_ARN" \
  --tags "$PROJECT_TAG_LIST"
_tag "lambda function $BREAKER_FN" \
  aws lambda tag-resource --region "$REGION" --resource "$BREAKER_ARN" \
  --tags "$PROJECT_TAG_MAP"
_tag "iam role $BREAKER_ROLE" \
  aws iam tag-role --role-name "$BREAKER_ROLE" --tags "$PROJECT_TAG_LIST"
_tag "log group /aws/lambda/$BREAKER_FN" \
  aws logs tag-resource --region "$REGION" \
  --resource-arn "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/$BREAKER_FN" \
  --tags "$PROJECT_TAG_MAP"
_tag "alarm $ALARM_NAME" \
  aws cloudwatch tag-resource --region "$REGION" \
  --resource-arn "arn:aws:cloudwatch:$REGION:$ACCOUNT:alarm:$ALARM_NAME" \
  --tags "$PROJECT_TAG_LIST"
if [[ -n "$UNTAGGED" ]]; then
  echo "WARNING: could not apply project=$PROJECT_TAG to: $UNTAGGED" >&2
fi

cat <<SUMMARY

spend cutoff deployed
  breaker function:  $BREAKER_FN
  cutoff topic:      $TOPIC_ARN
  fast-path alarm:   $ALARM_NAME (> $CUTOFF_THRESHOLD_USD USD per $CUTOFF_PERIOD_SECONDS s)
  limiter table:     $RATE_LIMIT_TABLE

Point the tag-scoped budget at the same topic (one-time, needs billing rights):
  see the "Spend cutoff" section of infra/README.md

Check the breaker:
  aws dynamodb get-item --region $REGION --table-name $RATE_LIMIT_TABLE \\
    --key '{"pk":{"S":"spend-breaker"}}'

Clear it after you have looked at why it tripped (nothing clears it on its own):
  aws dynamodb delete-item --region $REGION --table-name $RATE_LIMIT_TABLE \\
    --key '{"pk":{"S":"spend-breaker"}}'
Riders resume within about 30 seconds of that delete: each warm container
re-reads the breaker on its own SPEND_BREAKER_CACHE_SECONDS interval.

Trip it by hand, without waiting for the alarm:
  aws dynamodb put-item --region $REGION --table-name $RATE_LIMIT_TABLE \\
    --item '{"pk":{"S":"spend-breaker"},"open":{"BOOL":true},"reason":{"S":"manual"}}'
SUMMARY
