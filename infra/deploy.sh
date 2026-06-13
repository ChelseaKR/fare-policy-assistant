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
cp "$ROOT/web/handler.py" "$ROOT/web/index.html" "$BUNDLE/web/"

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
# request budget on top (web/handler.py).
aws lambda put-function-concurrency --function-name "$FN" --region "$REGION" \
  --reserved-concurrent-executions 2 >/dev/null

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
# Gateway-level throttle, ahead of the handler's own request budget.
aws apigatewayv2 update-stage --region "$REGION" --api-id "$API_ID" \
  --stage-name '$default' \
  --default-route-settings '{"ThrottlingRateLimit": 2, "ThrottlingBurstLimit": 5}' \
  >/dev/null

# Short log retention: logs hold counts and timings, never rider questions.
aws logs create-log-group --log-group-name "/aws/lambda/$FN" --region "$REGION" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" \
  --retention-in-days 14 --region "$REGION"

echo "deployed: https://$API_ID.execute-api.$REGION.amazonaws.com/"
