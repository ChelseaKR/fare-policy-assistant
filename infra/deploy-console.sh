#!/usr/bin/env bash
# Deploy the agency operator console: a second, separately deployed Lambda +
# API Gateway route, distinct from the rider-facing demo `infra/deploy.sh`
# deploys. See docs/ideation/03-expansions.md (EXP-09) for the design
# rationale and web/console.py for the handler and its routes.
#
#   FPA_RIDER_FUNCTION_NAME=fare-policy-assistant-demo \
#   FPA_CONSOLE_TOKEN=$(openssl rand -hex 32) \
#   ./infra/deploy-console.sh
#
# Requires the AWS CLI with credentials that may manage IAM, Lambda, API
# Gateway, and CloudWatch Logs, plus a local `git` (the bundle step runs
# `make history`, which shells out to git). Idempotent: safe to re-run after
# any change to web/console.py or the corpus.
#
# ── SECURITY: read this before handing the URL to an agency operator ────────
# The bearer-token check in web/console.py (FPA_CONSOLE_TOKEN) is adequate for
# a single-operator pilot but is not identity: anyone holding the token has
# full console access (pin any known corpus version, rewrite the embed
# allowlist). Before treating a deployment as production for a non-technical
# agency operator, put a real authorizer in front of this console's API
# Gateway route:
#
#   aws apigatewayv2 create-authorizer --api-id "$CONSOLE_API_ID" \
#     --authorizer-type JWT --identity-source '$request.header.Authorization' \
#     --jwt-configuration Audience=<client-id>,Issuer=<the agency's IdP issuer URL> \
#     --name "$CONSOLE_FN-authorizer"
#   aws apigatewayv2 update-route --api-id "$CONSOLE_API_ID" --route-id <id> \
#     --authorization-type JWT --authorizer-id <authorizer-id>
#
# backed by the agency's own SSO/IdP (Cognito, Entra ID, Google Workspace,
# whatever they already run) — the "behind the agency's own SSO/IAM" shape
# EXP-09 calls for. Wiring a *specific* agency's IdP into that command is a
# one-time manual step tied to credentials this script has no business
# holding, exactly like the AWS Budget setup in infra/README.md; it is
# intentionally left out of this script rather than faked. Until it is done,
# treat FPA_CONSOLE_TOKEN as the only gate and rotate it like any other secret.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
RIDER_FN="${FPA_RIDER_FUNCTION_NAME:?set FPA_RIDER_FUNCTION_NAME to the rider Lambda this console manages}"
CONSOLE_FN="${FPA_CONSOLE_FUNCTION_NAME:-$RIDER_FN-console}"
CONSOLE_TOKEN="${FPA_CONSOLE_TOKEN:?set FPA_CONSOLE_TOKEN, e.g. \$(openssl rand -hex 32), before deploying}"
ROLE_NAME="$CONSOLE_FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/infra/build-console"
BUNDLE="$BUILD/bundle"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# ── bundle ───────────────────────────────────────────────────────────────────
# The console reads a static changelog rather than shelling out to git at
# request time (the standard Lambda Python runtime ships no git binary) — see
# web/console.py's module docstring. Regenerate it fresh from this checkout's
# git history right before bundling, so the console always ships the same
# history this deploy's operator would see in `git log`.
(cd "$ROOT" && uv run python -m assistant.corpus history > "$ROOT/corpus/version_history.json")

rm -rf "$BUNDLE" "$BUILD/bundle.zip"
mkdir -p "$BUNDLE/src" "$BUNDLE/corpus/processed" "$BUNDLE/web" "$BUNDLE/evals"

# No model provider, no Bedrock: only the AWS SDK to read/write the rider
# Lambda's configuration, plus the corpus/eval-report readers it shares with
# the rest of assistant/.
uv pip install --quiet --target "$BUNDLE" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 --only-binary :all: \
  "boto3>=1.34" "pyyaml>=6.0" "httpx>=0.27" "beautifulsoup4>=4.12"

