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

# Preserve operator-owned Lambda settings across iterative deploys. AWS replaces
# the entire Variables map on update, so constructing it from only this script's
# three controls would silently erase settings such as FPA_EMBED_ANCESTORS.
FUNCTION_EXISTS=false
if EXISTING_LAMBDA_ENV="$(
  aws lambda get-function-configuration --function-name "$FN" --region "$REGION" \
    --query 'Environment.Variables' --output json 2>&1
)"; then
  FUNCTION_EXISTS=true
elif [[ "$EXISTING_LAMBDA_ENV" == *"ResourceNotFoundException"* ]]; then
  # A confirmed missing function is the only safe case for starting with an
  # empty environment. Authentication, authorization, and network failures
  # must abort rather than masquerade as a first deploy.
  EXISTING_LAMBDA_ENV='{}'
else
  echo "could not read existing Lambda environment; refusing to deploy:" >&2
  echo "$EXISTING_LAMBDA_ENV" >&2
  exit 1
fi

lambda_env_value() {
  (
    cd "$ROOT"
    FPA_DEPLOY_EXISTING_LAMBDA_ENV="$EXISTING_LAMBDA_ENV" \
      FPA_DEPLOY_ENV_KEY="$1" \
      uv run python -c '
import json
import os

raw = json.loads(os.environ["FPA_DEPLOY_EXISTING_LAMBDA_ENV"] or "{}")
values = raw if isinstance(raw, dict) else {}
print(values.get(os.environ["FPA_DEPLOY_ENV_KEY"], ""))
'
  )
}

EXISTING_DISABLED_DOC_IDS="$(lambda_env_value FPA_DISABLED_DOC_IDS)"
EXISTING_HISTORY_HMAC_KEY="$(lambda_env_value FPA_HISTORY_HMAC_KEY)"

# Production evidence controls. The currently reviewed bundle is pinned by its
# deterministic corpus identity. ``yolobus-fares`` is contained by default
# because the committed fare period ended 2026-06-30; remove it only after the
# replacement source has been reviewed, ingested, evaluated, and approved.
PINNED_CORPUS_VERSION="${FPA_PINNED_CORPUS_VERSION:-$(cd "$ROOT" && uv run python -c 'from assistant.corpus import corpus_version; print(corpus_version())')}"
if [[ ${FPA_DISABLED_DOC_IDS+x} ]]; then
  DISABLED_DOC_IDS="$FPA_DISABLED_DOC_IDS"
elif [[ -n "$EXISTING_DISABLED_DOC_IDS" ]]; then
  DISABLED_DOC_IDS="$EXISTING_DISABLED_DOC_IDS"
else
  DISABLED_DOC_IDS="yolobus-fares"
fi
if [[ ${FPA_HISTORY_HMAC_KEY+x} ]]; then
  HISTORY_HMAC_KEY="$FPA_HISTORY_HMAC_KEY"
elif [[ -n "$EXISTING_HISTORY_HMAC_KEY" ]]; then
  HISTORY_HMAC_KEY="$EXISTING_HISTORY_HMAC_KEY"
else
  HISTORY_HMAC_KEY="$(openssl rand -hex 32)"
fi
if [[ ! "$PINNED_CORPUS_VERSION" =~ ^[0-9a-f]{12}$ ]]; then
  echo "invalid corpus pin: expected a 12-character lowercase hex digest" >&2
  exit 2
fi
if [[ -n "$DISABLED_DOC_IDS" && ! "$DISABLED_DOC_IDS" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]]; then
  echo "invalid disabled document list: expected comma-separated document ids" >&2
  exit 2
fi
if [[ ! "$HISTORY_HMAC_KEY" =~ ^[0-9a-f]{64}$ ]]; then
  echo "invalid history signing key: expected a 64-character lowercase hex secret" >&2
  exit 2
fi
if [[ -n "$DISABLED_DOC_IDS" ]]; then
  (
    cd "$ROOT"
    FPA_DEPLOY_DISABLED_DOC_IDS="$DISABLED_DOC_IDS" uv run python -c '
import os

from assistant.ingest import load_chunks

requested = set(os.environ["FPA_DEPLOY_DISABLED_DOC_IDS"].split(","))
known = {chunk.doc_id for chunk in load_chunks()}
unknown = sorted(requested - known)
if unknown:
    raise SystemExit("unknown disabled document id(s): " + ", ".join(unknown))
'
  )
