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
LEGACY_IDENTITY_ROLLBACK_VERSION="${FPA_LEGACY_IDENTITY_ROLLBACK_VERSION:-}"
LOG_GROUP="/aws/lambda/$FN"
LOGGING_CONFIG="LogFormat=JSON,ApplicationLogLevel=INFO,SystemLogLevel=WARN,LogGroup=$LOG_GROUP"
ROLE_NAME="$FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${FPA_BUILD_DIR:-$ROOT/infra/build}"
BUNDLE="$BUILD/bundle"
PROMOTION_BUILD="$BUILD/promotion"
PROMOTION_RUNTIME_EVIDENCE="$BUILD/promotion-runtime.json"
PROMOTION_RUN_POINTER="$BUILD/promotion-run-path"
EVAL_BUNDLE_POINTER="$BUILD/promotion-evidence-pointer.json"
PROMOTION_RUNS_ROOT="${FPA_PROMOTION_RUNS_ROOT:-$ROOT/evals/runs}"
API_ID="${FPA_API_ID:-}"
SOURCE_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || {
  echo "source revision is not a full lowercase Git object id" >&2
  exit 2
}
if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]]; then
  echo "working tree is dirty; refusing a false source/release identity" >&2
  echo "commit the complete release before deploying" >&2
  exit 2
fi

for required_command in aws chmod cmp curl find install jq mktemp mv openssl uv; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "$required_command is required" >&2
    exit 2
  }
done

sha256_hex() {
  local digest
  digest="$(openssl dgst -sha256 <"$1")"
  digest="${digest##* }"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "could not compute SHA-256 for $1" >&2
    return 1
  }
  printf '%s\n' "$digest"
}
if [[ -n "$LEGACY_IDENTITY_ROLLBACK_VERSION" \
  && ! "$LEGACY_IDENTITY_ROLLBACK_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  echo "FPA_LEGACY_IDENTITY_ROLLBACK_VERSION must be a numeric published version" >&2
  exit 2
fi
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# ── cost allocation ──────────────────────────────────────────────────────────
# `project` is the cost-allocation tag key activated in Cost Explorer. Anything
# created without it lands in the account's untagged bucket, where the
# `fare-demo` budget and any per-project report cannot see this deployment's
# spend at all. There is no CDK/Terraform layer here (ADR 0004) -- this script is
# the whole deployment -- so tagging is applied by the script itself: on create
# where the API supports it, and re-applied idempotently at the end of every
# deploy so resources created before this existed get labelled on the next run
# rather than staying invisible forever.
#
# The value is the portfolio project name, which is deliberately NOT the repo
# name (`fare-policy-assistant`) or the function name: it is the key the budget
# and the cross-repo cost report group on, so it must stay stable even if the
# function is renamed. `tests/test_deploy_tagging.py` guards that.
PROJECT_TAG=fare-assistant
# Same pair in the two shorthand forms the AWS CLI uses: Lambda, Logs and API
# Gateway take a `key=value` map; IAM, SNS and CloudWatch take a list of
# Key=/Value= structs. Keeping both here means the value is written once.
PROJECT_TAG_MAP="project=$PROJECT_TAG"
PROJECT_TAG_LIST="Key=project,Value=$PROJECT_TAG"

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

# CloudWatch JSON metric contracts. Keep the legacy handler/call/feedback
# filters through one rollback-compatible release; the additive v2 filters
# consume structured application events emitted by JSON/INFO Lambda logging.
HANDLER_ERROR_V2_FILTER='{ $.event = "handler_error" }'
FEEDBACK_DOWN_V2_FILTER='{ $.event = "feedback" && $.verdict = "down" }'
GENAI_CALL_FILTER='{ $.event = "genai_call" && $.completion_recorded IS TRUE }'
MODEL_COST_FILTER='{ $.event = "genai_call" && $.cost_estimate_available IS TRUE && $.estimated_cost_usd = * }'
UNPRICED_MODEL_FILTER='{ $.event = "genai_call" && $.completion_recorded IS TRUE && $.cost_estimate_available IS FALSE }'
MODEL_DURATION_FILTER='{ $.event = "genai_call" && $.model_duration_ms = * }'
ANSWER_DURATION_FILTER='{ $.event = "answer_request" && $.duration_ms = * }'

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
PROMOTION_GUARD_RESTORE_DESCRIPTION=""
ROLLBACK_POINTER_GUARD_ACTIVE=false
ROLLBACK_POINTER_GUARD_EXPECTED_VERSION=""
ROLLBACK_POINTER_GUARD_EXPECTED_REVISION=""
ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION=""
ROLLBACK_POINTER_GUARD_RESTORE_VERSION=""
ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION=""

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
  if [[ "$current_version" == "$PROMOTION_GUARD_RESTORE_VERSION" \
    && "$current_description" == "$PROMOTION_GUARD_RESTORE_DESCRIPTION" ]]; then
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
      --description "$PROMOTION_GUARD_RESTORE_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )"; then
    echo "CRITICAL: compare-and-swap restore of live failed" >&2
    return 1
  fi
  if ! jq -e \
    --arg version "$PROMOTION_GUARD_RESTORE_VERSION" \
    --arg description "$PROMOTION_GUARD_RESTORE_DESCRIPTION" '
      .FunctionVersion == $version
      and (.Description // "") == $description
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
  if [[ "$current_version" == "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" \
    && "$current_description" == "$ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION" ]]; then
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
      --description "$ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )"; then
    echo "WARNING: compare-and-swap restore of the rollback pointer failed" >&2
    return 1
  fi
  if ! jq -e \
    --arg version "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" \
    --arg description "$ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION" '
      .FunctionVersion == $version
      and (.Description // "") == $description
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
BASELINE_LIVE_DESCRIPTION=""
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
    BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$LIVE_ALIAS_JSON")"
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
import hashlib
import json
import os

raw = json.loads(os.environ["FPA_DEPLOY_EXISTING_LAMBDA_ENV"] or "{}")
values = raw if isinstance(raw, dict) else {}
history_key = os.environ["FPA_DEPLOY_HISTORY_HMAC_KEY"]
history_key_id = hashlib.sha256(
    b"fare-assistant.history-key-id.v1\0" + history_key.encode("ascii")
).hexdigest()
for derived_key in (
    "FPA_ARTIFACT_CODE_SHA256",
    "FPA_CONFIG_VERSION",
    "FPA_PINNED_CONTENT_VERSION",
    "FPA_PINNED_SNAPSHOT_VERSION",
    "FPA_RELEASE_VERSION",
    "FPA_SOURCE_REVISION",
):
    values.pop(derived_key, None)
values.update(
    {
        "FPA_PINNED_CORPUS_VERSION": os.environ["FPA_DEPLOY_PINNED_CORPUS_VERSION"],
        "FPA_DISABLED_DOC_IDS": os.environ["FPA_DEPLOY_DISABLED_DOC_IDS"],
        "FPA_HISTORY_HMAC_KEY": history_key,
        "FPA_HISTORY_HMAC_KEY_ID": history_key_id,
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
    "LoggingConfig",
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
    --arg log_group "$LOG_GROUP" \
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
      and .LoggingConfig == {
        "LogFormat": "JSON",
        "ApplicationLogLevel": "INFO",
        "SystemLogLevel": "WARN",
        "LogGroup": $log_group
      }
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

qualified_release_health() {
  local version="$1"
  local expected_corpus="$2"
  local expected_disabled_docs="$3"
  local release_config
  local source
  local config_version
  local content
  local snapshot
  local release
  local artifact
  local value
  local present=0

  [[ "$version" =~ ^[1-9][0-9]*$ ]] || {
    echo "qualified release health requires a numeric version" >&2
    return 1
  }
  release_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --qualifier "$version" \
      --region "$REGION" --output json
  )"
  source="$(jq -r '.Environment.Variables.FPA_SOURCE_REVISION // ""' <<<"$release_config")"
  config_version="$(
    jq -r '.Environment.Variables.FPA_CONFIG_VERSION // ""' <<<"$release_config"
  )"
  content="$(
    jq -r '.Environment.Variables.FPA_PINNED_CONTENT_VERSION // ""' <<<"$release_config"
  )"
  snapshot="$(
    jq -r '.Environment.Variables.FPA_PINNED_SNAPSHOT_VERSION // ""' <<<"$release_config"
  )"
  release="$(jq -r '.Environment.Variables.FPA_RELEASE_VERSION // ""' <<<"$release_config")"
  artifact="$(
    jq -r '.Environment.Variables.FPA_ARTIFACT_CODE_SHA256 // ""' <<<"$release_config"
  )"
  for value in "$source" "$config_version" "$content" "$snapshot" "$release" "$artifact"; do
    [[ -n "$value" ]] && present=$((present + 1))
  done

  if [[ "$present" == "6" ]]; then
    jq -e --arg version "$version" --arg artifact "$artifact" '
      .Version == $version and .CodeSha256 == $artifact
    ' <<<"$release_config" >/dev/null || {
      echo "qualified release $version and identity artifact disagree" >&2
      return 1
    }
    "$ROOT/infra/check-lambda-version.sh" \
      --function-name "$FN" \
      --qualifier "$version" \
      --expected-corpus "$expected_corpus" \
      --expected-disabled-docs "$expected_disabled_docs" \
      --require-release-identity \
      --expected-source "$source" \
      --expected-config "$config_version" \
      --expected-content "$content" \
      --expected-snapshot "$snapshot" \
      --expected-release "$release" \
      --expected-artifact "$artifact" \
      --region "$REGION"
    return
  fi
  if [[ "$present" != "0" ]]; then
    echo "qualified release $version has a partial identity tuple; refusing direct health" >&2
    return 1
  fi
  if [[ -z "$LEGACY_IDENTITY_ROLLBACK_VERSION" \
    || "$version" != "$LEGACY_IDENTITY_ROLLBACK_VERSION" ]]; then
    echo "legacy identity is not allowlisted for qualified release $version" >&2
    return 1
  fi
  "$ROOT/infra/check-lambda-version.sh" \
    --function-name "$FN" \
    --qualifier "$version" \
    --expected-corpus "$expected_corpus" \
    --expected-disabled-docs "$expected_disabled_docs" \
    --allow-legacy-release-identity \
    --region "$REGION"
}

