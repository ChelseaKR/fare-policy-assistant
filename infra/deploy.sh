#!/usr/bin/env bash
# Deploy the demo: one Lambda behind a Function URL (ADR 0004).
#
#   ./infra/deploy.sh            # create or update, then print the public URL
#
# Requires the AWS CLI with credentials that may manage IAM, Lambda, and
# CloudWatch Logs. Region comes from AWS_REGION (default us-west-2, matching
# CI). Idempotent: safe to re-run after any change to code, corpus, or prompts.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
ROLE_NAME="$FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/infra/build"
BUNDLE="$BUILD/bundle"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# Hard ceiling on parallel Bedrock spend: at most this many containers run at
# once, no matter how many requests arrive. Every other rate figure below is
# derived from it so the two never drift out of sync (see ADR 0004 amendment,
# "a true cross-container rate limit" / roadmap P1 item 4).
RESERVED_CONCURRENCY=2
# API Gateway stage throttle, tuned to that ceiling: sustained rate equals the
# concurrency ceiling (a container answers a request in a few seconds, so
# admitting more than RESERVED_CONCURRENCY requests/sec would just queue and
# eventually 429/timeout at the Lambda layer instead of the gateway layer);
# burst allows one short spike above steady-state (e.g. two riders loading the
# page and asking at the same moment) to queue briefly rather than bounce.
# This is the actual cross-container ceiling: it is enforced by API Gateway
# before any container runs, so it holds identically whether the request lands
# on a warm container, a cold start, or a container that no longer exists by
# the time the next request arrives -- unlike the handler's in-memory budget
# (web/handler.py), which resets per container and is not shared across them.
THROTTLE_RATE_LIMIT="$RESERVED_CONCURRENCY"
THROTTLE_BURST_LIMIT=$((RESERVED_CONCURRENCY * 2 + 1))

# ── bundle ───────────────────────────────────────────────────────────────────
# The zip mirrors the repo layout (src/, prompts/, corpus/, web/) so that
# config.REPO_ROOT resolves the same way it does in a checkout.
rm -rf "$BUNDLE" "$BUILD/bundle.zip"
mkdir -p "$BUNDLE/src" "$BUNDLE/corpus/processed" "$BUNDLE/web"

# Cross-platform install: the Lambda runs linux/arm64, not the build machine's
# platform, so force manylinux wheels (numpy's C extension breaks otherwise).
uv pip install --quiet --target "$BUNDLE" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 --only-binary :all: \
  "anthropic[bedrock]>=0.100" "rank-bm25>=0.2.2" "pyyaml>=6.0" \
  "httpx>=0.27" "beautifulsoup4>=4.12"

cp -R "$ROOT/src/assistant" "$BUNDLE/src/assistant"
cp -R "$ROOT/prompts" "$BUNDLE/prompts"
cp "$ROOT/corpus/processed/chunks.jsonl" "$BUNDLE/corpus/processed/"
cp "$ROOT/web/__init__.py" "$ROOT/web/handler.py" "$ROOT/web/index.html" \
   "$ROOT/web/offline.py" "$ROOT/web/embed.py" "$BUNDLE/web/"

(cd "$BUNDLE" && zip -qr "$BUILD/bundle.zip" . -x '*__pycache__*' -x '*.dist-info/RECORD')

# ── IAM role: logs plus InvokeModel on the pinned answer model only ──────────
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/$FN*"
    },
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:$REGION:$ACCOUNT:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
      ]
    }
  ]
}
EOF
)

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "created role $ROLE_NAME; waiting for IAM propagation"
  sleep 10
fi
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "$FN-policy" --policy-document "$POLICY"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"

# ── Lambda ───────────────────────────────────────────────────────────────────
if aws lambda get-function --function-name "$FN" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --region "$REGION" \
    --architectures arm64 --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" >/dev/null
else
  aws lambda create-function --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler --architectures arm64 \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION"

# Hard ceiling on parallel Bedrock spend; the handler adds a per-container
# request budget on top (web/handler.py) as defense in depth within a warm
# container, but the true cross-container ceiling is the gateway throttle set
# below, derived from this same RESERVED_CONCURRENCY value.
aws lambda put-function-concurrency --function-name "$FN" --region "$REGION" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" >/dev/null

# Public endpoint: an HTTP API in front of the function. The first deploy
# used a Function URL with auth NONE; this account denies anonymous
# InvokeFunctionUrl at the policy layer (org-level public-access block), so
# the URL answered 403 no matter what the resource policy said. The HTTP API
# is invoked by the API Gateway service principal instead, sends the same
# payload-v2 event shape, and adds native throttling. ADR 0004 amendment.
aws lambda delete-function-url-config --function-name "$FN" --region "$REGION" \
  >/dev/null 2>&1 || true
aws lambda remove-permission --function-name "$FN" --region "$REGION" \
  --statement-id public-url >/dev/null 2>&1 || true

