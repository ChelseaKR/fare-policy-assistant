#!/usr/bin/env bash
# Deploy the rider assistant as an immutable Lambda version behind a live alias.
#
#   ./infra/deploy.sh            # stage, verify, promote, then print the URL
#
# Requires the AWS CLI with credentials that may manage IAM, Lambda, and
# CloudWatch Logs. Region comes from AWS_REGION (default us-west-2, matching
# CI). See ADR 0018 for the one-time unqualified-route migration and rollback
# state machine.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
LIVE_ALIAS="${FPA_LIVE_ALIAS:-live}"
ROLLBACK_ALIAS="${FPA_ROLLBACK_ALIAS:-rollback}"
ROLE_NAME="$FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/infra/build"
BUNDLE="$BUILD/bundle"
API_ID="${FPA_API_ID:-}"
SOURCE_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
if [[ "${FPA_ALLOW_DIRTY_DEPLOY:-}" != "1" \
  && -n "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]]; then
  echo "working tree is dirty; refusing an untraceable production deploy" >&2
  echo "commit the release or set FPA_ALLOW_DIRTY_DEPLOY=1 for an explicit emergency" >&2
  exit 2
fi

for required_command in aws curl jq openssl uv zip; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "$required_command is required" >&2
    exit 2
  }
done
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

# Preserve operator-owned Lambda settings from the actual live version. AWS
# replaces the entire Variables map on update, so constructing it from only
# this script's three controls would silently erase settings such as
# FPA_EMBED_ANCESTORS. Once the alias exists, never inherit from mutable
# $LATEST: a failed candidate must not poison the next release.
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

assert_unweighted_alias() {
  local alias_json="$1"
  local alias_name="$2"
  jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
    <<<"$alias_json" >/dev/null || {
    echo "Lambda alias $alias_name has weighted routing; refusing deterministic release" >&2
    exit 1
  }
}

EMPTY_ALIAS_ROUTING='{"AdditionalVersionWeights":{}}'
PROMOTION_GUARD_ACTIVE=false
PROMOTION_GUARD_EXPECTED_VERSION=""
PROMOTION_GUARD_EXPECTED_REVISION=""
PROMOTION_GUARD_EXPECTED_DESCRIPTION=""
PROMOTION_GUARD_RESTORE_VERSION=""
ROLLBACK_POINTER_GUARD_ACTIVE=false
ROLLBACK_POINTER_GUARD_EXPECTED_VERSION=""
ROLLBACK_POINTER_GUARD_EXPECTED_REVISION=""
ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION=""
ROLLBACK_POINTER_GUARD_RESTORE_VERSION=""

# Once live has moved, every abnormal exit must attempt a compare-and-swap
# restore until the public route has passed smoke. The version and RevisionId
# checks prevent this cleanup from overwriting a concurrent operator change.
restore_unverified_live() {
  local current_alias
  local current_version
  local current_revision
  local current_description
  local restored_alias

  [[ "$PROMOTION_GUARD_ACTIVE" == "true" ]] || return 0
  PROMOTION_GUARD_ACTIVE=false
  if ! current_alias="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"; then
    echo "CRITICAL: could not inspect live while restoring an unverified release" >&2
    return 1
  fi
  current_version="$(jq -r '.FunctionVersion // ""' <<<"$current_alias")"
  current_revision="$(jq -r '.RevisionId // ""' <<<"$current_alias")"
  current_description="$(jq -r '.Description // ""' <<<"$current_alias")"
  if [[ "$current_version" == "$PROMOTION_GUARD_RESTORE_VERSION" ]]; then
    if ! jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
      <<<"$current_alias" >/dev/null; then
      echo "CRITICAL: live returned to the prior primary version but still has weighted routing" >&2
      return 1
    fi
    return 0
  fi
  if [[ "$current_version" != "$PROMOTION_GUARD_EXPECTED_VERSION" \
    || "$current_description" != "$PROMOTION_GUARD_EXPECTED_DESCRIPTION" \
    || ( -n "$PROMOTION_GUARD_EXPECTED_REVISION" \
      && "$current_revision" != "$PROMOTION_GUARD_EXPECTED_REVISION" ) ]]; then
    echo "WARNING: live changed after promotion; automatic restore did not overwrite it" >&2
    return 1
  fi
  if ! restored_alias="$(
    aws lambda update-alias \
      --function-name "$FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$PROMOTION_GUARD_RESTORE_VERSION" \
      --revision-id "$current_revision" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "automatic restore of unverified $current_version" \
      --region "$REGION" \
      --output json
  )"; then
    echo "CRITICAL: compare-and-swap restore of live failed" >&2
    return 1
  fi
  if ! jq -e \
    --arg version "$PROMOTION_GUARD_RESTORE_VERSION" '
      .FunctionVersion == $version
      and ((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0
    ' <<<"$restored_alias" >/dev/null; then
    echo "CRITICAL: restored live alias failed target/routing verification" >&2
    return 1
  fi
  echo "restored unverified live version $current_version -> $PROMOTION_GUARD_RESTORE_VERSION" >&2
}

restore_previous_rollback_pointer() {
  local current_alias
  local current_version
  local current_revision
  local current_description
  local restored_alias

  [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]] || return 0
  ROLLBACK_POINTER_GUARD_ACTIVE=false
  if ! current_alias="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json
  )"; then
    echo "WARNING: could not inspect the rollback pointer during release cleanup" >&2
    return 1
  fi
  current_version="$(jq -r '.FunctionVersion // ""' <<<"$current_alias")"
  current_revision="$(jq -r '.RevisionId // ""' <<<"$current_alias")"
  current_description="$(jq -r '.Description // ""' <<<"$current_alias")"
  if [[ "$current_version" == "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" ]]; then
    if ! jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
      <<<"$current_alias" >/dev/null; then
      echo "WARNING: rollback pointer returned to its prior target but still has weighted routing" >&2
      return 1
    fi
    return 0
  fi
  if [[ "$current_version" != "$ROLLBACK_POINTER_GUARD_EXPECTED_VERSION" \
    || "$current_description" != "$ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION" \
    || ( -n "$ROLLBACK_POINTER_GUARD_EXPECTED_REVISION" \
      && "$current_revision" != "$ROLLBACK_POINTER_GUARD_EXPECTED_REVISION" ) ]]; then
    echo "WARNING: rollback pointer changed concurrently; cleanup did not overwrite it" >&2
    return 1
  fi
  if ! restored_alias="$(
    aws lambda update-alias \
      --function-name "$FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" \
      --revision-id "$current_revision" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "promotion aborted; prior pointer restored" \
      --region "$REGION" \
      --output json
  )"; then
    echo "WARNING: compare-and-swap restore of the rollback pointer failed" >&2
    return 1
  fi
  if ! jq -e \
    --arg version "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" '
      .FunctionVersion == $version
      and ((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0
    ' <<<"$restored_alias" >/dev/null; then
    echo "WARNING: restored rollback pointer failed target/routing verification" >&2
    return 1
  fi
}

release_exit_guard() {
  local status=$?
  local guard_was_active=false
  trap - EXIT INT TERM
  if [[ "$PROMOTION_GUARD_ACTIVE" == "true" ]]; then
    guard_was_active=true
    restore_unverified_live || true
  fi
  if [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]]; then
    guard_was_active=true
    restore_previous_rollback_pointer || true
  fi
  if [[ "$guard_was_active" == "true" && "$status" == "0" ]]; then
    status=1
  fi
  exit "$status"
}

trap release_exit_guard EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