public_assistant_smoke() {
  local expected_disabled_docs="$1"
  local live_alias_json
  local version
  local release_config
  local source
  local config_version
  local content
  local snapshot
  local release
  local artifact
  local present=0

  live_alias_json="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$live_alias_json" "$LIVE_ALIAS"
  version="$(jq -r '.FunctionVersion // ""' <<<"$live_alias_json")"
  [[ "$version" =~ ^[1-9][0-9]*$ ]] || {
    echo "live alias does not target a numeric version for public smoke" >&2
    return 1
  }
  release_config="$(
    aws lambda get-function-configuration \
      --function-name "$FN" --qualifier "$version" \
      --region "$REGION" --output json
  )"
  source="$(jq -r '.Environment.Variables.FPA_SOURCE_REVISION // ""' <<<"$release_config")"
  config_version="$(
    jq -r '.Environment.Variables.FPA_CONFIG_VERSION // ""' <<<"$release_config"
  )"
  content="$(
    jq -r '.Environment.Variables.FPA_PINNED_CONTENT_VERSION // ""' <<<"$release_config"
  )"
  snapshot="$(
    jq -r '.Environment.Variables.FPA_PINNED_SNAPSHOT_VERSION // ""' <<<"$release_config"
  )"
  release="$(jq -r '.Environment.Variables.FPA_RELEASE_VERSION // ""' <<<"$release_config")"
  artifact="$(
    jq -r '.Environment.Variables.FPA_ARTIFACT_CODE_SHA256 // ""' <<<"$release_config"
  )"
  for value in "$source" "$config_version" "$content" "$snapshot" "$release" "$artifact"; do
    [[ -n "$value" ]] && present=$((present + 1))
  done

  if [[ "$present" == "6" ]]; then
    jq -e --arg version "$version" --arg artifact "$artifact" '
      .Version == $version and .CodeSha256 == $artifact
    ' <<<"$release_config" >/dev/null || {
      echo "live configuration and identity artifact disagree before public smoke" >&2
      return 1
    }
    "$ROOT/scripts/smoke-production.sh" \
      --assistant-only \
      --assistant-base-url "https://$API_ID.execute-api.$REGION.amazonaws.com" \
      --expected-disabled-docs "$expected_disabled_docs" \
      --require-release-identity \
      --expected-source "$source" \
      --expected-config "$config_version" \
      --expected-content "$content" \
      --expected-snapshot "$snapshot" \
      --expected-release "$release" \
      --expected-artifact "$artifact" \
      --expected-function-version "$version"
    return
  fi
  if [[ "$present" != "0" ]]; then
    echo "live release has a partial identity tuple; refusing public smoke" >&2
    return 1
  fi
  if [[ -z "$LEGACY_IDENTITY_ROLLBACK_VERSION" \
    || "$version" != "$LEGACY_IDENTITY_ROLLBACK_VERSION" ]]; then
    echo "legacy identity is not allowlisted for live version $version" >&2
    return 1
  fi
  "$ROOT/scripts/smoke-production.sh" \
    --assistant-only \
    --assistant-base-url "https://$API_ID.execute-api.$REGION.amazonaws.com" \
    --expected-disabled-docs "$expected_disabled_docs" \
    --allow-legacy-release-identity
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
        --tags "$PROJECT_TAG_MAP" \
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
  qualified_release_health "$LIVE_VERSION" "$live_corpus" "$live_disabled"

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
  qualified_release_health \
    "$BOOTSTRAP_VERSION" "$BOOTSTRAP_CORPUS" "$BOOTSTRAP_DISABLED"
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
  BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$LIVE_ALIAS_JSON")"
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
rm -rf "$BUNDLE" "$BUILD/bundle.zip" "$PROMOTION_BUILD"
rm -f \
  "$PROMOTION_RUNTIME_EVIDENCE" \
  "$PROMOTION_RUN_POINTER" \
  "$EVAL_BUNDLE_POINTER"
mkdir -p \
  "$BUNDLE/src" \
  "$BUNDLE/corpus/processed" \
  "$BUNDLE/docs" \
  "$BUNDLE/release" \
  "$BUNDLE/web"

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

# Only reviewed Git index entries may cross the first-party bundle boundary.
# Recursive shell copies would also package ignored checkout debris such as
# bytecode, editor state, or a locally dropped credential.  Exact file scopes
# keep the intentionally small corpus/docs/web runtime surface explicit.
(
  cd "$ROOT"
  uv run python scripts/copy_tracked_bundle.py \
    --repo-root "$ROOT" \
    --destination "$BUNDLE" \
    --tree src/assistant \
    --tree prompts \
    --file corpus/processed/chunks.jsonl \
    --file docs/answer-contract.schema.json \
    --file web/__init__.py \
    --file web/handler.py \
    --file web/index.html \
    --file web/offline.py \
    --file web/guide.py \
    --file web/embed.py \
    --file web/csp.py
)

DESCRIPTOR_BUILD_ARGS=(
  --output "$BUNDLE/release/release.json"
  --source-revision "$SOURCE_REVISION"
)
RELEASE_DESCRIPTOR_SUMMARY="$(
  cd "$ROOT"
  FPA_RELEASE_EFFECTIVE_ENVIRONMENT_JSON="$LAMBDA_ENV" \
    uv run python scripts/build_release_descriptor.py \
      "${DESCRIPTOR_BUILD_ARGS[@]}"
)"
if ! jq -e \
  --arg source "$SOURCE_REVISION" \
  --arg corpus "$PINNED_CORPUS_VERSION" '
    .FPA_SOURCE_REVISION == $source
    and .FPA_PINNED_CORPUS_VERSION == $corpus
    and (.FPA_CONFIG_VERSION | test("^[0-9a-f]{64}$"))
    and (.FPA_PINNED_CONTENT_VERSION | test("^[0-9a-f]{64}$"))
    and (.FPA_PINNED_SNAPSHOT_VERSION | test("^[0-9a-f]{64}$"))
    and (.FPA_RELEASE_VERSION | test("^[0-9a-f]{64}$"))
    and (.FPA_HISTORY_HMAC_KEY_ID | test("^[0-9a-f]{64}$"))
  ' <<<"$RELEASE_DESCRIPTOR_SUMMARY" >/dev/null; then
  echo "release descriptor summary did not match the reviewed source/corpus" >&2
  exit 1
