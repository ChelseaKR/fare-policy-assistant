#!/usr/bin/env bash
# Move the stable rider alias back to the retained prior-good Lambda version.
#
# The API Gateway integration never changes during a routine rollback. This
# script validates the retained version first, performs one revision-guarded
# alias update, and restores the displaced version if the public smoke fails.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
LIVE_ALIAS="${FPA_LIVE_ALIAS:-live}"
ROLLBACK_ALIAS="${FPA_ROLLBACK_ALIAS:-rollback}"
API_ID="${FPA_API_ID:-}"
ASSISTANT_BASE_URL="${FPA_ASSISTANT_BASE_URL:-}"
if [[ ${FPA_REQUIRED_DISABLED_DOC_IDS+x} ]]; then
  REQUIRED_DISABLED_DOC_IDS="$FPA_REQUIRED_DISABLED_DOC_IDS"
else
  REQUIRED_DISABLED_DOC_IDS="yolobus-fares"
fi
MAX_SECONDS="${FPA_ROLLBACK_MAX_SECONDS:-900}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START_SECONDS="$(date +%s)"
OPERATION_DEADLINE_EPOCH=0
VERIFICATION_DEADLINE_EPOCH=0
DEADLINE_TMPDIR=""
EMPTY_ALIAS_ROUTING='{"AdditionalVersionWeights":{}}'
RESTORE_GUARD_ACTIVE=false
RESTORE_GUARD_EXPECTED_VERSION=""
RESTORE_GUARD_EXPECTED_REVISION=""
RESTORE_GUARD_EXPECTED_DESCRIPTION=""
RESTORE_GUARD_DISPLACED_VERSION=""

fail() {
  echo "rollback: FAIL: $*" >&2
  exit 1
}

assert_unweighted_alias() {
  local alias_json="$1"
  local alias_name="$2"
  jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
    <<<"$alias_json" >/dev/null \
    || fail "$alias_name has weighted routing; deterministic rollback is unsafe"
}

run_until() {
  local deadline_epoch="$1"
  shift
  local remaining
  local marker
  local command_pid
  local timer_pid
  local status

  remaining=$((deadline_epoch - $(date +%s)))
  ((remaining > 0)) || return 124
  marker="$(mktemp "$DEADLINE_TMPDIR/deadline.XXXXXX")"

  "$@" &
  command_pid=$!
  (
    timer_sleep_pid=""
    trap '
      if [[ -n "$timer_sleep_pid" ]]; then
        kill "$timer_sleep_pid" 2>/dev/null || true
      fi
      exit 0
    ' TERM INT
    sleep "$remaining" &
    timer_sleep_pid=$!
    wait "$timer_sleep_pid" || exit 0
    if kill -0 "$command_pid" 2>/dev/null; then
      echo "expired" >"$marker"
      kill -TERM "$command_pid" 2>/dev/null || true
      kill -KILL "$command_pid" 2>/dev/null || true
    fi
  ) &
  timer_pid=$!

  if wait "$command_pid"; then
    status=0
  else
    status=$?
  fi
  kill "$timer_pid" 2>/dev/null || true
  wait "$timer_pid" 2>/dev/null || true
  if [[ "$(<"$marker")" == "expired" ]] || (( $(date +%s) >= deadline_epoch )); then
    return 124
  fi
  return "$status"
}

aws_until() {
  local deadline_epoch="$1"
  shift
  local remaining
  local connect_timeout

  remaining=$((deadline_epoch - $(date +%s)))
  ((remaining > 0)) || return 124
  connect_timeout="$remaining"
  ((connect_timeout > 10)) && connect_timeout=10
  run_until "$deadline_epoch" \
    aws "$@" \
    --cli-connect-timeout "$connect_timeout" \
    --cli-read-timeout "$remaining"
}