HAS_LIVE_ALIAS=false
LIVE_ALIAS_JSON=""
BASELINE_LIVE_VERSION=""
BASELINE_LIVE_REVISION=""
if [[ "$FUNCTION_EXISTS" == "true" ]]; then
  if LIVE_ALIAS_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json 2>&1
  )"; then
    HAS_LIVE_ALIAS=true
    assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
    LIVE_VERSION="$(jq -r '.FunctionVersion' <<<"$LIVE_ALIAS_JSON")"
    LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
    [[ "$LIVE_VERSION" =~ ^[1-9][0-9]*$ ]] || {
      echo "$LIVE_ALIAS must target a numbered version, not $LIVE_VERSION" >&2
      exit 1
    }
    [[ -n "$LIVE_REVISION" ]] || {
      echo "$LIVE_ALIAS has no revision id; refusing an unguarded release" >&2
      exit 1
    }
    BASELINE_LIVE_VERSION="$LIVE_VERSION"
    BASELINE_LIVE_REVISION="$LIVE_REVISION"
    EXISTING_LAMBDA_ENV="$(
      aws lambda get-function-configuration \
        --function-name "$FN" --qualifier "$LIVE_VERSION" --region "$REGION" \
        --query 'Environment.Variables' --output json
    )"
  elif [[ "$LIVE_ALIAS_JSON" != *"ResourceNotFoundException"* ]]; then
    echo "could not inspect Lambda alias $LIVE_ALIAS; refusing to deploy:" >&2
    echo "$LIVE_ALIAS_JSON" >&2
    exit 1
  fi
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

# Return the newest numbered version whose complete versioned configuration
# matches a staged candidate. ListVersionsByFunction omits RuntimeVersionConfig,
# so it is only a code-hash shortlist; every possible match is re-read through
# GetFunctionConfiguration before reuse. This makes interrupted releases
# retryable without freezing an older managed-runtime patch by accident.
exact_published_version() {
  local candidate_config="$1"
  local versions_json="$2"
  local candidate_sha
  local version
  local version_config

  candidate_sha="$(jq -r '.CodeSha256' <<<"$candidate_config")"
  for version in $(
    jq -r --arg sha "$candidate_sha" '
      [.Versions[]
       | select((.Version | test("^[1-9][0-9]*$")) and .CodeSha256 == $sha)
       | (.Version | tonumber)]
      | sort
      | reverse[]
    ' <<<"$versions_json"
  ); do
    version_config="$(
      aws lambda get-function-configuration \
        --function-name "$FN" --qualifier "$version" \
        --region "$REGION" --output json
    )"
    if same_versioned_release_config "$candidate_config" "$version_config"; then
      printf '%s\n' "$version"
      return 0
    fi
  done
  return 0
}

# Produce a fail-closed view of configuration that this release does not
# intentionally manage. Unknown future fields stay in the view, so a new AWS
# versioned setting cannot silently hitchhike from mutable $LATEST. Derived
# status/identity fields and the fields explicitly rewritten below are omitted.
unmanaged_config_snapshot() {
  local config_json="$1"
  (
    cd "$ROOT"
    FPA_DEPLOY_CONFIG_JSON="$config_json" uv run python -c '
import json
import os

config = json.loads(os.environ["FPA_DEPLOY_CONFIG_JSON"])
managed_or_derived = {
    "Architectures",
    "CodeSha256",
    "CodeSize",
    "ConfigSha256",
    "Description",
    "Environment",
    "FunctionArn",
    "FunctionName",
    "Handler",
    "LastModified",
    "LastUpdateStatus",
    "LastUpdateStatusReason",
    "LastUpdateStatusReasonCode",
    "MasterArn",
    "MemorySize",
    "RevisionId",
    "Role",
    "Runtime",
    "RuntimeVersionConfig",
    "SigningJobArn",
    "SigningProfileVersionArn",
    "State",
    "StateReason",
    "StateReasonCode",
    "Timeout",
    "Version",
}
snapshot = {
    key: value
    for key, value in config.items()
    if key not in managed_or_derived
}
snapshot["Layers"] = snapshot.get("Layers") or []
snapshot["FileSystemConfigs"] = snapshot.get("FileSystemConfigs") or []
snapshot["KMSKeyArn"] = snapshot.get("KMSKeyArn") or ""
snapshot["DeadLetterConfig"] = snapshot.get("DeadLetterConfig") or {"TargetArn": ""}
snapshot["TracingConfig"] = snapshot.get("TracingConfig") or {"Mode": "PassThrough"}
snapshot["EphemeralStorage"] = snapshot.get("EphemeralStorage") or {"Size": 512}
vpc = snapshot.get("VpcConfig") or {}
snapshot["VpcConfig"] = {
    "SubnetIds": sorted(vpc.get("SubnetIds") or []),
    "SecurityGroupIds": sorted(vpc.get("SecurityGroupIds") or []),
    "Ipv6AllowedForDualStack": bool(vpc.get("Ipv6AllowedForDualStack", False)),
}
snap_start = snapshot.get("SnapStart") or {}
snapshot["SnapStart"] = {"ApplyOn": snap_start.get("ApplyOn", "None")}
logging = snapshot.get("LoggingConfig") or {}
snapshot["LoggingConfig"] = {
    "LogFormat": logging.get("LogFormat", "Text"),
    "LogGroup": logging.get("LogGroup", "/aws/lambda/" + config["FunctionName"]),
    **(
        {"ApplicationLogLevel": logging["ApplicationLogLevel"]}
        if "ApplicationLogLevel" in logging
        else {}
    ),
    **(
        {"SystemLogLevel": logging["SystemLogLevel"]}
        if "SystemLogLevel" in logging
        else {}
    ),
}
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
'
  )
}

assert_same_unmanaged_config() {
  local reviewed_json="$1"
  local candidate_json="$2"
  local context="$3"
  local reviewed_snapshot
  local candidate_snapshot

  reviewed_snapshot="$(unmanaged_config_snapshot "$reviewed_json")"
  candidate_snapshot="$(unmanaged_config_snapshot "$candidate_json")"
  if [[ "$reviewed_snapshot" != "$candidate_snapshot" ]]; then
    echo "$context has unmanaged versioned-configuration drift" >&2
    (
      cd "$ROOT"
      FPA_DEPLOY_REVIEWED_SNAPSHOT="$reviewed_snapshot" \
        FPA_DEPLOY_CANDIDATE_SNAPSHOT="$candidate_snapshot" \
        uv run python -c '
import json
import os

reviewed = json.loads(os.environ["FPA_DEPLOY_REVIEWED_SNAPSHOT"])
candidate = json.loads(os.environ["FPA_DEPLOY_CANDIDATE_SNAPSHOT"])
changed = sorted(
    key
    for key in reviewed.keys() | candidate.keys()
    if reviewed.get(key) != candidate.get(key)
)
print("changed unmanaged fields: " + ", ".join(changed), file=__import__("sys").stderr)
'
    )
    echo "review and reconcile those settings against the immutable live version before deploying" >&2
    exit 1
  fi
}

assert_managed_release_config() {
  local config_json="$1"
  local context="$2"
  local expected_revision="${3:-}"

  if ! jq -e \
    --arg code_sha "$LOCAL_CODE_SHA" \
    --arg role "$ROLE_ARN" \
    --arg revision "$expected_revision" \
    --argjson environment "$LAMBDA_ENV" '
      .CodeSha256 == $code_sha
      and .Runtime == "python3.12"
      and .Role == $role
      and .Handler == "web.handler.handler"
      and .Timeout == 25
      and .MemorySize == 512
      and .Environment == $environment
      and .PackageType == "Zip"
      and .Architectures == ["arm64"]
      and ($revision == "" or .RevisionId == $revision)
    ' <<<"$config_json" >/dev/null; then
    echo "$context does not match the locally built artifact and complete managed configuration" >&2
    exit 1
  fi
}