fi
CONFIG_VERSION="$(jq -r '.FPA_CONFIG_VERSION' <<<"$RELEASE_DESCRIPTOR_SUMMARY")"
CONTENT_VERSION="$(jq -r '.FPA_PINNED_CONTENT_VERSION' <<<"$RELEASE_DESCRIPTOR_SUMMARY")"
SNAPSHOT_VERSION="$(jq -r '.FPA_PINNED_SNAPSHOT_VERSION' <<<"$RELEASE_DESCRIPTOR_SUMMARY")"
RELEASE_VERSION="$(jq -r '.FPA_RELEASE_VERSION' <<<"$RELEASE_DESCRIPTOR_SUMMARY")"
DESCRIPTOR_HISTORY_KEY_ID="$(
  jq -r '.FPA_HISTORY_HMAC_KEY_ID' <<<"$RELEASE_DESCRIPTOR_SUMMARY"
)"
LAMBDA_HISTORY_KEY_ID="$(
  jq -r '.Variables.FPA_HISTORY_HMAC_KEY_ID // ""' <<<"$LAMBDA_ENV"
)"
[[ "$DESCRIPTOR_HISTORY_KEY_ID" == "$LAMBDA_HISTORY_KEY_ID" ]] || {
  echo "release descriptor history-key identity disagrees with Lambda configuration" >&2
  exit 1
}
LAMBDA_ENV="$(
  jq -c \
    --arg source "$SOURCE_REVISION" \
    --arg config "$CONFIG_VERSION" \
    --arg content "$CONTENT_VERSION" \
    --arg snapshot "$SNAPSHOT_VERSION" \
    --arg release "$RELEASE_VERSION" '
      .Variables.FPA_SOURCE_REVISION = $source
      | .Variables.FPA_CONFIG_VERSION = $config
      | .Variables.FPA_PINNED_CONTENT_VERSION = $content
      | .Variables.FPA_PINNED_SNAPSHOT_VERSION = $snapshot
      | .Variables.FPA_RELEASE_VERSION = $release
    ' <<<"$LAMBDA_ENV"
)"

(
  cd "$ROOT"
  uv run python scripts/build_lambda_zip.py "$BUNDLE" "$BUILD/bundle.zip"
)
LOCAL_CODE_SHA="$(
  openssl dgst -sha256 -binary "$BUILD/bundle.zip" \
    | openssl base64 -A
)"
[[ "$LOCAL_CODE_SHA" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
  echo "could not compute the AWS-style SHA-256 for the local deployment bundle" >&2
  exit 1
}
LAMBDA_ENV="$(
  jq -c --arg artifact "$LOCAL_CODE_SHA" \
    '.Variables.FPA_ARTIFACT_CODE_SHA256 = $artifact' <<<"$LAMBDA_ENV"
)"

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
    --assume-role-policy-document "$TRUST" \
    --tags "$PROJECT_TAG_LIST" >/dev/null
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

# Create the reviewed log destination and metric filters before candidate
# verification. The paid numeric-version check must produce a real structured
# event against the same filter contract that will observe public traffic.
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION" \
  --tags "$PROJECT_TAG_MAP" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" \
  --retention-in-days 14 --region "$REGION"

_metric_filter() {  # filter name, pattern, transformation
  aws logs put-metric-filter --region "$REGION" \
    --log-group-name "$LOG_GROUP" \
    --filter-name "$FN-$1" \
    --filter-pattern "$2" \
    --metric-transformations "$3" >/dev/null
}

_assert_metric_filter() {  # filter name, pattern, metric name, metric value, default mode
  local filter_name="$FN-$1"
  local default_mode="${5:-zero}"
  local attempt
  local installed
  for attempt in 1 2 3 4 5; do
    installed="$(
      aws logs describe-metric-filters --region "$REGION" \
        --log-group-name "$LOG_GROUP" \
        --filter-name-prefix "$filter_name" \
        --output json
    )"
    if jq -e \
      --arg filter_name "$filter_name" \
      --arg pattern "$2" \
      --arg namespace "$FN" \
      --arg metric_name "$3" \
      --arg metric_value "$4" \
      --arg default_mode "$default_mode" '
        [.metricFilters[]
         | select(.filterName == $filter_name)
         | select(.filterPattern == $pattern)
         | select(
             (.metricTransformations | length) == 1
             and .metricTransformations[0].metricNamespace == $namespace
             and .metricTransformations[0].metricName == $metric_name
             and .metricTransformations[0].metricValue == $metric_value
             and (
               if $default_mode == "absent"
               then (.metricTransformations[0] | has("defaultValue") | not)
               else .metricTransformations[0].defaultValue == 0
               end
             )
           )]
        | length == 1
      ' <<<"$installed" >/dev/null; then
      return 0
    fi
    ((attempt == 5)) || sleep 2
  done
  echo "metric filter $filter_name did not settle on its reviewed contract" >&2
  exit 1
}

# Legacy filters remain valid for the retained plaintext/WARN rollback release.
_metric_filter handler-errors '{ $.error = * }' \
  "metricName=HandlerErrors,metricNamespace=$FN,metricValue=1,defaultValue=0"
_metric_filter bedrock-calls '{ $.model_called IS TRUE }' \
  "metricName=BedrockAnswerCalls,metricNamespace=$FN,metricValue=1,defaultValue=0"
_metric_filter feedback-down '{ $.feedback = "down" }' \
  "metricName=FeedbackDown,metricNamespace=$FN,metricValue=1,defaultValue=0"

# Structured v2 metrics. Cost is an application estimate, not AWS billing.
_metric_filter handler-errors-v2 "$HANDLER_ERROR_V2_FILTER" \
  "metricName=HandlerErrors,metricNamespace=$FN,metricValue=1,defaultValue=0"
_metric_filter feedback-down-v2 "$FEEDBACK_DOWN_V2_FILTER" \
  "metricName=FeedbackDown,metricNamespace=$FN,metricValue=1,defaultValue=0"
_metric_filter genai-calls "$GENAI_CALL_FILTER" \
  "metricName=GenAICalls,metricNamespace=$FN,metricValue=1,defaultValue=0"
_metric_filter estimated-model-cost "$MODEL_COST_FILTER" \
  "metricName=EstimatedModelCostUsd,metricNamespace=$FN,metricValue=\$.estimated_cost_usd,defaultValue=0"
_metric_filter unpriced-model-calls "$UNPRICED_MODEL_FILTER" \
  "metricName=UnpricedModelCalls,metricNamespace=$FN,metricValue=1,defaultValue=0"
# Histogram-like metrics must not inject zero samples during minutes that
# contain unrelated logs; those defaults would falsify latency percentiles.
_metric_filter model-duration "$MODEL_DURATION_FILTER" \
  "metricName=ModelDurationMs,metricNamespace=$FN,metricValue=\$.model_duration_ms"
_metric_filter answer-duration "$ANSWER_DURATION_FILTER" \
  "metricName=AnswerDurationMs,metricNamespace=$FN,metricValue=\$.duration_ms"

_assert_metric_filter handler-errors '{ $.error = * }' HandlerErrors 1
_assert_metric_filter bedrock-calls '{ $.model_called IS TRUE }' BedrockAnswerCalls 1
_assert_metric_filter feedback-down '{ $.feedback = "down" }' FeedbackDown 1
_assert_metric_filter handler-errors-v2 "$HANDLER_ERROR_V2_FILTER" HandlerErrors 1
_assert_metric_filter feedback-down-v2 "$FEEDBACK_DOWN_V2_FILTER" FeedbackDown 1
_assert_metric_filter genai-calls "$GENAI_CALL_FILTER" GenAICalls 1
_assert_metric_filter estimated-model-cost "$MODEL_COST_FILTER" \
  EstimatedModelCostUsd '$.estimated_cost_usd'
_assert_metric_filter unpriced-model-calls "$UNPRICED_MODEL_FILTER" UnpricedModelCalls 1
_assert_metric_filter model-duration "$MODEL_DURATION_FILTER" \
  ModelDurationMs '$.model_duration_ms' absent
_assert_metric_filter answer-duration "$ANSWER_DURATION_FILTER" \
  AnswerDurationMs '$.duration_ms' absent

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
      --logging-config "$LOGGING_CONFIG" \
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
  [[ -n "$(jq -r '.RevisionId // ""' <<<"$STAGED_CODE_RESPONSE")" ]] || {
    echo "code staging returned no revision id" >&2
    exit 1
  }