cp -R "$ROOT/src/assistant" "$BUNDLE/src/assistant"
cp "$ROOT/corpus/processed/chunks.jsonl" "$BUNDLE/corpus/processed/"
cp "$ROOT/corpus/version_history.json" "$BUNDLE/corpus/"
cp "$ROOT/web/__init__.py" "$ROOT/web/console.py" "$ROOT/web/a11y.py" "$BUNDLE/web/"
touch "$BUNDLE/evals/__init__.py"
# Eval reports the console can read (the rider deploy's own bundle does not
# include these; the console needs them to render the "latest eval report"
# panel). Skips cleanly if none have been run yet.
if [ -d "$ROOT/evals/runs" ]; then
  cp -R "$ROOT/evals/runs" "$BUNDLE/evals/runs"
fi

(cd "$BUNDLE" && zip -qr "$BUILD/bundle.zip" . -x '*__pycache__*' -x '*.dist-info/RECORD')

# ── IAM role: logs, plus the two rider-Lambda config calls the console makes,
# scoped to exactly that one function's ARN — never lambda:* and never a
# wildcard resource. ──────────────────────────────────────────────────────
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/$CONSOLE_FN*"
    },
    {
      "Effect": "Allow",
      "Action": ["lambda:GetFunctionConfiguration", "lambda:UpdateFunctionConfiguration"],
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$RIDER_FN"
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
  --policy-name "$CONSOLE_FN-policy" --policy-document "$POLICY"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"

# ── Lambda ───────────────────────────────────────────────────────────────────
ENV_VARS="Variables={FPA_CONSOLE_TOKEN=$CONSOLE_TOKEN,FPA_RIDER_FUNCTION_NAME=$RIDER_FN}"
if aws lambda get-function --function-name "$CONSOLE_FN" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$CONSOLE_FN" --region "$REGION" \
    --architectures arm64 --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
  aws lambda wait function-updated --function-name "$CONSOLE_FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$CONSOLE_FN" --region "$REGION" \
    --runtime python3.12 --handler web.console.console_handler \
    --timeout 15 --memory-size 256 --role "$ROLE_ARN" --environment "$ENV_VARS" >/dev/null
else
  aws lambda create-function --function-name "$CONSOLE_FN" --region "$REGION" \
    --runtime python3.12 --handler web.console.console_handler --architectures arm64 \
    --timeout 15 --memory-size 256 --role "$ROLE_ARN" --environment "$ENV_VARS" \
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
fi
aws lambda wait function-updated --function-name "$CONSOLE_FN" --region "$REGION"
# Single operator, not a public rider surface: a tight concurrency ceiling is
# plenty and bounds cost from a leaked token.
aws lambda put-function-concurrency --function-name "$CONSOLE_FN" --region "$REGION" \
  --reserved-concurrent-executions 1 >/dev/null

# ── HTTP API, separate from the rider API (own ApiId, own routes) ───────────
API_ID="$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='$CONSOLE_FN'].ApiId | [0]" --output text)"
if [ -z "$API_ID" ] || [ "$API_ID" = "None" ]; then
  API_ID="$(aws apigatewayv2 create-api --region "$REGION" --name "$CONSOLE_FN" \
    --protocol-type HTTP \
    --target "arn:aws:lambda:$REGION:$ACCOUNT:function:$CONSOLE_FN" \
    --query ApiId --output text)"
fi
aws lambda add-permission --function-name "$CONSOLE_FN" --region "$REGION" \
  --statement-id apigw --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" \
  >/dev/null 2>&1 || true
aws apigatewayv2 update-stage --region "$REGION" --api-id "$API_ID" \
  --stage-name '$default' \
  --default-route-settings '{"ThrottlingRateLimit": 2, "ThrottlingBurstLimit": 5}' \
  >/dev/null

aws logs create-log-group --log-group-name "/aws/lambda/$CONSOLE_FN" --region "$REGION" \
  2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$CONSOLE_FN" \
  --retention-in-days 14 --region "$REGION"

API_URL="$(aws apigatewayv2 get-api --api-id "$API_ID" --region "$REGION" \
  --query ApiEndpoint --output text)"
echo "Console deployed: $API_URL/console"
echo "Remember the authorizer step in this script's header comment before sharing that URL."