normalized_release_config() {
  local config_json="$1"
  (
    cd "$ROOT"
    FPA_DEPLOY_RELEASE_CONFIG="$config_json" uv run python -c '
import json
import os

config = json.loads(os.environ["FPA_DEPLOY_RELEASE_CONFIG"])
non_behavioral = {
    "CodeSize",
    "ConfigSha256",
    "Description",
    "FunctionArn",
    "FunctionName",
    "LastModified",
    "LastUpdateStatus",
    "LastUpdateStatusReason",
    "LastUpdateStatusReasonCode",
    "MasterArn",
    "RevisionId",
    "SigningJobArn",
    "SigningProfileVersionArn",
    "State",
    "StateReason",
    "StateReasonCode",
    "Version",
}
snapshot = {
    key: value
    for key, value in config.items()
    if key not in non_behavioral
}
snapshot["Layers"] = snapshot.get("Layers") or []
snapshot["FileSystemConfigs"] = snapshot.get("FileSystemConfigs") or []
snapshot["KMSKeyArn"] = snapshot.get("KMSKeyArn") or ""
snapshot["DeadLetterConfig"] = snapshot.get("DeadLetterConfig") or {"TargetArn": ""}
snapshot["TracingConfig"] = snapshot.get("TracingConfig") or {"Mode": "PassThrough"}
snapshot["EphemeralStorage"] = snapshot.get("EphemeralStorage") or {"Size": 512}
vpc = snapshot.get("VpcConfig") or {}
snapshot["VpcConfig"] = {
    "SubnetIds": sorted(vpc.get("SubnetIds") or []),
    "SecurityGroupIds": sorted(vpc.get("SecurityGroupIds") or []),
    "Ipv6AllowedForDualStack": bool(vpc.get("Ipv6AllowedForDualStack", False)),
}
snap_start = snapshot.get("SnapStart") or {}
snapshot["SnapStart"] = {"ApplyOn": snap_start.get("ApplyOn", "None")}
logging = snapshot.get("LoggingConfig") or {}
snapshot["LoggingConfig"] = {
    "LogFormat": logging.get("LogFormat", "Text"),
    "LogGroup": logging.get("LogGroup", "/aws/lambda/" + config["FunctionName"]),
    **(
        {"ApplicationLogLevel": logging["ApplicationLogLevel"]}
        if "ApplicationLogLevel" in logging
        else {}
    ),
    **(
        {"SystemLogLevel": logging["SystemLogLevel"]}
        if "SystemLogLevel" in logging
        else {}
    ),
}
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
'
  )
}

same_versioned_release_config() {
  local first_snapshot
  local second_snapshot

  first_snapshot="$(normalized_release_config "$1")"
  second_snapshot="$(normalized_release_config "$2")"
  [[ "$first_snapshot" == "$second_snapshot" ]]
}

# ── stable alias and one-time route migration ───────────────────────────────
ALIAS_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$FN:$LIVE_ALIAS"
UNQUALIFIED_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$FN"
ALIAS_INTEGRATION_URI="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$ALIAS_ARN/invocations"
UNQUALIFIED_INTEGRATION_URI="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$UNQUALIFIED_ARN/invocations"
API_EXISTS=false
INTEGRATION_ID=""
INTEGRATION_URI=""

discover_api() {
  local api_ids
  local api_count

  if [[ -n "$API_ID" ]]; then
    aws apigatewayv2 get-api --api-id "$API_ID" --region "$REGION" >/dev/null
    API_EXISTS=true
    return
  fi

  api_ids="$(
    aws apigatewayv2 get-apis --region "$REGION" \
      --query "Items[?Name=='$FN'].ApiId" --output json
  )"
  api_count="$(jq 'length' <<<"$api_ids")"
  if [[ "$api_count" == "0" ]]; then
    API_EXISTS=false
  elif [[ "$api_count" == "1" ]]; then
    API_ID="$(jq -r '.[0]' <<<"$api_ids")"
    API_EXISTS=true
  else
    echo "found multiple HTTP APIs named $FN; set FPA_API_ID explicitly" >&2
    exit 1
  fi
}

refresh_integration() {
  local integrations
  local integration_count

  INTEGRATION_ID=""
  INTEGRATION_URI=""
  [[ "$API_EXISTS" == "true" ]] || return
  integrations="$(
    aws apigatewayv2 get-integrations \
      --api-id "$API_ID" --region "$REGION" --query Items --output json
  )"
  integration_count="$(jq 'length' <<<"$integrations")"
  [[ "$integration_count" == "1" ]] || {
    echo "expected exactly one integration on HTTP API $API_ID" >&2
    exit 1
  }
  INTEGRATION_ID="$(jq -r '.[0].IntegrationId' <<<"$integrations")"
  INTEGRATION_URI="$(jq -r '.[0].IntegrationUri' <<<"$integrations")"
}

integration_targets_live_alias() {
  [[ "$INTEGRATION_URI" == "$ALIAS_ARN" \
    || "$INTEGRATION_URI" == "$ALIAS_INTEGRATION_URI" ]]
}

integration_targets_unqualified_function() {
  [[ "$INTEGRATION_URI" == "$UNQUALIFIED_ARN" \
    || "$INTEGRATION_URI" == "$UNQUALIFIED_INTEGRATION_URI" ]]
}

ensure_alias_permission() {
  local source_arn="arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*"
  local alias_before
  local alias_after
  local alias_before_snapshot
  local alias_after_snapshot
  local alias_before_version
  local alias_before_revision
  local permission_revision
  local policy_response

  alias_before="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$alias_before" "$LIVE_ALIAS"
  alias_before_version="$(jq -r '.FunctionVersion // ""' <<<"$alias_before")"
  alias_before_revision="$(jq -r '.RevisionId // ""' <<<"$alias_before")"
  [[ "$alias_before_version" =~ ^[1-9][0-9]*$ && -n "$alias_before_revision" ]] || {
    echo "$LIVE_ALIAS has no guarded numbered target for API permission setup" >&2
    exit 1
  }
  if [[ -n "$BASELINE_LIVE_VERSION" \
    && ( "$alias_before_version" != "$BASELINE_LIVE_VERSION" \
      || "$alias_before_revision" != "$BASELINE_LIVE_REVISION" ) ]]; then
    echo "live alias changed before API permission setup; refusing to mix release baselines" >&2
    exit 1
  fi
  alias_before_snapshot="$(
    jq -S -c '{
      AliasArn,
      Name,
      FunctionVersion,
      Description: (.Description // ""),
      RoutingConfig: {
        AdditionalVersionWeights:
          (.RoutingConfig.AdditionalVersionWeights // {})
      }
    }' <<<"$alias_before"
  )"
  BASELINE_LIVE_VERSION="$alias_before_version"
  BASELINE_LIVE_REVISION="$alias_before_revision"

  if policy_response="$(
    aws lambda get-policy \
      --function-name "$FN" --qualifier "$LIVE_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    if jq -e \
      --arg source "$source_arn" \
      --arg resource "$ALIAS_ARN" '
        .Policy
        | fromjson
        | any(.Statement[];
            .Sid == "apigw-live"
            and .Effect == "Allow"
            and .Action == "lambda:InvokeFunction"
            and .Resource == $resource
            and .Principal.Service == "apigateway.amazonaws.com"
            and .Condition.ArnLike["AWS:SourceArn"] == $source)
      ' <<<"$policy_response" >/dev/null; then
      return
    fi
    if jq -e '
      .Policy
      | fromjson
      | any(.Statement[]; .Sid == "apigw-live")
    ' <<<"$policy_response" >/dev/null; then
      echo "alias policy statement apigw-live exists but does not match the reviewed API permission" >&2
      echo "remove or repair that statement explicitly before deploying" >&2
      exit 1
    fi
  elif [[ "$policy_response" != *"ResourceNotFoundException"* ]]; then
    echo "could not inspect the $LIVE_ALIAS alias policy:" >&2
    echo "$policy_response" >&2
    exit 1
  fi

  # A qualified resource-policy mutation advances the same revision reported
  # by GetAlias/GetPolicy. Guard the write with the pre-policy revision, then
  # adopt only the new revision that both APIs report for unchanged routing.
  aws lambda add-permission \
    --function-name "$FN" \
    --qualifier "$LIVE_ALIAS" \
    --statement-id apigw-live \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-account "$ACCOUNT" \
    --source-arn "$source_arn" \
    --revision-id "$alias_before_revision" \
    --region "$REGION" >/dev/null
  policy_response="$(
    aws lambda get-policy \
      --function-name "$FN" --qualifier "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  jq -e \
    --arg source "$source_arn" \
    --arg resource "$ALIAS_ARN" '
      .Policy
      | fromjson
      | any(.Statement[];
          .Sid == "apigw-live"
          and .Effect == "Allow"
          and .Action == "lambda:InvokeFunction"
          and .Resource == $resource
          and .Principal.Service == "apigateway.amazonaws.com"
          and .Condition.ArnLike["AWS:SourceArn"] == $source)
    ' <<<"$policy_response" >/dev/null || {
    echo "alias permission apigw-live was not installed with the reviewed scope" >&2
    exit 1
  }
  permission_revision="$(jq -r '.RevisionId // ""' <<<"$policy_response")"
  [[ -n "$permission_revision" ]] || {
    echo "alias permission installation returned no revision id" >&2
    exit 1
  }
  alias_after="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$alias_after" "$LIVE_ALIAS"
  alias_after_snapshot="$(
    jq -S -c '{
      AliasArn,
      Name,
      FunctionVersion,
      Description: (.Description // ""),
      RoutingConfig: {
        AdditionalVersionWeights:
          (.RoutingConfig.AdditionalVersionWeights // {})
      }
    }' <<<"$alias_after"
  )"
  if [[ "$alias_after_snapshot" != "$alias_before_snapshot" \
    || "$(jq -r '.RevisionId // ""' <<<"$alias_after")" != "$permission_revision" ]]; then
    echo "live alias changed outside the guarded API permission mutation" >&2
    exit 1
  fi
  LIVE_VERSION="$alias_before_version"
  LIVE_REVISION="$permission_revision"
  BASELINE_LIVE_VERSION="$alias_before_version"
  BASELINE_LIVE_REVISION="$permission_revision"
}