restore_displaced_live() {
  local current_alias
  local current_version
  local current_revision
  local current_description
  local restored_alias

  [[ "$RESTORE_GUARD_ACTIVE" == "true" ]] || return 0
  RESTORE_GUARD_ACTIVE=false
  if ! current_alias="$(
    aws_until "$OPERATION_DEADLINE_EPOCH" lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"; then
    echo "rollback: CRITICAL: could not inspect live for guarded restore" >&2
    return 1
  fi
  current_version="$(jq -r '.FunctionVersion // ""' <<<"$current_alias")"
  current_revision="$(jq -r '.RevisionId // ""' <<<"$current_alias")"
  current_description="$(jq -r '.Description // ""' <<<"$current_alias")"
  if [[ "$current_version" == "$RESTORE_GUARD_DISPLACED_VERSION" ]]; then
    if ! jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
      <<<"$current_alias" >/dev/null; then
      echo "rollback: CRITICAL: displaced primary version was restored but weighted routing remains" >&2
      return 1
    fi
    return 0
  fi
  if [[ "$current_version" != "$RESTORE_GUARD_EXPECTED_VERSION" \
    || "$current_description" != "$RESTORE_GUARD_EXPECTED_DESCRIPTION" \
    || ( -n "$RESTORE_GUARD_EXPECTED_REVISION" \
      && "$current_revision" != "$RESTORE_GUARD_EXPECTED_REVISION" ) ]]; then
    echo "rollback: WARNING: live changed concurrently; guarded restore did not overwrite it" >&2
    return 1
  fi
  echo "rollback: target was not verified; restoring displaced version $RESTORE_GUARD_DISPLACED_VERSION" >&2
  if ! restored_alias="$(
    aws_until "$OPERATION_DEADLINE_EPOCH" lambda update-alias \
      --function-name "$FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$RESTORE_GUARD_DISPLACED_VERSION" \
      --revision-id "$current_revision" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "rollback target unverified; displaced live restored" \
      --region "$REGION" \
      --output json
  )"; then
    echo "rollback: CRITICAL: compare-and-swap restore of live failed" >&2
    return 1
  fi
  if ! jq -e \
    --arg version "$RESTORE_GUARD_DISPLACED_VERSION" '
      .FunctionVersion == $version
      and ((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0
    ' <<<"$restored_alias" >/dev/null; then
    echo "rollback: CRITICAL: restored live failed target/routing verification" >&2
    return 1
  fi
}

rollback_exit_guard() {
  local status=$?
  local guard_was_active=false
  trap - EXIT INT TERM
  if [[ "$RESTORE_GUARD_ACTIVE" == "true" ]]; then
    guard_was_active=true
    restore_displaced_live || true
  fi
  if [[ "$guard_was_active" == "true" && "$status" == "0" ]]; then
    status=1
  fi
  if [[ -n "$DEADLINE_TMPDIR" ]]; then
    rm -rf "$DEADLINE_TMPDIR"
  fi
  exit "$status"
}

trap rollback_exit_guard EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v aws >/dev/null 2>&1 || fail "aws is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"
[[ "$MAX_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "FPA_ROLLBACK_MAX_SECONDS must be positive"
OPERATION_DEADLINE_EPOCH=$((START_SECONDS + MAX_SECONDS))
RESTORE_RESERVE_SECONDS=$((MAX_SECONDS / 3))
((RESTORE_RESERVE_SECONDS < 1)) && RESTORE_RESERVE_SECONDS=1
((RESTORE_RESERVE_SECONDS > 60)) && RESTORE_RESERVE_SECONDS=60
VERIFICATION_DEADLINE_EPOCH=$((OPERATION_DEADLINE_EPOCH - RESTORE_RESERVE_SECONDS))
DEADLINE_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-rollback-deadline.XXXXXX")"
chmod 700 "$DEADLINE_TMPDIR"

LIVE_JSON="$(
  aws_until "$OPERATION_DEADLINE_EPOCH" lambda get-alias \
    --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
)"
ROLLBACK_JSON="$(
  aws_until "$OPERATION_DEADLINE_EPOCH" lambda get-alias \
    --function-name "$FN" --name "$ROLLBACK_ALIAS" --region "$REGION" --output json
)"
assert_unweighted_alias "$LIVE_JSON" "$LIVE_ALIAS"
assert_unweighted_alias "$ROLLBACK_JSON" "$ROLLBACK_ALIAS"
CURRENT_VERSION="$(jq -r '.FunctionVersion' <<<"$LIVE_JSON")"
LIVE_REVISION="$(jq -r '.RevisionId' <<<"$LIVE_JSON")"
TARGET_VERSION="$(jq -r '.FunctionVersion' <<<"$ROLLBACK_JSON")"
ALIAS_ARN="$(jq -r '.AliasArn // ""' <<<"$LIVE_JSON")"
[[ -n "$ALIAS_ARN" ]] || fail "$LIVE_ALIAS has no qualified alias ARN"
ALIAS_INTEGRATION_URI="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$ALIAS_ARN/invocations"

[[ "$CURRENT_VERSION" =~ ^[1-9][0-9]*$ ]] \
  || fail "$LIVE_ALIAS does not target a numbered version"
[[ "$TARGET_VERSION" =~ ^[1-9][0-9]*$ ]] \
  || fail "$ROLLBACK_ALIAS does not target a numbered version"
[[ "$TARGET_VERSION" != "$CURRENT_VERSION" ]] \
  || fail "$ROLLBACK_ALIAS does not retain a distinct prior version"

TARGET_CONFIG="$(
  aws_until "$OPERATION_DEADLINE_EPOCH" lambda get-function-configuration \
    --function-name "$FN" --qualifier "$TARGET_VERSION" \
    --region "$REGION" --output json
)"
TARGET_CORPUS="$(jq -r '.Environment.Variables.FPA_PINNED_CORPUS_VERSION // ""' \
  <<<"$TARGET_CONFIG")"