else
  STAGED_CODE_RESPONSE="$(
    aws lambda create-function --function-name "$FN" --region "$REGION" \
      --runtime python3.12 --handler web.handler.handler --architectures arm64 \
      --timeout 25 --memory-size 512 --role "$ROLE_ARN" \
      --environment "$LAMBDA_ENV" \
      --logging-config "$LOGGING_CONFIG" \
      --tags "$PROJECT_TAG_MAP" \
      --zip-file "fileb://$BUILD/bundle.zip" \
      --output json
  )"
  [[ -n "$(jq -r '.RevisionId // ""' <<<"$STAGED_CODE_RESPONSE")" ]] || {
    echo "function creation returned no revision id" >&2
    exit 1
  }
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION"

# UpdateFunctionCode/CreateFunction responses can contain a transient view of
# configuration while Lambda is still applying the operation. Promotion is
# bound only to the settled, re-read snapshot and its current revision.
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
EXPECTED_CANDIDATE_REVISION="$CANDIDATE_REVISION"
[[ -n "$EXPECTED_CANDIDATE_REVISION" ]] || {
  echo "settled candidate has no revision id" >&2
  exit 1
}
assert_managed_release_config \
  "$CANDIDATE_CONFIG" "staged candidate" "$EXPECTED_CANDIDATE_REVISION"
RELEASE_DESCRIPTION="release=$RELEASE_VERSION source=${SOURCE_REVISION:0:12} config=${CONFIG_VERSION:0:12} content=${CONTENT_VERSION:0:12} snapshot=${SNAPSHOT_VERSION:0:12}"
(( ${#RELEASE_DESCRIPTION} <= 256 )) || {
  echo "release identity description exceeds the Lambda 256-character limit" >&2
  exit 1
}
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
[[ "$(jq -r '.Description // ""' <<<"$PUBLISHED_CONFIG")" == "$RELEASE_DESCRIPTION" ]] || {
  echo "published version description does not match the release identity" >&2
  exit 1
}
assert_managed_release_config "$PUBLISHED_CONFIG" "published version"
CANDIDATE_TELEMETRY="$BUILD/candidate-telemetry.json"
"$ROOT/infra/check-lambda-version.sh" \
  --function-name "$FN" \
  --qualifier "$NEW_VERSION" \
  --expected-corpus "$PINNED_CORPUS_VERSION" \
  --expected-disabled-docs "$DISABLED_DOC_IDS" \
  --require-release-identity \
  --expected-source "$SOURCE_REVISION" \
  --expected-config "$CONFIG_VERSION" \
  --expected-content "$CONTENT_VERSION" \
  --expected-snapshot "$SNAPSHOT_VERSION" \
  --expected-release "$RELEASE_VERSION" \
  --expected-artifact "$CANDIDATE_CODE_SHA" \
  --require-structured-telemetry \
  --telemetry-output "$CANDIDATE_TELEMETRY" \
  --region "$REGION"

_test_metric_filter_event() {  # filter name, pattern, event selector, expected matches
  local event_message
  local messages
  local result
  event_message="$(jq -c "$3" "$CANDIDATE_TELEMETRY")"
  [[ "$event_message" != "null" ]] || {
    echo "candidate telemetry did not contain the $3 event" >&2
    exit 1
  }
  messages="$(jq -nc --arg message "$event_message" '[$message]')"
  result="$(
    aws logs test-metric-filter --region "$REGION" \
      --filter-pattern "$2" \
      --log-event-messages "$messages" \
      --output json
  )"
  jq -e --argjson expected "$4" \
    '(.matches | type == "array") and (.matches | length) == $expected' \
    <<<"$result" >/dev/null || {
    echo "metric filter $FN-$1 did not match candidate telemetry as expected" >&2
    exit 1
  }
}

# Prove the installed patterns against the actual numbered candidate's events
# before any public alias moves. Negative checks catch accidental overlap as
# well as filters that never match.
_test_metric_filter_event genai-calls "$GENAI_CALL_FILTER" .genai_call 1
_test_metric_filter_event estimated-model-cost "$MODEL_COST_FILTER" .genai_call 1
_test_metric_filter_event unpriced-model-calls "$UNPRICED_MODEL_FILTER" .genai_call 0
_test_metric_filter_event model-duration "$MODEL_DURATION_FILTER" .genai_call 1
_test_metric_filter_event handler-errors-v2 "$HANDLER_ERROR_V2_FILTER" .answer_request 0
_test_metric_filter_event bedrock-calls '{ $.model_called IS TRUE }' .answer_request 1
_test_metric_filter_event answer-duration "$ANSWER_DURATION_FILTER" .answer_request 1

verify_promotion_trio() {
  local evidence_dir="$1"
  local expected_summary_sha="$2"
  local expected_results_sha="$3"
  local expected_promotion_sha="$4"
  local evidence_name
  local unexpected_entry
  local receipt

  [[ -d "$evidence_dir" && ! -L "$evidence_dir" ]] || {
    echo "promotion evidence bundle is not a regular directory" >&2
    return 1
  }
  for evidence_name in summary.json results.jsonl promotion.json; do
    [[ -f "$evidence_dir/$evidence_name" \
      && ! -L "$evidence_dir/$evidence_name" ]] || {
      echo "promotion evidence bundle is missing regular $evidence_name" >&2
      return 1
    }
  done
  unexpected_entry="$(
    find "$evidence_dir" -mindepth 1 -maxdepth 1 \
      ! -name summary.json ! -name results.jsonl ! -name promotion.json \
      -print -quit
  )"
  [[ -z "$unexpected_entry" ]] || {
    echo "promotion evidence bundle must contain exactly the verified trio" >&2
    return 1
  }
  [[ "$(sha256_hex "$evidence_dir/summary.json")" == "$expected_summary_sha" \
    && "$(sha256_hex "$evidence_dir/results.jsonl")" == "$expected_results_sha" \
    && "$(sha256_hex "$evidence_dir/promotion.json")" == "$expected_promotion_sha" ]] || {
    echo "promotion evidence bundle digest does not match its pointer" >&2
    return 1
  }

  receipt="$(
    cd "$ROOT"
    FPA_DEPLOY_PROMOTION_DIR="$evidence_dir" \
      FPA_DEPLOY_EXPECTED_SOURCE="$SOURCE_REVISION" \
      FPA_DEPLOY_EXPECTED_CONFIG="$CONFIG_VERSION" \
      FPA_DEPLOY_EXPECTED_CONTENT="$CONTENT_VERSION" \
      FPA_DEPLOY_EXPECTED_SNAPSHOT="$SNAPSHOT_VERSION" \
      FPA_DEPLOY_EXPECTED_RELEASE="$RELEASE_VERSION" \
      FPA_DEPLOY_EXPECTED_CORPUS="$PINNED_CORPUS_VERSION" \
      FPA_DEPLOY_EXPECTED_ARTIFACT="$CANDIDATE_CODE_SHA" \
      FPA_DEPLOY_EXPECTED_FUNCTION_VERSION="$NEW_VERSION" \
      uv run python -c '
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from assistant.promotion_evidence import verify_promotion_evidence

root = Path(os.environ["FPA_DEPLOY_PROMOTION_DIR"])
evidence = verify_promotion_evidence(
    summary_path=root / "summary.json",
    results_path=root / "results.jsonl",
    promotion_path=root / "promotion.json",
    freshness_budget=timedelta(days=7),
    clock=lambda: datetime.now(UTC),
)
receipt = evidence.as_dict()
print(json.dumps(
    {
        "status": evidence.status,
        "summary_sha256": evidence.summary_sha256,
        "results_sha256": evidence.results_sha256,
        "promotion_sha256": evidence.promotion_sha256,
        "runtime_release": receipt["runtime_release"],
    },
    sort_keys=True,
    separators=(",", ":"),
))
'
  )"
  jq -e \
    --arg summary "$expected_summary_sha" \
    --arg results "$expected_results_sha" \
    --arg promotion "$expected_promotion_sha" \
    --arg source "$SOURCE_REVISION" \
    --arg config "$CONFIG_VERSION" \
    --arg content "$CONTENT_VERSION" \
    --arg snapshot "$SNAPSHOT_VERSION" \
    --arg release "$RELEASE_VERSION" \
    --arg corpus "$PINNED_CORPUS_VERSION" \
    --arg artifact "$CANDIDATE_CODE_SHA" \
    --arg function_version "$NEW_VERSION" '
      .status == "verified"
      and .summary_sha256 == $summary
      and .results_sha256 == $results
      and .promotion_sha256 == $promotion
      and .runtime_release.source_revision == $source
      and .runtime_release.config_version == $config
      and .runtime_release.content_version == $content
      and .runtime_release.snapshot_version == $snapshot
      and .runtime_release.release_version == $release
      and .runtime_release.corpus_version == $corpus
      and .runtime_release.artifact_code_sha256 == $artifact
      and .runtime_release.function_version == $function_version
    ' <<<"$receipt" >/dev/null || {
    echo "full promotion evidence verifier returned an unexpected receipt" >&2
    return 1
  }
  printf '%s\n' "$receipt"
}