remove_unqualified_api_permission() {
  local removal
  local remaining_policy

  if ! removal="$(
    aws lambda remove-permission \
      --function-name "$FN" --statement-id apigw \
      --region "$REGION" 2>&1
  )"; then
    [[ "$removal" == *"ResourceNotFoundException"* ]] || {
      echo "alias route is live, but the old unqualified permission could not be removed:" >&2
      echo "$removal" >&2
      exit 1
    }
  fi
  if remaining_policy="$(
    aws lambda get-policy \
      --function-name "$FN" --region "$REGION" --output json 2>&1
  )"; then
    if jq -e '.Policy | fromjson | any(.Statement[]; .Sid == "apigw")' \
      <<<"$remaining_policy" >/dev/null; then
      echo "old unqualified API Gateway permission is still present" >&2
      exit 1
    fi
  elif [[ "$remaining_policy" != *"ResourceNotFoundException"* ]]; then
    echo "could not verify removal of the old unqualified permission:" >&2
    echo "$remaining_policy" >&2
    exit 1
  fi
}

ensure_initial_rollback_alias() {
  local target_version="$1"
  local description="$2"
  local rollback_json
  local rollback_version
  local rollback_revision

  if rollback_json="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
    rollback_version="$(jq -r '.FunctionVersion // ""' <<<"$rollback_json")"
    rollback_revision="$(jq -r '.RevisionId // ""' <<<"$rollback_json")"
    [[ "$rollback_version" =~ ^[1-9][0-9]*$ && -n "$rollback_revision" ]] || {
      echo "$ROLLBACK_ALIAS is not a valid revision-guarded numbered alias" >&2
      exit 1
    }
    if [[ "$rollback_version" != "$target_version" ]]; then
      rollback_json="$(
        aws lambda update-alias \
          --function-name "$FN" \
          --name "$ROLLBACK_ALIAS" \
          --function-version "$target_version" \
          --revision-id "$rollback_revision" \
          --routing-config "$EMPTY_ALIAS_ROUTING" \
          --description "$description" \
          --region "$REGION" \
          --output json
      )"
    fi
  elif [[ "$rollback_json" == *"ResourceNotFoundException"* ]]; then
    rollback_json="$(
      aws lambda create-alias \
        --function-name "$FN" \
        --name "$ROLLBACK_ALIAS" \
        --function-version "$target_version" \
        --routing-config "$EMPTY_ALIAS_ROUTING" \
        --description "$description" \
        --region "$REGION" \
        --output json
    )"
  else
    echo "could not inspect bootstrap rollback alias:" >&2
    echo "$rollback_json" >&2
    exit 1
  fi
  assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$rollback_json")" == "$target_version" ]] || {
    echo "$ROLLBACK_ALIAS did not settle on bootstrap version $target_version" >&2
    exit 1
  }
}

ensure_rollback_alias_exists() {
  local live_version="$1"
  local rollback_json
  local rollback_version

  if rollback_json="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
    rollback_version="$(jq -r '.FunctionVersion // ""' <<<"$rollback_json")"
    [[ "$rollback_version" =~ ^[1-9][0-9]*$ ]] || {
      echo "$ROLLBACK_ALIAS does not target a numbered version" >&2
      exit 1
    }
    return
  fi
  if [[ "$rollback_json" != *"ResourceNotFoundException"* ]]; then
    echo "could not inspect rollback alias:" >&2
    echo "$rollback_json" >&2
    exit 1
  fi

  # This is the interrupted-bootstrap case: live exists but its companion
  # pointer does not. Creating it at live is idempotent and does not overwrite
  # a valid distinct steady-state rollback target.
  rollback_json="$(
    aws lambda create-alias \
      --function-name "$FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$live_version" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "recovered missing rollback pointer at live" \
      --region "$REGION" \
      --output json
  )"
  assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
}

public_assistant_smoke() {
  local expected_disabled_docs="$1"
  "$ROOT/scripts/smoke-production.sh" \
    --assistant-only \
    --assistant-base-url "https://$API_ID.execute-api.$REGION.amazonaws.com" \
    --expected-disabled-docs "$expected_disabled_docs"
}

