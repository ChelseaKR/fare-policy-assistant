#!/usr/bin/env bash
# Deploy the agency operator console: a second, separately deployed Lambda +
# API Gateway route, distinct from the rider-facing demo `infra/deploy.sh`
# deploys. See docs/ideation/03-expansions.md (EXP-09) for the design
# rationale and web/console.py for the handler and its routes.
#
#   FPA_RIDER_FUNCTION_NAME=fare-policy-assistant-demo \
#   FPA_CONSOLE_TOKEN_PARAMETER_NAME=/fare-policy-assistant/demo-console-token \
#   ./infra/deploy-console.sh
#
# Requires the AWS CLI with credentials that may manage IAM, Lambda, API
# Gateway, and CloudWatch Logs, plus a local `git` (the bundle step runs
# `make history`, which shells out to git). Idempotent: safe to re-run after
# any change to web/console.py or the corpus.
#
# ── SECURITY: read this before handing the URL to an agency operator ────────
# The bearer-token check in web/console.py is adequate for
# a single-operator pilot but is not identity: anyone holding the token has
# read-only console access to release configuration and evaluation evidence.
# Before treating a deployment as production for a non-technical
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
# treat the SSM-backed token as the only gate and rotate it like any other secret.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
RIDER_FN="${FPA_RIDER_FUNCTION_NAME:?set FPA_RIDER_FUNCTION_NAME to the rider Lambda this console manages}"
CONSOLE_FN="${FPA_CONSOLE_FUNCTION_NAME:-$RIDER_FN-console}"
CONSOLE_TOKEN_PARAMETER="${FPA_CONSOLE_TOKEN_PARAMETER_NAME:-/fare-policy-assistant/demo-console-token}"
ROLE_NAME="$CONSOLE_FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/infra/build-console"
BUNDLE="$BUILD/bundle"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# Cost allocation. Same activated `project` tag key, and the same value, as the
# rider deploy in deploy.sh: the operator console is part of the same project and
# its spend should land in the same bucket. See the longer note in deploy.sh.
PROJECT_TAG=fare-assistant
PROJECT_TAG_MAP="project=$PROJECT_TAG"
PROJECT_TAG_LIST="Key=project,Value=$PROJECT_TAG"

# ── bundle ───────────────────────────────────────────────────────────────────
# The console reads a static changelog rather than shelling out to git at
# request time (the standard Lambda Python runtime ships no git binary) — see
# web/console.py's module docstring. Regenerate it fresh from this checkout's
# git history right before bundling, so the console always ships the same
# history this deploy's operator would see in `git log`.
(cd "$ROOT" && uv run python -m assistant.corpus history > "$ROOT/corpus/version_history.json")

rm -rf "$BUNDLE" "$BUILD/bundle.zip"
mkdir -p "$BUNDLE/src" "$BUNDLE/corpus/processed" "$BUNDLE/web" "$BUNDLE/evals"

# No model provider, no Bedrock: only the AWS SDK to read the rider Lambda's
# immutable live alias/configuration, plus the corpus/eval-report readers it shares with
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

# ── IAM role: logs plus read-only access to the rider's live alias. ──────────
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
      "Action": "lambda:GetAlias",
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$RIDER_FN"
    },
    {
      "Effect": "Allow",
      "Action": "lambda:GetFunctionConfiguration",
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$RIDER_FN:live"
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParameter"],
      "Resource": "arn:aws:ssm:$REGION:$ACCOUNT:parameter$CONSOLE_TOKEN_PARAMETER"
    }
  ]
}
EOF
)

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" \
    --tags "$PROJECT_TAG_LIST" >/dev/null
  echo "created role $ROLE_NAME; waiting for IAM propagation"
  sleep 10
fi
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "$CONSOLE_FN-policy" --policy-document "$POLICY"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"

# ── Lambda ───────────────────────────────────────────────────────────────────
ENV_VARS="Variables={FPA_CONSOLE_TOKEN_PARAMETER_NAME=$CONSOLE_TOKEN_PARAMETER,FPA_RIDER_FUNCTION_NAME=$RIDER_FN,FPA_RIDER_ALIAS=live}"
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
    --tags "$PROJECT_TAG_MAP" \
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
    --tags "$PROJECT_TAG_MAP" \
    --query ApiId --output text)"
fi
aws lambda add-permission --function-name "$CONSOLE_FN" --region "$REGION" \
  --statement-id apigw --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" \
  >/dev/null 2>&1 || true
aws apigatewayv2 update-stage --region "$REGION" --api-id "$API_ID" \
  --stage-name "\$default" \
  --default-route-settings '{"ThrottlingRateLimit": 2, "ThrottlingBurstLimit": 5}' \
  >/dev/null

aws logs create-log-group --log-group-name "/aws/lambda/$CONSOLE_FN" --region "$REGION" \
  --tags "$PROJECT_TAG_MAP" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$CONSOLE_FN" \
  --retention-in-days 14 --region "$REGION"

# Re-apply `project` on every deploy so console resources created before this
# tagging existed stop hiding in the account's untagged bucket. Idempotent, and
# non-fatal for the same reason as in deploy.sh: the console is already live by
# now and a billing label should not fail the deploy. The SSM parameter holding
# the console token is created by hand (see this script's header), so it is not
# tagged here.
CONSOLE_UNTAGGED=""
_tag_console() {  # human-readable resource label, then the command that tags it
  local label="$1"
  shift
  "$@" >/dev/null 2>&1 || CONSOLE_UNTAGGED="$CONSOLE_UNTAGGED${CONSOLE_UNTAGGED:+, }$label"
}
_tag_console "lambda function $CONSOLE_FN" \
  aws lambda tag-resource --region "$REGION" \
  --resource "arn:aws:lambda:$REGION:$ACCOUNT:function:$CONSOLE_FN" \
  --tags "$PROJECT_TAG_MAP"
_tag_console "iam role $ROLE_NAME" \
  aws iam tag-role --role-name "$ROLE_NAME" --tags "$PROJECT_TAG_LIST"
_tag_console "log group /aws/lambda/$CONSOLE_FN" \
  aws logs tag-resource --region "$REGION" \
  --resource-arn "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/$CONSOLE_FN" \
  --tags "$PROJECT_TAG_MAP"
_tag_console "http api $API_ID" \
  aws apigatewayv2 tag-resource --region "$REGION" \
  --resource-arn "arn:aws:apigateway:$REGION::/apis/$API_ID" \
  --tags "$PROJECT_TAG_MAP"
if [[ -n "$CONSOLE_UNTAGGED" ]]; then
  echo "WARNING: could not apply project=$PROJECT_TAG to: $CONSOLE_UNTAGGED" >&2
fi

API_URL="$(aws apigatewayv2 get-api --api-id "$API_ID" --region "$REGION" \
  --query ApiEndpoint --output text)"
echo "Console deployed: $API_URL/console"
echo "Remember the authorizer step in this script's header comment before sharing that URL."