# Bind the exact numbered candidate to a complete, live, uncached evaluation
# before either alias can move. The runner writes the machine-readable pointer
# only after strict parity/regression gates and a post-run identity recheck.
mkdir -p "$PROMOTION_BUILD"
(
  cd "$ROOT"
  FPA_RELEASE_EFFECTIVE_ENVIRONMENT_JSON="$LAMBDA_ENV" \
    uv run python -m evals.runner \
      --full \
      --promotion \
      --no-cache \
      --release-descriptor "$BUNDLE/release/release.json" \
      --run-path-output "$PROMOTION_RUN_POINTER"
)
[[ -f "$PROMOTION_RUN_POINTER" && ! -L "$PROMOTION_RUN_POINTER" ]] || {
  echo "promotion runner did not write a regular eval-bundle pointer" >&2
  exit 1
}
PROMOTION_RUN_POINTER_SNAPSHOT="$(
  mktemp "$BUILD/.promotion-run-pointer-snapshot.XXXXXX"
)"
install -m 0444 "$PROMOTION_RUN_POINTER" "$PROMOTION_RUN_POINTER_SNAPSHOT"
PROMOTION_RUN_POINTER_CANONICAL="$(mktemp "$BUILD/.promotion-run-pointer.XXXXXX")"
if ! jq -e -s 'length == 1 and (.[0] | type == "object")' \
    "$PROMOTION_RUN_POINTER_SNAPSHOT" >/dev/null \
  || ! jq -S -c . \
    "$PROMOTION_RUN_POINTER_SNAPSHOT" >"$PROMOTION_RUN_POINTER_CANONICAL" \
  || ! cmp -s \
    "$PROMOTION_RUN_POINTER_SNAPSHOT" "$PROMOTION_RUN_POINTER_CANONICAL"; then
  echo "promotion eval-bundle pointer is not canonical JSON" >&2
  rm -f "$PROMOTION_RUN_POINTER_CANONICAL" "$PROMOTION_RUN_POINTER_SNAPSHOT"
  exit 1
fi
rm -f "$PROMOTION_RUN_POINTER_CANONICAL"
PROMOTION_RUN_POINTER_JSON="$(<"$PROMOTION_RUN_POINTER_SNAPSHOT")"
rm -f "$PROMOTION_RUN_POINTER_SNAPSHOT"
jq -e '
  keys == [
    "bundle_path",
    "content_address",
    "results_sha256",
    "run_dir",
    "schema",
    "summary_sha256"
  ]
  and .schema == "fare-assistant.eval-run-bundle-pointer.v1"
  and (.run_dir | type == "string" and startswith("/"))
  and (.bundle_path | type == "string" and startswith("/"))
  and (.content_address | test("^[0-9a-f]{64}$"))
  and (.summary_sha256 | test("^[0-9a-f]{64}$"))
  and (.results_sha256 | test("^[0-9a-f]{64}$"))