ensure_api_targets_live() {
  local expected_disabled_docs="$1"
  local expected_source_revision="${2:-}"
  local original_uri
  local observed_uri
  local live_config
  local live_corpus
  local live_disabled
  local source_before_config
  local source_before_revision
  local source_pre_cutover_config
  local source_pre_cutover_revision
  local source_after_config=""
  local source_after_revision
  local restored_disabled_docs
  local migration_failure=""
  local attempt

  discover_api
  if [[ "$API_EXISTS" != "true" ]]; then
    API_ID="$(
      aws apigatewayv2 create-api \
        --region "$REGION" \
        --name "$FN" \
        --protocol-type HTTP \
        --target "$ALIAS_ARN" \
        --query ApiId --output text
    )"
    API_EXISTS=true
  fi
  refresh_integration
  ensure_alias_permission

  if integration_targets_live_alias; then
    # The route and qualified permission are both present. Prove the public
    # service works before removing any obsolete unqualified permission left
    # by an interrupted migration.
    public_assistant_smoke "$expected_disabled_docs"
    remove_unqualified_api_permission
    return
  fi
  if ! integration_targets_unqualified_function; then
    echo "HTTP API $API_ID targets unexpected integration $INTEGRATION_URI" >&2
    exit 1
  fi

  # A partially completed bootstrap can leave the alias in place while the
  # API still targets $LATEST. Revalidate the alias target before completing
  # that migration; no candidate is allowed to reach traffic implicitly.
  LIVE_ALIAS_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  LIVE_VERSION="$(jq -r '.FunctionVersion' <<<"$LIVE_ALIAS_JSON")"
  LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
  if [[ -n "$BASELINE_LIVE_VERSION" \
    && ( "$LIVE_VERSION" != "$BASELINE_LIVE_VERSION" \
      || "$LIVE_REVISION" != "$BASELINE_LIVE_REVISION" ) ]]; then
    echo "live alias changed during route reconciliation; refusing to mix release baselines" >&2
    exit 1
  fi
  live_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --qualifier "$LIVE_VERSION" \
      --region "$REGION" --output json
  )"
  live_corpus="$(jq -r '.Environment.Variables.FPA_PINNED_CORPUS_VERSION // ""' \
    <<<"$live_config")"
  live_disabled="$(jq -r '.Environment.Variables.FPA_DISABLED_DOC_IDS // ""' \
    <<<"$live_config")"
  expected_disabled_docs="$live_disabled"
  source_before_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  source_before_revision="$(jq -r '.RevisionId // ""' <<<"$source_before_config")"
  [[ -n "$source_before_revision" ]] || {
    echo "unqualified bootstrap source has no revision id" >&2
    exit 1
  }
  if [[ -n "$expected_source_revision" \
    && "$source_before_revision" != "$expected_source_revision" ]]; then
    echo "mutable \$LATEST changed after the bootstrap snapshot; route was not migrated" >&2
    exit 1
  fi
  if ! same_versioned_release_config "$live_config" "$source_before_config"; then
    echo "mutable \$LATEST no longer matches the frozen live alias; route was not migrated" >&2
    exit 1
  fi
  expected_source_revision="$source_before_revision"
  "$ROOT/infra/check-lambda-version.sh" \
    --function-name "$FN" \
    --qualifier "$LIVE_VERSION" \
    --expected-corpus "$live_corpus" \
    --expected-disabled-docs "$live_disabled" \
    --region "$REGION"

  source_pre_cutover_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  source_pre_cutover_revision="$(
    jq -r '.RevisionId // ""' <<<"$source_pre_cutover_config"
  )"
  if [[ "$source_pre_cutover_revision" != "$expected_source_revision" ]] \
    || ! same_versioned_release_config "$live_config" "$source_pre_cutover_config"; then
    echo "mutable \$LATEST changed during bootstrap verification; route was not migrated" >&2
    exit 1
  fi

  original_uri="$INTEGRATION_URI"
  aws apigatewayv2 update-integration \
    --api-id "$API_ID" \
    --integration-id "$INTEGRATION_ID" \
    --integration-uri "$ALIAS_INTEGRATION_URI" \
    --region "$REGION" >/dev/null

  observed_uri=""
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    : "$attempt"
    observed_uri="$(
      aws apigatewayv2 get-integration \
        --api-id "$API_ID" --integration-id "$INTEGRATION_ID" \
        --region "$REGION" --query IntegrationUri --output text
    )"
    [[ "$observed_uri" == "$ALIAS_INTEGRATION_URI" ]] && break
    sleep 1
  done
  if [[ "$observed_uri" != "$ALIAS_INTEGRATION_URI" ]]; then
    migration_failure="qualified integration did not settle"
  elif ! public_assistant_smoke "$expected_disabled_docs"; then
    migration_failure="qualified route failed public smoke"
  elif ! source_after_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"; then
    migration_failure="could not revalidate the unqualified bootstrap source"
  else
    source_after_revision="$(jq -r '.RevisionId // ""' <<<"$source_after_config")"
    if [[ "$source_after_revision" != "$expected_source_revision" ]] \
      || ! same_versioned_release_config "$live_config" "$source_after_config"; then
      migration_failure="unqualified bootstrap source changed during route cutover"
    fi
  fi
  if [[ -n "$migration_failure" ]]; then
    echo "alias route migration failed ($migration_failure); restoring the unqualified integration" >&2
    aws apigatewayv2 update-integration \
      --api-id "$API_ID" \
      --integration-id "$INTEGRATION_ID" \
      --integration-uri "$original_uri" \
      --region "$REGION" >/dev/null
    restored_disabled_docs="$expected_disabled_docs"
    if [[ -n "$source_after_config" ]]; then
      restored_disabled_docs="$(
        jq -r '.Environment.Variables.FPA_DISABLED_DOC_IDS // ""' \
          <<<"$source_after_config"
      )"
    fi
    public_assistant_smoke "$restored_disabled_docs" >/dev/null \
      || echo "WARNING: restored integration also failed public smoke" >&2
    exit 1
  fi
  INTEGRATION_URI="$ALIAS_INTEGRATION_URI"
  remove_unqualified_api_permission
}

# Existing deployments originally exposed the mutable, unqualified function.
# Freeze that exact production state and migrate the route before staging any
# new code, configuration, IAM, or shared infrastructure.
if [[ "$FUNCTION_EXISTS" == "true" && "$HAS_LIVE_ALIAS" != "true" ]]; then
  BOOTSTRAP_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  EARLY_BOOTSTRAP_ENV="$(
    jq -S -c 'if type == "object" then . else {} end' <<<"$EXISTING_LAMBDA_ENV"
  )"
  BOOTSTRAP_ENV="$(jq -S -c '.Environment.Variables // {}' <<<"$BOOTSTRAP_CONFIG")"
  [[ "$BOOTSTRAP_ENV" == "$EARLY_BOOTSTRAP_ENV" ]] || {
    echo "mutable \$LATEST environment changed during alias bootstrap; refusing mixed snapshots" >&2
    exit 1
  }
  BOOTSTRAP_CODE_SHA="$(jq -r '.CodeSha256' <<<"$BOOTSTRAP_CONFIG")"
  BOOTSTRAP_REVISION="$(jq -r '.RevisionId' <<<"$BOOTSTRAP_CONFIG")"
  BOOTSTRAP_CORPUS="$(jq -r '.Environment.Variables.FPA_PINNED_CORPUS_VERSION // ""' \
    <<<"$BOOTSTRAP_CONFIG")"
  BOOTSTRAP_DISABLED="$(jq -r '.Environment.Variables.FPA_DISABLED_DOC_IDS // ""' \
    <<<"$BOOTSTRAP_CONFIG")"
  [[ "$BOOTSTRAP_CORPUS" =~ ^[0-9a-f]{12}$ ]] || {
    echo "existing production has no valid corpus pin; refusing alias bootstrap" >&2
    exit 1
  }
  BOOTSTRAP_PUBLISHED_VERSIONS="$(
    aws lambda list-versions-by-function \
      --function-name "$FN" --region "$REGION" --output json
  )"
  # A retry may follow a successful publish but a failed health check or alias
  # creation. Lambda will not publish the unchanged snapshot again, so recover
  # only an exact numbered configuration match; a code-hash-only match is not
  # sufficient because environment and runtime settings are versioned too.
  BOOTSTRAP_MATCHING_VERSION="$(
    exact_published_version "$BOOTSTRAP_CONFIG" "$BOOTSTRAP_PUBLISHED_VERSIONS"
  )"
  if [[ -n "$BOOTSTRAP_MATCHING_VERSION" ]]; then
    BOOTSTRAP_VERSION="$BOOTSTRAP_MATCHING_VERSION"
    echo "reusing exact pre-alias production version $BOOTSTRAP_VERSION"
  else
    BOOTSTRAP_VERSION="$(
      aws lambda publish-version \
        --function-name "$FN" \
        --code-sha256 "$BOOTSTRAP_CODE_SHA" \
        --revision-id "$BOOTSTRAP_REVISION" \
        --description "bootstrap pre-alias live $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --region "$REGION" \
        --query Version --output text
    )"
  fi
  aws lambda wait published-version-active \
    --function-name "$FN" --qualifier "$BOOTSTRAP_VERSION" --region "$REGION"
  aws lambda put-runtime-management-config \
    --function-name "$FN" \
    --qualifier "$BOOTSTRAP_VERSION" \
    --update-runtime-on FunctionUpdate \
    --region "$REGION" >/dev/null
  BOOTSTRAP_RUNTIME_MODE="$(
    aws lambda get-runtime-management-config \
      --function-name "$FN" --qualifier "$BOOTSTRAP_VERSION" \
      --region "$REGION" --query UpdateRuntimeOn --output text
  )"
  [[ "$BOOTSTRAP_RUNTIME_MODE" == "FunctionUpdate" ]] || {
    echo "bootstrap version runtime mode was not frozen at FunctionUpdate" >&2
    exit 1
  }
  "$ROOT/infra/check-lambda-version.sh" \
    --function-name "$FN" \
    --qualifier "$BOOTSTRAP_VERSION" \
    --expected-corpus "$BOOTSTRAP_CORPUS" \
    --expected-disabled-docs "$BOOTSTRAP_DISABLED" \
    --region "$REGION"
  LIVE_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$BOOTSTRAP_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "stable rider traffic" \
      --region "$REGION" \
      --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  ensure_initial_rollback_alias \
    "$BOOTSTRAP_VERSION" "retained prior-good rider version"
  HAS_LIVE_ALIAS=true
  LIVE_VERSION="$BOOTSTRAP_VERSION"
  BASELINE_LIVE_VERSION="$BOOTSTRAP_VERSION"
  BASELINE_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
  [[ -n "$BASELINE_LIVE_REVISION" ]] || {
    echo "$LIVE_ALIAS bootstrap response had no revision id" >&2
    exit 1
  }
  ensure_api_targets_live "$BOOTSTRAP_DISABLED" "$BOOTSTRAP_REVISION"
  echo "bootstrapped immutable live alias at version $BOOTSTRAP_VERSION"