TARGET_DISABLED="$(jq -r '.Environment.Variables.FPA_DISABLED_DOC_IDS // ""' \
  <<<"$TARGET_CONFIG")"
[[ "$TARGET_CORPUS" =~ ^[0-9a-f]{12}$ ]] \
  || fail "retained version has no valid corpus pin"
TARGET_RUNTIME_MODE="$(
  aws_until "$OPERATION_DEADLINE_EPOCH" lambda get-runtime-management-config \
    --function-name "$FN" --qualifier "$TARGET_VERSION" \
    --region "$REGION" --query UpdateRuntimeOn --output text
)"
[[ "$TARGET_RUNTIME_MODE" == "FunctionUpdate" ]] \
  || fail "retained version is not frozen in FunctionUpdate runtime mode"

IFS=',' read -r -a REQUIRED_DISABLED <<<"$REQUIRED_DISABLED_DOC_IDS"
for document_id in "${REQUIRED_DISABLED[@]}"; do
  [[ -z "$document_id" || ",$TARGET_DISABLED," == *",$document_id,"* ]] \
    || fail "retained version does not contain required disabled document $document_id"
done

"$ROOT/infra/check-lambda-version.sh" \
  --function-name "$FN" \
  --qualifier "$TARGET_VERSION" \
  --expected-corpus "$TARGET_CORPUS" \
  --expected-disabled-docs "$TARGET_DISABLED" \
  --deadline-epoch "$OPERATION_DEADLINE_EPOCH" \
  --region "$REGION"

if [[ -z "$API_ID" ]]; then
  API_IDS="$(
    aws_until "$OPERATION_DEADLINE_EPOCH" apigatewayv2 get-apis --region "$REGION" \
      --query "Items[?Name=='$FN'].ApiId" --output json
  )"
  [[ "$(jq 'length' <<<"$API_IDS")" == "1" ]] \
    || fail "expected exactly one HTTP API named $FN"
  API_ID="$(jq -r '.[0]' <<<"$API_IDS")"
fi
if [[ -z "$ASSISTANT_BASE_URL" ]]; then
  ASSISTANT_BASE_URL="https://$API_ID.execute-api.$REGION.amazonaws.com"
fi