fi
LAMBDA_ENV="$(
  cd "$ROOT"
  FPA_DEPLOY_EXISTING_LAMBDA_ENV="$EXISTING_LAMBDA_ENV" \
    FPA_DEPLOY_PINNED_CORPUS_VERSION="$PINNED_CORPUS_VERSION" \
    FPA_DEPLOY_DISABLED_DOC_IDS="$DISABLED_DOC_IDS" \
    FPA_DEPLOY_HISTORY_HMAC_KEY="$HISTORY_HMAC_KEY" \
    uv run python -c '
import json
import os

raw = json.loads(os.environ["FPA_DEPLOY_EXISTING_LAMBDA_ENV"] or "{}")
values = raw if isinstance(raw, dict) else {}
values.update(
    {
        "FPA_PINNED_CORPUS_VERSION": os.environ["FPA_DEPLOY_PINNED_CORPUS_VERSION"],
        "FPA_DISABLED_DOC_IDS": os.environ["FPA_DEPLOY_DISABLED_DOC_IDS"],
        "FPA_HISTORY_HMAC_KEY": os.environ["FPA_DEPLOY_HISTORY_HMAC_KEY"],
    }
)
print(json.dumps({"Variables": values}, separators=(",", ":")))
'
)"

# ── bundle ───────────────────────────────────────────────────────────────────
# The zip mirrors the repo layout (src/, prompts/, corpus/, web/) so that
# config.REPO_ROOT resolves the same way it does in a checkout.
rm -rf "$BUNDLE" "$BUILD/bundle.zip"
mkdir -p "$BUNDLE/src" "$BUNDLE/corpus/processed" "$BUNDLE/docs" "$BUNDLE/web"

# Cross-platform install: the Lambda runs linux/arm64, not the build machine's
# platform, so force manylinux wheels (numpy's C extension breaks otherwise).
# The python3.12 Lambda runtime is Amazon Linux 2023 (glibc 2.34), so
# manylinux_2_28 wheels are safe; the locked numpy publishes no manylinux2014
# wheels, which is why the older 2014 tag is not used here.
#
# Hash-pinned (roadmap M-7 / audit P1-6): the bundle installs exactly the
# versions uv.lock tested, verified by hash, so the deployed artifact cannot
# drift from the tested tree. Regenerate infra/requirements-deploy.txt with
# `make deploy-reqs` after any dependency change;
# tests/test_deploy_requirements.py keeps it in lockstep with uv.lock.
uv pip install --quiet --target "$BUNDLE" \
  --python-platform aarch64-manylinux_2_28 --python-version 3.12 --only-binary :all: \
  --require-hashes -r "$ROOT/infra/requirements-deploy.txt"

cp -R "$ROOT/src/assistant" "$BUNDLE/src/assistant"
cp -R "$ROOT/prompts" "$BUNDLE/prompts"
cp "$ROOT/corpus/processed/chunks.jsonl" "$BUNDLE/corpus/processed/"
cp "$ROOT/docs/answer-contract.schema.json" "$BUNDLE/docs/"
cp "$ROOT/web/__init__.py" "$ROOT/web/handler.py" "$ROOT/web/index.html" \
   "$ROOT/web/offline.py" "$ROOT/web/guide.py" "$ROOT/web/embed.py" \
   "$ROOT/web/csp.py" "$BUNDLE/web/"

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
if [[ "$FUNCTION_EXISTS" == "true" ]]; then
  # Capture the exact live code plus full configuration before mutation. The
  # signed AWS download URL is consumed without printing it; the rollback
  # directory is private because configuration may contain secrets.
  ROLLBACK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-rollback.XXXXXX")"
  chmod 700 "$ROLLBACK_DIR"
  aws lambda get-function-configuration --function-name "$FN" --region "$REGION" \
    >"$ROLLBACK_DIR/configuration.json"
  PREVIOUS_CODE_URL="$(
    aws lambda get-function --function-name "$FN" --region "$REGION" \
      --query Code.Location --output text
  )"
  curl --silent --show-error --fail --location "$PREVIOUS_CODE_URL" \
    --output "$ROLLBACK_DIR/function.zip"
  chmod 600 "$ROLLBACK_DIR/configuration.json" "$ROLLBACK_DIR/function.zip"
  echo "saved pre-deploy rollback artifact: $ROLLBACK_DIR"

  # Apply and verify required containment/configuration before new code can go
  # live. If configuration fails, the old code remains in place.
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
    --environment "$LAMBDA_ENV" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  APPLIED_LAMBDA_ENV="$(
    aws lambda get-function-configuration --function-name "$FN" --region "$REGION" \
      --query 'Environment.Variables' --output json
  )"
  (
    cd "$ROOT"
    FPA_DEPLOY_APPLIED_LAMBDA_ENV="$APPLIED_LAMBDA_ENV" \
      FPA_DEPLOY_PINNED_CORPUS_VERSION="$PINNED_CORPUS_VERSION" \
      FPA_DEPLOY_DISABLED_DOC_IDS="$DISABLED_DOC_IDS" \
      FPA_DEPLOY_HISTORY_HMAC_KEY="$HISTORY_HMAC_KEY" \
      uv run python -c '
import json
import os

actual = json.loads(os.environ["FPA_DEPLOY_APPLIED_LAMBDA_ENV"] or "{}")
expected = {
    "FPA_PINNED_CORPUS_VERSION": os.environ["FPA_DEPLOY_PINNED_CORPUS_VERSION"],
    "FPA_DISABLED_DOC_IDS": os.environ["FPA_DEPLOY_DISABLED_DOC_IDS"],
    "FPA_HISTORY_HMAC_KEY": os.environ["FPA_DEPLOY_HISTORY_HMAC_KEY"],
}
wrong = sorted(key for key, value in expected.items() if actual.get(key) != value)
if wrong:
    raise SystemExit("Lambda environment verification failed for: " + ", ".join(wrong))
'
  )
  aws lambda update-function-code --function-name "$FN" --region "$REGION" \
    --architectures arm64 --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