elif [[ "$HAS_LIVE_ALIAS" == "true" ]]; then
  # This is a no-op in steady state and safely completes a route migration
  # interrupted after alias creation but before API Gateway cutover.
  ensure_rollback_alias_exists "$LIVE_VERSION"
  ensure_api_targets_live "$EXISTING_DISABLED_DOC_IDS"
fi

# The immutable live version is the reviewed baseline for every setting that
# this script does not own. Abort before bundle/IAM/Lambda mutation if mutable
# $LATEST differs in layers, networking, DLQ, tracing, KMS, EFS, ephemeral
# storage, SnapStart, logging, or any future unrecognised configuration field.
LIVE_REVIEWED_CONFIG=""
if [[ "$FUNCTION_EXISTS" == "true" && "$HAS_LIVE_ALIAS" == "true" ]]; then
  LIVE_REVIEWED_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --qualifier "$BASELINE_LIVE_VERSION" \
      --region "$REGION" --output json
  )"
  LATEST_REVIEW_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  assert_same_unmanaged_config \
    "$LIVE_REVIEWED_CONFIG" "$LATEST_REVIEW_CONFIG" "mutable \$LATEST"
fi

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
LOCAL_CODE_SHA="$(
  openssl dgst -sha256 -binary "$BUILD/bundle.zip" \
    | openssl base64 -A
)"
[[ "$LOCAL_CODE_SHA" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
  echo "could not compute the AWS-style SHA-256 for the local deployment bundle" >&2
  exit 1
}

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

ROLE_CREATED=false
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  ROLE_CREATED=true
  echo "created role $ROLE_NAME; waiting for IAM propagation"
  sleep 10
fi
if [[ "$ROLE_CREATED" == "true" ]]; then
  aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "$FN-policy" --policy-document "$POLICY"
else
  if EXISTING_ROLE_POLICY="$(
    aws iam get-role-policy \
      --role-name "$ROLE_NAME" --policy-name "$FN-policy" --output json 2>&1
  )"; then
    ROLE_POLICY_EXISTS=true
    EXISTING_ROLE_POLICY_DOCUMENT="$(jq -c '.PolicyDocument' <<<"$EXISTING_ROLE_POLICY")"
    if jq -e --argjson desired "$POLICY" '.PolicyDocument == $desired' \
      <<<"$EXISTING_ROLE_POLICY" >/dev/null; then
      ROLE_POLICY_MATCHES=true
    else
      ROLE_POLICY_MATCHES=false
    fi
  elif [[ "$EXISTING_ROLE_POLICY" == *"NoSuchEntity"* ]]; then
    ROLE_POLICY_EXISTS=false
    ROLE_POLICY_MATCHES=false
    EXISTING_ROLE_POLICY_DOCUMENT=""
  else
    echo "could not inspect shared Lambda execution policy:" >&2
    echo "$EXISTING_ROLE_POLICY" >&2
    exit 1
  fi

  if [[ "$ROLE_POLICY_MATCHES" != "true" ]]; then
    [[ "${FPA_ALLOW_SHARED_IAM_CHANGE:-}" == "1" ]] || {
      echo "shared IAM policy drift detected; alias rollback cannot recover it" >&2
      echo "review separately, then set FPA_ALLOW_SHARED_IAM_CHANGE=1 explicitly" >&2
      exit 1
    }
    aws iam put-role-policy --role-name "$ROLE_NAME" \
      --policy-name "$FN-policy" --policy-document "$POLICY"
    if [[ "$FUNCTION_EXISTS" == "true" ]] \
      && ! public_assistant_smoke "$EXISTING_DISABLED_DOC_IDS"; then
      echo "shared IAM migration broke the live release; restoring the prior policy" >&2
      if [[ "$ROLE_POLICY_EXISTS" == "true" ]]; then
        aws iam put-role-policy --role-name "$ROLE_NAME" \
          --policy-name "$FN-policy" \
          --policy-document "$EXISTING_ROLE_POLICY_DOCUMENT"
      else
        aws iam delete-role-policy \
          --role-name "$ROLE_NAME" --policy-name "$FN-policy"
      fi
      public_assistant_smoke "$EXISTING_DISABLED_DOC_IDS" >/dev/null \
        || echo "CRITICAL: live smoke still fails after IAM policy restoration" >&2
      exit 1
    fi
  fi
fi
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"

# ── Lambda ───────────────────────────────────────────────────────────────────
OLD_VERSION=""
OLD_LIVE_REVISION=""
if [[ "$HAS_LIVE_ALIAS" == "true" ]]; then
  LIVE_ALIAS_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  OBSERVED_LIVE_VERSION="$(jq -r '.FunctionVersion' <<<"$LIVE_ALIAS_JSON")"
  OBSERVED_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
  [[ "$OBSERVED_LIVE_VERSION" == "$BASELINE_LIVE_VERSION" \
    && "$OBSERVED_LIVE_REVISION" == "$BASELINE_LIVE_REVISION" ]] || {
    echo "live alias changed during deployment; refusing to mix release baselines" >&2
    exit 1
  }
  OLD_VERSION="$BASELINE_LIVE_VERSION"
  OLD_LIVE_REVISION="$BASELINE_LIVE_REVISION"
fi