assert_single_live_integration() {
  local deadline_epoch="${1:-$OPERATION_DEADLINE_EPOCH}"
  local integrations
  local integration_count
  local integration_uri

  integrations="$(
    aws_until "$deadline_epoch" apigatewayv2 get-integrations \
      --api-id "$API_ID" --region "$REGION" --query Items --output json
  )"
  integration_count="$(jq 'length' <<<"$integrations")"
  [[ "$integration_count" == "1" ]] \
    || fail "expected exactly one integration on HTTP API $API_ID"
  integration_uri="$(jq -r '.[0].IntegrationUri // ""' <<<"$integrations")"
  [[ "$integration_uri" == "$ALIAS_ARN" \
    || "$integration_uri" == "$ALIAS_INTEGRATION_URI" ]] \
    || fail "HTTP API $API_ID does not target the qualified $LIVE_ALIAS alias"
}

assert_single_live_integration

(( $(date +%s) < VERIFICATION_DEADLINE_EPOCH )) \
  || fail "insufficient time remains to move and verify the rollback target safely"

ROLLBACK_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ROLLBACK_DESCRIPTION="rollback=$ROLLBACK_AT from=$CURRENT_VERSION"
RESTORE_GUARD_EXPECTED_VERSION="$TARGET_VERSION"
RESTORE_GUARD_EXPECTED_REVISION=""
RESTORE_GUARD_EXPECTED_DESCRIPTION="$ROLLBACK_DESCRIPTION"
RESTORE_GUARD_DISPLACED_VERSION="$CURRENT_VERSION"
RESTORE_GUARD_ACTIVE=true
if ! UPDATED_ALIAS="$(
  aws_until "$VERIFICATION_DEADLINE_EPOCH" lambda update-alias \
    --function-name "$FN" \
    --name "$LIVE_ALIAS" \
    --function-version "$TARGET_VERSION" \
    --revision-id "$LIVE_REVISION" \
    --routing-config "$EMPTY_ALIAS_ROUTING" \
    --description "$ROLLBACK_DESCRIPTION" \
    --region "$REGION" \
    --output json
)"; then
  restore_displaced_live || true
  fail "live alias changed concurrently; no rollback was applied"
fi
UPDATED_REVISION="$(jq -r '.RevisionId' <<<"$UPDATED_ALIAS")"
RESTORE_GUARD_EXPECTED_REVISION="$UPDATED_REVISION"
assert_unweighted_alias "$UPDATED_ALIAS" "$LIVE_ALIAS"
[[ "$(jq -r '.FunctionVersion // ""' <<<"$UPDATED_ALIAS")" == "$TARGET_VERSION" ]] \
  || fail "$LIVE_ALIAS did not settle on retained version $TARGET_VERSION"
assert_single_live_integration "$VERIFICATION_DEADLINE_EPOCH"

if ! "$ROOT/scripts/smoke-production.sh" \
  --assistant-only \
  --assistant-base-url "$ASSISTANT_BASE_URL" \
  --expected-disabled-docs "$REQUIRED_DISABLED_DOC_IDS" \
  --deadline-epoch "$VERIFICATION_DEADLINE_EPOCH"; then
  fail "retained version failed the public assistant smoke"
fi
assert_single_live_integration "$VERIFICATION_DEADLINE_EPOCH"
VERIFIED_LIVE_JSON="$(
  aws_until "$VERIFICATION_DEADLINE_EPOCH" lambda get-alias \
    --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
)"
assert_unweighted_alias "$VERIFIED_LIVE_JSON" "$LIVE_ALIAS"
[[ "$(jq -r '.FunctionVersion // ""' <<<"$VERIFIED_LIVE_JSON")" == "$TARGET_VERSION" \
  && "$(jq -r '.RevisionId // ""' <<<"$VERIFIED_LIVE_JSON")" == "$UPDATED_REVISION" ]] \
  || fail "live alias changed before rollback verification completed"

ELAPSED_SECONDS=$(( $(date +%s) - START_SECONDS ))
((ELAPSED_SECONDS < MAX_SECONDS)) \
  || fail "rollback exceeded the ${MAX_SECONDS}s recovery deadline"
RESTORE_GUARD_ACTIVE=false

echo "rollback: PASS: $LIVE_ALIAS moved $CURRENT_VERSION -> $TARGET_VERSION in ${ELAPSED_SECONDS}s"