API_ID="$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='$FN'].ApiId | [0]" --output text)"
if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  API_ID="$(aws apigatewayv2 create-api --region "$REGION" --name "$FN" \
    --protocol-type HTTP \
    --target "arn:aws:lambda:$REGION:$ACCOUNT:function:$FN" \
    --query ApiId --output text)"
fi
aws lambda add-permission --function-name "$FN" --region "$REGION" \
  --statement-id apigw --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" \
  >/dev/null 2>&1 || true
# Gateway-level throttle: the true cross-container rate limit (roadmap P1
# item 4), ahead of the handler's own per-container request budget. Values are
# derived from RESERVED_CONCURRENCY above, not restated here, so a future
# change to one cannot silently leave the other untuned.
aws apigatewayv2 update-stage --region "$REGION" --api-id "$API_ID" \
  --stage-name '$default' \
  --default-route-settings "{\"ThrottlingRateLimit\": $THROTTLE_RATE_LIMIT, \"ThrottlingBurstLimit\": $THROTTLE_BURST_LIMIT}" \
  >/dev/null

# Short log retention: logs hold counts and timings, never rider questions.
aws logs create-log-group --log-group-name "/aws/lambda/$FN" --region "$REGION" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" \
  --retention-in-days 14 --region "$REGION"

# ── observability: alarms on errors, throttles, latency, and Bedrock calls ───
# Alarms publish to an SNS topic; subscribe an email once to be paged:
#   aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint you@example.com
TOPIC_ARN="$(aws sns create-topic --name "$FN-alerts" --region "$REGION" \
  --query TopicArn --output text)"

# A metric filter turns the handler's structured error log into a metric. The
# handler logs {"error": "<Type>"} only on a 500 (never rider content).
aws logs put-metric-filter --region "$REGION" \
  --log-group-name "/aws/lambda/$FN" \
  --filter-name "$FN-handler-errors" \
  --filter-pattern '{ $.error = * }' \
  --metric-transformations \
    "metricName=HandlerErrors,metricNamespace=$FN,metricValue=1,defaultValue=0" >/dev/null
# A second filter counts answer-model calls (cache misses) as a spend proxy.
aws logs put-metric-filter --region "$REGION" \
  --log-group-name "/aws/lambda/$FN" \
  --filter-name "$FN-bedrock-calls" \
  --filter-pattern '{ $.cache = "miss" }' \
  --metric-transformations \
    "metricName=BedrockAnswerCalls,metricNamespace=$FN,metricValue=1,defaultValue=0" >/dev/null
# Thumbs-down feedback (verdict only, no content) as a quality signal.
aws logs put-metric-filter --region "$REGION" \
  --log-group-name "/aws/lambda/$FN" \
  --filter-name "$FN-feedback-down" \
  --filter-pattern '{ $.feedback = "down" }' \
  --metric-transformations \
    "metricName=FeedbackDown,metricNamespace=$FN,metricValue=1,defaultValue=0" >/dev/null

_alarm() {  # name, namespace, metric, statistic, period, threshold, dimensions...
  aws cloudwatch put-metric-alarm --region "$REGION" \
    --alarm-name "$FN-$1" --namespace "$2" --metric-name "$3" \
    --statistic "$4" --period "$5" --threshold "$6" --evaluation-periods 1 \
    --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
    --alarm-actions "$TOPIC_ARN" "${@:7}" >/dev/null
}
LAMBDA_DIM="Name=FunctionName,Value=$FN"
_alarm handler-errors "$FN" HandlerErrors Sum 300 0
_alarm lambda-errors AWS/Lambda Errors Sum 300 0 --dimensions "$LAMBDA_DIM"
_alarm lambda-throttles AWS/Lambda Throttles Sum 300 0 --dimensions "$LAMBDA_DIM"
# p99 latency over 20s (the function timeout is 25s); Duration is in ms.
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "$FN-latency-p99" --namespace AWS/Lambda --metric-name Duration \
  --extended-statistic p99 --period 300 --threshold 20000 --evaluation-periods 1 \
  --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
  --dimensions "$LAMBDA_DIM" --alarm-actions "$TOPIC_ARN" >/dev/null
# Cost backstop: more than 500 answer-model calls in 5 minutes is well beyond
# demo traffic and trips before spend runs away (concurrency caps it anyway).
_alarm bedrock-surge "$FN" BedrockAnswerCalls Sum 300 500

# An account-level AWS Budget is the spend backstop beneath these; it needs
# billing permissions this role may lack, so it stays a one-time manual step:
#   aws budgets create-budget --account-id <id> --budget '{...}'  (see infra/README.md)

echo "deployed: https://$API_ID.execute-api.$REGION.amazonaws.com/"
echo "alerts topic: $TOPIC_ARN (subscribe an email to receive alarms)"