if [[ "$FUNCTION_EXISTS" == "true" ]]; then
  # Keep a break-glass copy of the actual alias target, not mutable $LATEST.
  # Normal rollback moves the alias; this encrypted-at-rest AWS snapshot is a
  # second recovery path if version state is damaged manually.
  ROLLBACK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-rollback.XXXXXX")"
  chmod 700 "$ROLLBACK_DIR"
  if [[ -n "$OLD_VERSION" ]]; then
    aws lambda get-function-configuration \
      --function-name "$FN" --qualifier "$OLD_VERSION" --region "$REGION" \
      >"$ROLLBACK_DIR/configuration.json"
    PREVIOUS_CODE_URL="$(
      aws lambda get-function \
        --function-name "$FN" --qualifier "$OLD_VERSION" --region "$REGION" \
        --query Code.Location --output text
    )"
  else
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" \
      >"$ROLLBACK_DIR/configuration.json"
    PREVIOUS_CODE_URL="$(
      aws lambda get-function --function-name "$FN" --region "$REGION" \
        --query Code.Location --output text
    )"
  fi
  curl --silent --show-error --fail --location "$PREVIOUS_CODE_URL" \
    --output "$ROLLBACK_DIR/function.zip"
  chmod 600 "$ROLLBACK_DIR/configuration.json" "$ROLLBACK_DIR/function.zip"
  echo "saved pre-deploy rollback artifact: $ROLLBACK_DIR"

  # Stage configuration and code only on $LATEST. The public route resolves a
  # numbered alias, so neither half-applied state is rider-visible.
  PRESTAGE_LATEST_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  if [[ -n "$LIVE_REVIEWED_CONFIG" ]]; then
    assert_same_unmanaged_config \
      "$LIVE_REVIEWED_CONFIG" "$PRESTAGE_LATEST_CONFIG" \
      "mutable \$LATEST before staging"
  fi
  LATEST_REVISION="$(jq -r '.RevisionId // ""' <<<"$PRESTAGE_LATEST_CONFIG")"
  [[ -n "$LATEST_REVISION" ]] || {
    echo "mutable \$LATEST has no revision id; refusing to stage" >&2
    exit 1
  }
  STAGED_CONFIG_RESPONSE="$(
    aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
      --runtime python3.12 --handler web.handler.handler \
      --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
      --revision-id "$LATEST_REVISION" \
      --environment "$LAMBDA_ENV" \
      --output json
  )"
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  # Lambda may advance RevisionId once more while the asynchronous
  # configuration update settles. Re-read the completed snapshot and use that
  # revision for the code CAS; the update response's revision can be stale.
  SETTLED_CONFIG_RESPONSE="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --region "$REGION" --output json
  )"
  if ! same_versioned_release_config \
    "$STAGED_CONFIG_RESPONSE" "$SETTLED_CONFIG_RESPONSE"; then
    echo "mutable \$LATEST changed while configuration staging settled" >&2
    exit 1
  fi
  LATEST_REVISION="$(jq -r '.RevisionId // ""' <<<"$SETTLED_CONFIG_RESPONSE")"
  [[ -n "$LATEST_REVISION" ]] || {
    echo "settled configuration has no revision id" >&2
    exit 1
  }
  STAGED_CODE_RESPONSE="$(
    aws lambda update-function-code --function-name "$FN" --region "$REGION" \
      --architectures arm64 \
      --revision-id "$LATEST_REVISION" \
      --zip-file "fileb://$BUILD/bundle.zip" \
      --output json
  )"
  EXPECTED_CANDIDATE_REVISION="$(jq -r '.RevisionId // ""' <<<"$STAGED_CODE_RESPONSE")"
  [[ -n "$EXPECTED_CANDIDATE_REVISION" ]] || {
    echo "code staging returned no revision id" >&2
    exit 1
  }
  assert_managed_release_config \
    "$STAGED_CODE_RESPONSE" "code staging response" "$EXPECTED_CANDIDATE_REVISION"
else
  STAGED_CODE_RESPONSE="$(
    aws lambda create-function --function-name "$FN" --region "$REGION" \
      --runtime python3.12 --handler web.handler.handler --architectures arm64 \
      --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
      --environment "$LAMBDA_ENV" \
      --zip-file "fileb://$BUILD/bundle.zip" \
      --output json
  )"
  EXPECTED_CANDIDATE_REVISION="$(jq -r '.RevisionId // ""' <<<"$STAGED_CODE_RESPONSE")"
  [[ -n "$EXPECTED_CANDIDATE_REVISION" ]] || {
    echo "function creation returned no revision id" >&2
    exit 1
  }
  assert_managed_release_config \
    "$STAGED_CODE_RESPONSE" "function creation response" "$EXPECTED_CANDIDATE_REVISION"
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION"

CANDIDATE_CONFIG="$(
  aws lambda get-function-configuration \
    --function-name "$FN" --region "$REGION" --output json
)"
if [[ -n "$LIVE_REVIEWED_CONFIG" ]]; then
  assert_same_unmanaged_config \
    "$LIVE_REVIEWED_CONFIG" "$CANDIDATE_CONFIG" "staged candidate"
fi
CANDIDATE_CODE_SHA="$(jq -r '.CodeSha256' <<<"$CANDIDATE_CONFIG")"
CANDIDATE_REVISION="$(jq -r '.RevisionId' <<<"$CANDIDATE_CONFIG")"
assert_managed_release_config \
  "$CANDIDATE_CONFIG" "staged candidate" "$EXPECTED_CANDIDATE_REVISION"
RELEASE_DESCRIPTION="git=${SOURCE_REVISION:0:12} corpus=$PINNED_CORPUS_VERSION utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PUBLISHED_VERSIONS="$(
  aws lambda list-versions-by-function \
    --function-name "$FN" --region "$REGION" --output json
)"
# Lambda refuses to republish an unchanged code/config snapshot. Reuse an exact
# numbered match so an interrupted deployment can be retried safely; compare
# all behavior-affecting versioned settings, not just the zip hash.
MATCHING_VERSION="$(exact_published_version "$CANDIDATE_CONFIG" "$PUBLISHED_VERSIONS")"
if [[ -n "$MATCHING_VERSION" ]]; then
  NEW_VERSION="$MATCHING_VERSION"
  echo "reusing exact published candidate version $NEW_VERSION"
else
  NEW_VERSION="$(
    aws lambda publish-version \
      --function-name "$FN" \
      --code-sha256 "$CANDIDATE_CODE_SHA" \
      --revision-id "$CANDIDATE_REVISION" \
      --description "$RELEASE_DESCRIPTION" \
      --region "$REGION" \
      --query Version --output text
  )"
fi
aws lambda wait published-version-active \
  --function-name "$FN" --qualifier "$NEW_VERSION" --region "$REGION"
aws lambda put-runtime-management-config \
  --function-name "$FN" \
  --qualifier "$NEW_VERSION" \
  --update-runtime-on FunctionUpdate \
  --region "$REGION" >/dev/null
CANDIDATE_RUNTIME_MODE="$(
  aws lambda get-runtime-management-config \
    --function-name "$FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --query UpdateRuntimeOn --output text
)"
[[ "$CANDIDATE_RUNTIME_MODE" == "FunctionUpdate" ]] || {
  echo "candidate runtime mode was not frozen at FunctionUpdate" >&2
  exit 1
}

PUBLISHED_CONFIG="$(
  aws lambda get-function-configuration \
    --function-name "$FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --output json
)"
PUBLISHED_CODE_SHA="$(jq -r '.CodeSha256' <<<"$PUBLISHED_CONFIG")"
[[ "$PUBLISHED_CODE_SHA" == "$CANDIDATE_CODE_SHA" ]] || {
  echo "published version code hash does not match the staged artifact" >&2
  exit 1
}
assert_managed_release_config "$PUBLISHED_CONFIG" "published version"
"$ROOT/infra/check-lambda-version.sh" \
  --function-name "$FN" \
  --qualifier "$NEW_VERSION" \
  --expected-corpus "$PINNED_CORPUS_VERSION" \
  --expected-disabled-docs "$DISABLED_DOC_IDS" \
  --region "$REGION"

# Apply the function-wide cost ceiling before a first deployment creates any
# public route. Existing releases already carry this value; reapplying it is
# idempotent shared-infrastructure reconciliation.
aws lambda put-function-concurrency --function-name "$FN" --region "$REGION" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" >/dev/null

if [[ "$HAS_LIVE_ALIAS" == "true" && "$NEW_VERSION" == "$OLD_VERSION" ]]; then
  echo "candidate is already the live immutable version $NEW_VERSION; no alias move needed"
  public_assistant_smoke "$DISABLED_DOC_IDS"
