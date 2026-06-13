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

uv pip install --quiet --target "$BUNDLE" \
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
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" >/dev/null
else
  aws lambda create-function --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --handler web.handler.handler \
    --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION"

# Hard ceiling on parallel Bedrock spend; the handler adds a per-container
# request budget on top (web/handler.py).
aws lambda put-function-concurrency --function-name "$FN" --region "$REGION" \
  --reserved-concurrent-executions 2 >/dev/null

# Public URL. Auth NONE is deliberate: the demo is public and the guards are
# concurrency, the request budget, the input cap, and pinned max_tokens.
aws lambda create-function-url-config --function-name "$FN" --region "$REGION" \
  --auth-type NONE >/dev/null 2>&1 || true
aws lambda add-permission --function-name "$FN" --region "$REGION" \
  --statement-id public-url --action lambda:InvokeFunctionUrl \
  --principal '*' --function-url-auth-type NONE >/dev/null 2>&1 || true

# Short log retention: logs hold counts and timings, never rider questions.
aws logs create-log-group --log-group-name "/aws/lambda/$FN" --region "$REGION" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" \
  --retention-in-days 14 --region "$REGION"

URL="$(aws lambda get-function-url-config --function-name "$FN" --region "$REGION" \
  --query FunctionUrl --output text)"
echo "deployed: $URL"