else
  aws lambda create-function --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler --architectures arm64 \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
    --environment "$LAMBDA_ENV" \
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
# A second filter counts answer-model calls as a spend proxy. This is separate
# from cache status because a fail-closed output guard still consumed Bedrock.
aws logs put-metric-filter --region "$REGION" \
  --log-group-name "/aws/lambda/$FN" \
  --filter-name "$FN-bedrock-calls" \
  --filter-pattern '{ $.model_called IS TRUE }' \
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

# ── dashboard: per-day cost proxy, live traffic, and alarm status ────────────
# put-dashboard creates or overwrites by name, so this is idempotent. The
# BedrockAnswerCalls widget uses a 1-day period so per-day call volume (the
# cost proxy) is legible; a second widget shows 5-minute traffic. The alarm
# widget surfaces the five alarms above without leaving the dashboard.
_alarm_arn() { echo "arn:aws:cloudwatch:$REGION:$ACCOUNT:alarm:$FN-$1"; }
DASHBOARD_BODY=$(cat <<EOF
{
  "widgets": [
    {
      "type": "metric", "x": 0, "y": 0, "width": 12, "height": 6,
      "properties": {
        "title": "Bedrock answer calls per day (cost proxy)",
        "region": "$REGION", "view": "timeSeries", "stat": "Sum", "period": 86400,
        "metrics": [["$FN", "BedrockAnswerCalls"]]
      }
    },
    {
      "type": "metric", "x": 12, "y": 0, "width": 12, "height": 6,
      "properties": {
        "title": "Traffic (5-minute)",
        "region": "$REGION", "view": "timeSeries", "stat": "Sum", "period": 300,
        "metrics": [
          ["AWS/Lambda", "Invocations", "FunctionName", "$FN"],
          ["AWS/Lambda", "Errors", "FunctionName", "$FN"],
          ["AWS/Lambda", "Throttles", "FunctionName", "$FN"],
          ["$FN", "HandlerErrors"],
          ["$FN", "FeedbackDown"]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Duration p99 (ms)",
        "region": "$REGION", "view": "timeSeries", "stat": "p99", "period": 300,
        "metrics": [["AWS/Lambda", "Duration", "FunctionName", "$FN"]]
      }
    },
    {
      "type": "alarm", "x": 12, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Alarms",
        "alarms": [
          "$(_alarm_arn handler-errors)",
          "$(_alarm_arn lambda-errors)",
          "$(_alarm_arn lambda-throttles)",
          "$(_alarm_arn latency-p99)",
          "$(_alarm_arn bedrock-surge)"
        ]
      }
    }
  ]
}
EOF
)
aws cloudwatch put-dashboard --region "$REGION" --dashboard-name "$FN" \
  --dashboard-body "$DASHBOARD_BODY" >/dev/null

# An account-level AWS Budget is the spend backstop beneath these; it needs
# billing permissions this role may lack, so it stays a one-time manual step:
#   aws budgets create-budget --account-id <id> --budget '{...}'  (see infra/README.md)

echo "deployed: https://$API_ID.execute-api.$REGION.amazonaws.com/"
echo "corpus pin: $PINNED_CORPUS_VERSION"
echo "disabled documents pending review: $DISABLED_DOC_IDS"
echo "alerts topic: $TOPIC_ARN (subscribe an email to receive alarms)"
echo "dashboard: https://$REGION.console.aws.amazon.com/cloudwatch/home?region=$REGION#dashboards/dashboard/$FN"