elif [[ "$HAS_LIVE_ALIAS" != "true" ]]; then
  LIVE_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$NEW_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$RELEASE_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  ensure_initial_rollback_alias \
    "$NEW_VERSION" "no prior release retained yet"
  HAS_LIVE_ALIAS=true
  ensure_api_targets_live "$DISABLED_DOC_IDS"
  public_assistant_smoke "$DISABLED_DOC_IDS"
else
  # Re-read immediately before promotion. A deployment that raced us must fail
  # rather than overwrite the winner or corrupt the retained rollback pointer.
  CURRENT_LIVE_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$CURRENT_LIVE_JSON" "$LIVE_ALIAS"
  CURRENT_LIVE_VERSION="$(jq -r '.FunctionVersion' <<<"$CURRENT_LIVE_JSON")"
  CURRENT_LIVE_REVISION="$(jq -r '.RevisionId' <<<"$CURRENT_LIVE_JSON")"
  [[ "$CURRENT_LIVE_VERSION" == "$OLD_VERSION" \
    && "$CURRENT_LIVE_REVISION" == "$OLD_LIVE_REVISION" ]] || {
    echo "live alias changed during deployment; candidate $NEW_VERSION was not promoted" >&2
    exit 1
  }

  if PREVIOUS_ROLLBACK_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    assert_unweighted_alias "$PREVIOUS_ROLLBACK_JSON" "$ROLLBACK_ALIAS"
    PREVIOUS_ROLLBACK_VERSION="$(jq -r '.FunctionVersion' <<<"$PREVIOUS_ROLLBACK_JSON")"
    PREVIOUS_ROLLBACK_REVISION="$(jq -r '.RevisionId' <<<"$PREVIOUS_ROLLBACK_JSON")"
    ROLLBACK_POINTER_DESCRIPTION="prior live before $NEW_VERSION"
    if [[ "$PREVIOUS_ROLLBACK_VERSION" != "$OLD_VERSION" ]]; then
      ROLLBACK_POINTER_GUARD_EXPECTED_VERSION="$OLD_VERSION"
      ROLLBACK_POINTER_GUARD_EXPECTED_REVISION=""
      ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION="$ROLLBACK_POINTER_DESCRIPTION"
      ROLLBACK_POINTER_GUARD_RESTORE_VERSION="$PREVIOUS_ROLLBACK_VERSION"
      ROLLBACK_POINTER_GUARD_ACTIVE=true
    fi
    UPDATED_ROLLBACK_JSON="$(
      aws lambda update-alias \
        --function-name "$FN" \
        --name "$ROLLBACK_ALIAS" \
        --function-version "$OLD_VERSION" \
        --revision-id "$PREVIOUS_ROLLBACK_REVISION" \
        --routing-config "$EMPTY_ALIAS_ROUTING" \
        --description "$ROLLBACK_POINTER_DESCRIPTION" \
        --region "$REGION" \
        --output json
    )"
  elif [[ "$PREVIOUS_ROLLBACK_JSON" == *"ResourceNotFoundException"* ]]; then
    PREVIOUS_ROLLBACK_VERSION=""
    UPDATED_ROLLBACK_JSON="$(
      aws lambda create-alias \
        --function-name "$FN" \
        --name "$ROLLBACK_ALIAS" \
        --function-version "$OLD_VERSION" \
        --routing-config "$EMPTY_ALIAS_ROUTING" \
        --description "prior live before $NEW_VERSION" \
        --region "$REGION" \
        --output json
    )"
  else
    echo "could not inspect rollback alias:" >&2
    echo "$PREVIOUS_ROLLBACK_JSON" >&2
    exit 1
  fi
  assert_unweighted_alias "$UPDATED_ROLLBACK_JSON" "$ROLLBACK_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$UPDATED_ROLLBACK_JSON")" == "$OLD_VERSION" ]] || {
    echo "$ROLLBACK_ALIAS did not settle on prior live version $OLD_VERSION" >&2
    exit 1
  }
  UPDATED_ROLLBACK_REVISION="$(jq -r '.RevisionId' <<<"$UPDATED_ROLLBACK_JSON")"
  if [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]]; then
    ROLLBACK_POINTER_GUARD_EXPECTED_REVISION="$UPDATED_ROLLBACK_REVISION"
  fi

  PROMOTION_DESCRIPTION="$RELEASE_DESCRIPTION previous=$OLD_VERSION"
  PROMOTION_GUARD_EXPECTED_VERSION="$NEW_VERSION"
  PROMOTION_GUARD_EXPECTED_REVISION=""
  PROMOTION_GUARD_EXPECTED_DESCRIPTION="$PROMOTION_DESCRIPTION"
  PROMOTION_GUARD_RESTORE_VERSION="$OLD_VERSION"
  PROMOTION_GUARD_ACTIVE=true
  if ! PROMOTED_LIVE_JSON="$(
    aws lambda update-alias \
      --function-name "$FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$NEW_VERSION" \
      --revision-id "$OLD_LIVE_REVISION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$PROMOTION_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )"; then
    restore_unverified_live || true
    restore_previous_rollback_pointer || true
    echo "live alias promotion failed; candidate $NEW_VERSION is not public" >&2
    exit 1
  fi
  PROMOTED_LIVE_REVISION="$(jq -r '.RevisionId' <<<"$PROMOTED_LIVE_JSON")"
  PROMOTION_GUARD_EXPECTED_REVISION="$PROMOTED_LIVE_REVISION"
  assert_unweighted_alias "$PROMOTED_LIVE_JSON" "$LIVE_ALIAS"

  if ! public_assistant_smoke "$DISABLED_DOC_IDS"; then
    echo "candidate $NEW_VERSION failed public smoke; rolling live back to $OLD_VERSION" >&2
    exit 1
  fi
  VERIFIED_LIVE_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$VERIFIED_LIVE_JSON" "$LIVE_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$VERIFIED_LIVE_JSON")" == "$NEW_VERSION" \
    && "$(jq -r '.RevisionId // ""' <<<"$VERIFIED_LIVE_JSON")" == "$PROMOTED_LIVE_REVISION" ]] || {
    echo "live alias changed before promotion verification completed" >&2
    exit 1
  }
  refresh_integration
  integration_targets_live_alias || {
    echo "HTTP API $API_ID stopped targeting the qualified live alias during promotion" >&2
    exit 1
  }
  PROMOTION_GUARD_ACTIVE=false
  ROLLBACK_POINTER_GUARD_ACTIVE=false
fi

# The first deploy used a Function URL with auth NONE; this account denies
# anonymous InvokeFunctionUrl at the policy layer. Keep that obsolete surface
# removed. API Gateway now invokes only the stable qualified alias.
aws lambda delete-function-url-config --function-name "$FN" --region "$REGION" \
  >/dev/null 2>&1 || true
aws lambda remove-permission --function-name "$FN" --region "$REGION" \
  --statement-id public-url >/dev/null 2>&1 || true

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
echo "live Lambda version: $NEW_VERSION"
FINAL_ROLLBACK_VERSION="$(
  aws lambda get-alias \
    --function-name "$FN" --name "$ROLLBACK_ALIAS" --region "$REGION" \
    --query FunctionVersion --output text
)"
echo "retained rollback version: $FINAL_ROLLBACK_VERSION"
echo "source revision: $SOURCE_REVISION"
echo "artifact code sha256 (base64): $CANDIDATE_CODE_SHA"
echo "corpus pin: $PINNED_CORPUS_VERSION"
echo "disabled documents pending review: $DISABLED_DOC_IDS"
echo "alerts topic: $TOPIC_ARN (subscribe an email to receive alarms)"
echo "dashboard: https://$REGION.console.aws.amazon.com/cloudwatch/home?region=$REGION#dashboards/dashboard/$FN"