' <<<"$PROMOTION_RUN_POINTER_JSON" >/dev/null || {
  echo "promotion eval-bundle pointer has an invalid closed schema" >&2
  exit 1
}
PROMOTION_RUN_DIR="$(jq -r '.run_dir' <<<"$PROMOTION_RUN_POINTER_JSON")"
PROMOTION_EVAL_BUNDLE="$(jq -r '.bundle_path' <<<"$PROMOTION_RUN_POINTER_JSON")"
PROMOTION_EVAL_CONTENT_ADDRESS="$(
  jq -r '.content_address' <<<"$PROMOTION_RUN_POINTER_JSON"
)"
PROMOTION_EVAL_SUMMARY_SHA="$(
  jq -r '.summary_sha256' <<<"$PROMOTION_RUN_POINTER_JSON"
)"
PROMOTION_EVAL_RESULTS_SHA="$(
  jq -r '.results_sha256' <<<"$PROMOTION_RUN_POINTER_JSON"
)"
[[ "$PROMOTION_RUN_DIR" == /* \
  && "$(dirname "$PROMOTION_RUN_DIR")" == "$PROMOTION_RUNS_ROOT" \
  && "$(basename "$PROMOTION_RUN_DIR")" =~ ^[0-9]{8}T[0-9]{6}Z(-[0-9]{2})?$ \
  && -d "$PROMOTION_RUNS_ROOT" \
  && ! -L "$PROMOTION_RUNS_ROOT" \
  && -d "$PROMOTION_RUN_DIR" \
  && ! -L "$PROMOTION_RUN_DIR" ]] || {
  echo "promotion runner returned an unsafe or unexpected run directory" >&2
  exit 1
}
[[ "$PROMOTION_EVAL_BUNDLE" \
    == "$PROMOTION_RUN_DIR/bundles/$PROMOTION_EVAL_CONTENT_ADDRESS" \
  && -d "$PROMOTION_RUN_DIR/bundles" \
  && ! -L "$PROMOTION_RUN_DIR/bundles" \
  && -d "$PROMOTION_EVAL_BUNDLE" \
  && ! -L "$PROMOTION_EVAL_BUNDLE" ]] || {
  echo "promotion pointer does not identify its content-addressed run bundle" >&2
  exit 1
}
for promotion_input in summary.json results.jsonl bundle.json; do
  [[ -f "$PROMOTION_EVAL_BUNDLE/$promotion_input" \
    && ! -L "$PROMOTION_EVAL_BUNDLE/$promotion_input" ]] || {
    echo "promotion eval bundle is missing regular $promotion_input" >&2
    exit 1
  }
done
PROMOTION_EVAL_EXTRA="$(
  find "$PROMOTION_EVAL_BUNDLE" -mindepth 1 -maxdepth 1 \
    ! -name summary.json ! -name results.jsonl ! -name bundle.json \
    -print -quit
)"
[[ -z "$PROMOTION_EVAL_EXTRA" ]] || {
  echo "promotion eval bundle contains an unexpected entry" >&2
  exit 1
}
PROMOTION_BUNDLE_MANIFEST_SOURCE="$PROMOTION_EVAL_BUNDLE/bundle.json"
PROMOTION_BUNDLE_MANIFEST="$(
  mktemp "$BUILD/.promotion-bundle-manifest-snapshot.XXXXXX"
)"
install -m 0444 "$PROMOTION_BUNDLE_MANIFEST_SOURCE" "$PROMOTION_BUNDLE_MANIFEST"
PROMOTION_BUNDLE_MANIFEST_CANONICAL="$(
  mktemp "$BUILD/.promotion-bundle-manifest.XXXXXX"
)"
if ! jq -e -s 'length == 1 and (.[0] | type == "object")' \
    "$PROMOTION_BUNDLE_MANIFEST" >/dev/null \
  || ! jq -S -c . "$PROMOTION_BUNDLE_MANIFEST" \
    >"$PROMOTION_BUNDLE_MANIFEST_CANONICAL" \
  || ! cmp -s \
    "$PROMOTION_BUNDLE_MANIFEST" \
    "$PROMOTION_BUNDLE_MANIFEST_CANONICAL"; then
  echo "promotion eval-bundle manifest is not canonical JSON" >&2
  rm -f "$PROMOTION_BUNDLE_MANIFEST_CANONICAL" "$PROMOTION_BUNDLE_MANIFEST"
  exit 1
fi
rm -f "$PROMOTION_BUNDLE_MANIFEST_CANONICAL"
jq -e \
  --arg summary "$PROMOTION_EVAL_SUMMARY_SHA" \
  --arg results "$PROMOTION_EVAL_RESULTS_SHA" '
    keys == ["results_sha256", "schema", "summary_sha256"]
    and .schema == "fare-assistant.eval-run-bundle.v1"
    and .summary_sha256 == $summary
    and .results_sha256 == $results
  ' "$PROMOTION_BUNDLE_MANIFEST" >/dev/null || {
  echo "promotion eval-bundle manifest disagrees with its pointer" >&2
  exit 1
}
[[ "$(sha256_hex "$PROMOTION_BUNDLE_MANIFEST")" \
    == "$PROMOTION_EVAL_CONTENT_ADDRESS" \
  && "$(sha256_hex "$PROMOTION_EVAL_BUNDLE/summary.json")" \
    == "$PROMOTION_EVAL_SUMMARY_SHA" \
  && "$(sha256_hex "$PROMOTION_EVAL_BUNDLE/results.jsonl")" \
    == "$PROMOTION_EVAL_RESULTS_SHA" ]] || {
  echo "promotion eval bundle does not match its content address or file digests" >&2
  rm -f "$PROMOTION_BUNDLE_MANIFEST"
  exit 1
}
rm -f "$PROMOTION_BUNDLE_MANIFEST"

POST_EVAL_SOURCE_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$POST_EVAL_SOURCE_REVISION" == "$SOURCE_REVISION" \
  && -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
  echo "source state changed during promotion evaluation" >&2
  exit 1
}

# Stage only from the runner's atomically published, content-addressed bundle.
# Re-hash both destinations against its pointer so a source swap between the
# two independent copies cannot compose a mixed evaluation.
install -m 0644 \
  "$PROMOTION_EVAL_BUNDLE/summary.json" \
  "$PROMOTION_BUILD/summary.json"
install -m 0644 \
  "$PROMOTION_EVAL_BUNDLE/results.jsonl" \
  "$PROMOTION_BUILD/results.jsonl"
[[ "$(sha256_hex "$PROMOTION_BUILD/summary.json")" \
    == "$PROMOTION_EVAL_SUMMARY_SHA" \
  && "$(sha256_hex "$PROMOTION_BUILD/results.jsonl")" \
    == "$PROMOTION_EVAL_RESULTS_SHA" ]] || {
  echo "staged promotion evidence differs from the atomic eval-bundle pointer" >&2
  exit 1
}

# The evaluation may be long. Re-read the immutable candidate and its runtime
# mode so an operator/runtime drift cannot be hidden behind the earlier smoke.
POST_EVAL_CANDIDATE_CONFIG="$(
  aws lambda get-function-configuration \
    --function-name "$FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --output json
)"
if ! same_versioned_release_config \
  "$PUBLISHED_CONFIG" "$POST_EVAL_CANDIDATE_CONFIG"; then
  echo "numbered candidate changed during promotion evaluation" >&2
  exit 1
fi
POST_EVAL_CODE_SHA="$(jq -r '.CodeSha256' <<<"$POST_EVAL_CANDIDATE_CONFIG")"
[[ "$POST_EVAL_CODE_SHA" == "$LOCAL_CODE_SHA" \
  && "$POST_EVAL_CODE_SHA" == "$CANDIDATE_CODE_SHA" ]] || {
  echo "post-evaluation candidate artifact digest does not match the local bundle" >&2
  exit 1
}
assert_managed_release_config \
  "$POST_EVAL_CANDIDATE_CONFIG" "post-evaluation numbered candidate"
POST_EVAL_RUNTIME_MODE="$(
  aws lambda get-runtime-management-config \
    --function-name "$FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --query UpdateRuntimeOn --output text
)"
[[ "$POST_EVAL_RUNTIME_MODE" == "FunctionUpdate" ]] || {
  echo "candidate runtime mode changed during promotion evaluation" >&2
  exit 1
}

# This is deliberately the only runtime projection written to disk. The
# complete Lambda environment contains a history-signing secret and must never
# cross into public promotion evidence.
jq -n \
  --arg source_revision "$SOURCE_REVISION" \
  --arg config_version "$CONFIG_VERSION" \
  --arg content_version "$CONTENT_VERSION" \
  --arg snapshot_version "$SNAPSHOT_VERSION" \
  --arg release_version "$RELEASE_VERSION" \
  --arg corpus_version "$PINNED_CORPUS_VERSION" \
  --arg artifact_code_sha256 "$POST_EVAL_CODE_SHA" \
  --arg function_version "$NEW_VERSION" '{
    source_revision: $source_revision,
    config_version: $config_version,
    content_version: $content_version,
    snapshot_version: $snapshot_version,
    release_version: $release_version,
    corpus_version: $corpus_version,
    artifact_code_sha256: $artifact_code_sha256,
    function_version: $function_version
  }' >"$PROMOTION_RUNTIME_EVIDENCE"
PROMOTION_BUILD_SUMMARY="$(
  cd "$ROOT"
  uv run python scripts/build_promotion_attestation.py \
    --runtime "$PROMOTION_RUNTIME_EVIDENCE" \
    --summary "$PROMOTION_BUILD/summary.json" \
    --results "$PROMOTION_BUILD/results.jsonl" \
    --output "$PROMOTION_BUILD/promotion.json"
)"
if ! jq -e \
  --arg output "$PROMOTION_BUILD/promotion.json" '
    .output_path == $output
    and (.attestation_sha256 | test("^[0-9a-f]{64}$"))
  ' <<<"$PROMOTION_BUILD_SUMMARY" >/dev/null; then
  echo "promotion attestation builder returned an invalid receipt" >&2
  exit 1
fi
PROMOTION_ATTESTATION_SHA="$(
  jq -r '.attestation_sha256' <<<"$PROMOTION_BUILD_SUMMARY"
)"
[[ -f "$PROMOTION_BUILD/promotion.json" \
  && ! -L "$PROMOTION_BUILD/promotion.json" ]] || {
  echo "promotion attestation was not written as a regular file" >&2
  exit 1
}
STAGED_SUMMARY_SHA="$(sha256_hex "$PROMOTION_BUILD/summary.json")"
STAGED_RESULTS_SHA="$(sha256_hex "$PROMOTION_BUILD/results.jsonl")"
STAGED_PROMOTION_SHA="$(sha256_hex "$PROMOTION_BUILD/promotion.json")"
[[ "$STAGED_PROMOTION_SHA" == "$PROMOTION_ATTESTATION_SHA" ]] || {
  echo "promotion builder receipt does not identify the exact staged attestation bytes" >&2
  exit 1
}

# The builder composes the attestation, but the shared verifier is the complete
# consumer predicate. Run it against the exact three staged files before those
# bytes are eligible for publication.
verify_promotion_trio \
  "$PROMOTION_BUILD" \
  "$STAGED_SUMMARY_SHA" \
  "$STAGED_RESULTS_SHA" \
  "$STAGED_PROMOTION_SHA" >/dev/null

# The canonical promotion attestation commits to the exact summary/results
# digests, so its SHA-256 is the content address for the closed evidence trio.
# Populate an unpublished sibling first; only the final pointer is replaced
# atomically after every copied byte re-matches the verified staging receipt.
PROMOTION_ARCHIVE="$BUILD/promotions/$NEW_VERSION/$PROMOTION_ATTESTATION_SHA"
PROMOTION_ARCHIVE_PARENT="$(dirname "$PROMOTION_ARCHIVE")"
mkdir -p "$PROMOTION_ARCHIVE_PARENT"
PROMOTION_ARCHIVE_STAGING="$(
  mktemp -d "$PROMOTION_ARCHIVE_PARENT/.${PROMOTION_ATTESTATION_SHA}.XXXXXX"
)"
for promotion_artifact in summary.json results.jsonl promotion.json; do
  install -m 0444 \
    "$PROMOTION_BUILD/$promotion_artifact" \
    "$PROMOTION_ARCHIVE_STAGING/$promotion_artifact"
done
if [[ -e "$PROMOTION_ARCHIVE" ]]; then
  [[ -d "$PROMOTION_ARCHIVE" && ! -L "$PROMOTION_ARCHIVE" ]] || {
    echo "retained promotion evidence path is not a regular directory" >&2
    exit 1
  }
  for promotion_artifact in summary.json results.jsonl promotion.json; do
    if [[ ! -f "$PROMOTION_ARCHIVE/$promotion_artifact" \
      || -L "$PROMOTION_ARCHIVE/$promotion_artifact" ]] \
        || ! cmp -s \
          "$PROMOTION_ARCHIVE_STAGING/$promotion_artifact" \
          "$PROMOTION_ARCHIVE/$promotion_artifact"; then
      echo "retained promotion evidence conflicts with the current artifact" >&2
      exit 1
    fi
  done
  rm -rf "$PROMOTION_ARCHIVE_STAGING"
else
  chmod 0555 "$PROMOTION_ARCHIVE_STAGING"
  mv "$PROMOTION_ARCHIVE_STAGING" "$PROMOTION_ARCHIVE"
fi
chmod 0555 "$PROMOTION_ARCHIVE"
for promotion_artifact in summary.json results.jsonl promotion.json; do
  if [[ -e "$PROMOTION_ARCHIVE/$promotion_artifact" ]]; then
    continue
  else
    echo "atomically published promotion bundle is incomplete" >&2
    exit 1
  fi
done

EVAL_BUNDLE_POINTER_TEMP="$(mktemp "$BUILD/.promotion-evidence-pointer.XXXXXX")"
jq -n -S -c \
  --arg bundle_path "$PROMOTION_ARCHIVE" \
  --arg content_address "$PROMOTION_ATTESTATION_SHA" \
  --arg function_version "$NEW_VERSION" \
  --arg summary_sha256 "$STAGED_SUMMARY_SHA" \
  --arg results_sha256 "$STAGED_RESULTS_SHA" \
  --arg promotion_sha256 "$STAGED_PROMOTION_SHA" '{
    schema: "fare-assistant.eval-bundle-pointer.v1",
    bundle_path: $bundle_path,
    content_address: $content_address,
    function_version: $function_version,
    summary_sha256: $summary_sha256,
    results_sha256: $results_sha256,
    promotion_sha256: $promotion_sha256
  }' >"$EVAL_BUNDLE_POINTER_TEMP"
chmod 0444 "$EVAL_BUNDLE_POINTER_TEMP"
mv "$EVAL_BUNDLE_POINTER_TEMP" "$EVAL_BUNDLE_POINTER"
[[ -f "$EVAL_BUNDLE_POINTER" && ! -L "$EVAL_BUNDLE_POINTER" ]] || {
  echo "promotion evidence pointer was not atomically published as a regular file" >&2
  exit 1
}

# Apply the function-wide cost ceiling before a first deployment creates any
# public route. Existing releases already carry this value; reapplying it is
# idempotent shared-infrastructure reconciliation.
aws lambda put-function-concurrency --function-name "$FN" --region "$REGION" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" >/dev/null

# Resolve the atomic pointer rather than trusting in-memory paths, recheck its
# content address and exact digests, then invoke the full consumer predicate
# immediately before the first possible candidate alias mutation.
EVAL_BUNDLE_POINTER_JSON="$(<"$EVAL_BUNDLE_POINTER")"
jq -e \
  --arg bundle_path "$PROMOTION_ARCHIVE" \
  --arg content_address "$PROMOTION_ATTESTATION_SHA" \
  --arg function_version "$NEW_VERSION" \
  --arg summary_sha256 "$STAGED_SUMMARY_SHA" \
  --arg results_sha256 "$STAGED_RESULTS_SHA" \
  --arg promotion_sha256 "$STAGED_PROMOTION_SHA" '
    keys == [
      "bundle_path",
      "content_address",
      "function_version",
      "promotion_sha256",
      "results_sha256",
      "schema",
      "summary_sha256"
    ]
    and .schema == "fare-assistant.eval-bundle-pointer.v1"
    and .bundle_path == $bundle_path
    and .content_address == $content_address
    and .function_version == $function_version
    and .summary_sha256 == $summary_sha256
    and .results_sha256 == $results_sha256
    and .promotion_sha256 == $promotion_sha256
  ' <<<"$EVAL_BUNDLE_POINTER_JSON" >/dev/null || {
  echo "promotion evidence pointer is malformed or changed after publication" >&2
  exit 1
}
POINTER_BUNDLE_PATH="$(jq -r '.bundle_path' <<<"$EVAL_BUNDLE_POINTER_JSON")"
POINTER_SUMMARY_SHA="$(jq -r '.summary_sha256' <<<"$EVAL_BUNDLE_POINTER_JSON")"
POINTER_RESULTS_SHA="$(jq -r '.results_sha256' <<<"$EVAL_BUNDLE_POINTER_JSON")"
POINTER_PROMOTION_SHA="$(jq -r '.promotion_sha256' <<<"$EVAL_BUNDLE_POINTER_JSON")"
verify_promotion_trio \
  "$POINTER_BUNDLE_PATH" \
  "$POINTER_SUMMARY_SHA" \
  "$POINTER_RESULTS_SHA" \
  "$POINTER_PROMOTION_SHA" >/dev/null
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$SOURCE_REVISION" \
  && -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
  echo "source state changed before promotion evidence was consumed" >&2
  exit 1
}

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
  jq -e \
    --arg version "$NEW_VERSION" \
    --arg description "$RELEASE_DESCRIPTION" '
      .FunctionVersion == $version
      and (.Description // "") == $description
    ' <<<"$LIVE_ALIAS_JSON" >/dev/null || {
    echo "$LIVE_ALIAS did not retain the candidate release identity description" >&2
    exit 1
  }
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
    PREVIOUS_ROLLBACK_DESCRIPTION="$(
      jq -r '.Description // ""' <<<"$PREVIOUS_ROLLBACK_JSON"
    )"
    ROLLBACK_POINTER_DESCRIPTION="$BASELINE_LIVE_DESCRIPTION"
    if [[ "$PREVIOUS_ROLLBACK_VERSION" != "$OLD_VERSION" \
      || "$PREVIOUS_ROLLBACK_DESCRIPTION" != "$ROLLBACK_POINTER_DESCRIPTION" ]]; then
      ROLLBACK_POINTER_GUARD_EXPECTED_VERSION="$OLD_VERSION"
      ROLLBACK_POINTER_GUARD_EXPECTED_REVISION=""
      ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION="$ROLLBACK_POINTER_DESCRIPTION"
      ROLLBACK_POINTER_GUARD_RESTORE_VERSION="$PREVIOUS_ROLLBACK_VERSION"
      ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION="$PREVIOUS_ROLLBACK_DESCRIPTION"
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
        --description "$BASELINE_LIVE_DESCRIPTION" \
        --region "$REGION" \
        --output json
    )"
  else
    echo "could not inspect rollback alias:" >&2
    echo "$PREVIOUS_ROLLBACK_JSON" >&2
    exit 1
  fi
  assert_unweighted_alias "$UPDATED_ROLLBACK_JSON" "$ROLLBACK_ALIAS"
  if ! jq -e \
    --arg version "$OLD_VERSION" \
    --arg description "$BASELINE_LIVE_DESCRIPTION" '
      .FunctionVersion == $version
      and (.Description // "") == $description
    ' <<<"$UPDATED_ROLLBACK_JSON" >/dev/null; then
    echo "$ROLLBACK_ALIAS did not settle on prior live version $OLD_VERSION" >&2
    exit 1
  fi
  UPDATED_ROLLBACK_REVISION="$(jq -r '.RevisionId' <<<"$UPDATED_ROLLBACK_JSON")"
  if [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]]; then
    ROLLBACK_POINTER_GUARD_EXPECTED_REVISION="$UPDATED_ROLLBACK_REVISION"
  fi

  PROMOTION_DESCRIPTION="$RELEASE_DESCRIPTION previous=$OLD_VERSION"
  (( ${#PROMOTION_DESCRIPTION} <= 256 )) || {
    echo "promotion alias description exceeds the Lambda 256-character limit" >&2
    exit 1
  }
  PROMOTION_GUARD_EXPECTED_VERSION="$NEW_VERSION"
  PROMOTION_GUARD_EXPECTED_REVISION=""
  PROMOTION_GUARD_EXPECTED_DESCRIPTION="$PROMOTION_DESCRIPTION"
  PROMOTION_GUARD_RESTORE_VERSION="$OLD_VERSION"
  PROMOTION_GUARD_RESTORE_DESCRIPTION="$BASELINE_LIVE_DESCRIPTION"
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
  jq -e \
    --arg version "$NEW_VERSION" \
    --arg description "$PROMOTION_DESCRIPTION" '
      .FunctionVersion == $version
      and (.Description // "") == $description
    ' <<<"$PROMOTED_LIVE_JSON" >/dev/null || {
    echo "promoted alias did not retain the candidate release identity description" >&2
    exit 1
  }

  if ! public_assistant_smoke "$DISABLED_DOC_IDS"; then
    echo "candidate $NEW_VERSION failed public smoke; rolling live back to $OLD_VERSION" >&2
    exit 1
  fi
  VERIFIED_LIVE_JSON="$(
    aws lambda get-alias \
      --function-name "$FN" --name "$LIVE_ALIAS" --region "$REGION" --output json
  )"
  assert_unweighted_alias "$VERIFIED_LIVE_JSON" "$LIVE_ALIAS"
  if ! jq -e \
    --arg version "$NEW_VERSION" \
    --arg revision "$PROMOTED_LIVE_REVISION" \
    --arg description "$PROMOTION_DESCRIPTION" '
      .FunctionVersion == $version
      and (.RevisionId // "") == $revision
      and (.Description // "") == $description
    ' <<<"$VERIFIED_LIVE_JSON" >/dev/null; then
    echo "live alias changed before promotion verification completed" >&2
    exit 1
  fi
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

# ── observability: alarms on errors, throttles, latency, and model cost ──────
# Alarms publish to an SNS topic; subscribe an email once to be paged:
#   aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint you@example.com
TOPIC_ARN="$(aws sns create-topic --name "$FN-alerts" --region "$REGION" \
  --query TopicArn --output text)"
SUBSCRIPTIONS="$(
  aws sns list-subscriptions-by-topic \
    --topic-arn "$TOPIC_ARN" --region "$REGION" --output json
)"
CONFIRMED_SUBSCRIPTIONS="$(
  jq '[.Subscriptions[]?
       | select(.SubscriptionArn != "PendingConfirmation")] | length' \
    <<<"$SUBSCRIPTIONS"
)"
if [[ "$CONFIRMED_SUBSCRIPTIONS" == "0" ]]; then
  echo "WARNING: $TOPIC_ARN has no confirmed subscriber; alarms are configured but cannot page an operator" >&2
fi

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
_alarm unpriced-model-calls "$FN" UnpricedModelCalls Sum 300 0
# p99 latency over 20s (the function timeout is 25s); Duration is in ms.
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "$FN-latency-p99" --namespace AWS/Lambda --metric-name Duration \
  --extended-statistic p99 --period 300 --threshold 20000 --evaluation-periods 1 \
  --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching \
  --dimensions "$LAMBDA_DIM" --alarm-actions "$TOPIC_ARN" >/dev/null
# Cost backstop: more than 500 answer-model calls in 5 minutes is well beyond
# demo traffic and trips before spend runs away (concurrency caps it anyway).
_alarm bedrock-surge "$FN" BedrockAnswerCalls Sum 300 500

# ── dashboard: estimated cost, real request/model latency, and alarms ─────────
# put-dashboard creates or overwrites by name, so this is idempotent. Cost is
# estimated from observed model/token usage and the repository-pinned price
# table; it is explicitly not an AWS billing metric or a substitute for Budget.
_alarm_arn() { echo "arn:aws:cloudwatch:$REGION:$ACCOUNT:alarm:$FN-$1"; }
DASHBOARD_BODY=$(cat <<EOF
{
  "widgets": [
    {
      "type": "metric", "x": 0, "y": 0, "width": 12, "height": 6,
      "properties": {
        "title": "Application-estimated model cost and calls per day",
        "region": "$REGION", "view": "timeSeries", "period": 86400,
        "metrics": [
          ["$FN", "EstimatedModelCostUsd", {"label": "Estimated USD", "stat": "Sum", "yAxis": "left"}],
          ["$FN", "GenAICalls", {"label": "Completed model calls", "stat": "Sum", "yAxis": "right"}],
          ["$FN", "UnpricedModelCalls", {"label": "Unpriced calls", "stat": "Sum", "yAxis": "right"}]
        ],
        "yAxis": {
          "left": {"label": "Estimated USD", "showUnits": false},
          "right": {"label": "Calls", "showUnits": false}
        }
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
        "title": "Request, model, and Lambda duration (ms)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$FN", "AnswerDurationMs", {"label": "Answer p50", "stat": "p50"}],
          ["$FN", "AnswerDurationMs", {"label": "Answer p95", "stat": "p95"}],
          ["$FN", "AnswerDurationMs", {"label": "Answer p99", "stat": "p99"}],
          ["$FN", "ModelDurationMs", {"label": "Model p95", "stat": "p95"}],
          ["AWS/Lambda", "Duration", "FunctionName", "$FN", {"label": "Lambda p99", "stat": "p99"}]
        ]
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
          "$(_alarm_arn unpriced-model-calls)",
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

# ── cost allocation: label everything this deploy owns ────────────────────────
# The `--tags` arguments further up only fire the first time a resource is
# created. This sweep re-applies `project` on EVERY deploy, so resources that
# predate the tagging (or that a half-finished run created before reaching this
# point) get labelled on the next run instead of sitting in the account's
# untagged bucket indefinitely. Tagging an already-correctly-tagged resource is
# a no-op, so re-running costs nothing.
#
# Deliberately not tagged, because AWS accepts no tags on them: CloudWatch
# metric filters, the CloudWatch dashboard, Lambda aliases and published
# versions (tags live on the function and cover every version), the API's
# `$default` stage and route, and the inline IAM role policy. None of them bill
# separately from a parent that is tagged here.
#
# A failure here does not fail the deploy: the service is live and verified by
# this point, and a billing label is not worth tearing that down. It is reported
# loudly instead, because untagged spend is invisible spend. The usual cause is
# deploy credentials without tag permissions -- `lambda:TagResource`,
# `iam:TagRole`, `logs:TagResource`, `apigateway:POST` on `/tags/*`,
# `sns:TagResource`, `cloudwatch:TagResource`.
UNTAGGED=""
_tag() {  # human-readable resource label, then the command that tags it
  local label="$1"
  shift
  "$@" >/dev/null 2>&1 || UNTAGGED="$UNTAGGED${UNTAGGED:+, }$label"
}
_tag "lambda function $FN" \
  aws lambda tag-resource --region "$REGION" \
  --resource "$UNQUALIFIED_ARN" --tags "$PROJECT_TAG_MAP"
_tag "iam role $ROLE_NAME" \
  aws iam tag-role --role-name "$ROLE_NAME" --tags "$PROJECT_TAG_LIST"
_tag "log group $LOG_GROUP" \
  aws logs tag-resource --region "$REGION" \
  --resource-arn "arn:aws:logs:$REGION:$ACCOUNT:log-group:$LOG_GROUP" \
  --tags "$PROJECT_TAG_MAP"
_tag "http api $API_ID" \
  aws apigatewayv2 tag-resource --region "$REGION" \
  --resource-arn "arn:aws:apigateway:$REGION::/apis/$API_ID" \
  --tags "$PROJECT_TAG_MAP"
_tag "sns topic $FN-alerts" \
  aws sns tag-resource --region "$REGION" \
  --resource-arn "$TOPIC_ARN" --tags "$PROJECT_TAG_LIST"
for alarm_suffix in handler-errors lambda-errors lambda-throttles \
  unpriced-model-calls latency-p99 bedrock-surge; do
  _tag "alarm $FN-$alarm_suffix" \
    aws cloudwatch tag-resource --region "$REGION" \
    --resource-arn "$(_alarm_arn "$alarm_suffix")" --tags "$PROJECT_TAG_LIST"
done
if [[ -n "$UNTAGGED" ]]; then
  echo "WARNING: could not apply project=$PROJECT_TAG to: $UNTAGGED" >&2
  echo "their spend stays in the untagged bucket, invisible to the fare-demo budget" >&2
else
  echo "cost allocation: project=$PROJECT_TAG applied to this deployment's resources"
fi

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
echo "content version: $CONTENT_VERSION"
echo "snapshot version: $SNAPSHOT_VERSION"
echo "config version: $CONFIG_VERSION"
echo "release version: $RELEASE_VERSION"
echo "promotion attestation sha256: $PROMOTION_ATTESTATION_SHA"
echo "promotion evidence: $PROMOTION_ARCHIVE"
echo "promotion evidence pointer: $EVAL_BUNDLE_POINTER"
echo "disabled documents pending review: $DISABLED_DOC_IDS"
echo "alerts topic: $TOPIC_ARN (subscribe an email to receive alarms)"
echo "dashboard: https://$REGION.console.aws.amazon.com/cloudwatch/home?region=$REGION#dashboards/dashboard/$FN"
