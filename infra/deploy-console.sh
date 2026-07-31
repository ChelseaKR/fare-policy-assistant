#!/usr/bin/env bash
# Deploy the read-only agency console as an immutable Lambda version.
#
# Required release inputs:
#
#   FPA_RIDER_FUNCTION_NAME=fare-policy-assistant-demo \
#   FPA_RIDER_ALIAS=live \
#   FPA_RIDER_BASE_URL=https://fare.example.gov \
#   FPA_CONSOLE_TOKEN_PARAMETER_NAME=/fare-policy-assistant/demo-console-token \
#   FPA_PROMOTION_EVIDENCE_DIR=/absolute/path/to/promoted-evidence \
#   ./infra/deploy-console.sh
#
# The evidence directory must contain exactly summary.json, results.jsonl, and
# promotion.json from one validated rider promotion. The console is published
# behind a qualified `live` alias; `rollback` retains the prior live version.
#
# The bearer token is suitable only for a single-operator pilot. Before sharing
# the URL broadly, put an agency-managed JWT/IAM authorizer in front of the API.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
RIDER_FN="${FPA_RIDER_FUNCTION_NAME:?set FPA_RIDER_FUNCTION_NAME}"
RIDER_ALIAS="${FPA_RIDER_ALIAS:-live}"
RIDER_BASE_URL="${FPA_RIDER_BASE_URL:?set FPA_RIDER_BASE_URL to the public rider origin}"
CONSOLE_FN="${FPA_CONSOLE_FUNCTION_NAME:-$RIDER_FN-console}"
LIVE_ALIAS="${FPA_CONSOLE_LIVE_ALIAS:-live}"
ROLLBACK_ALIAS="${FPA_CONSOLE_ROLLBACK_ALIAS:-rollback}"
CONSOLE_TOKEN_PARAMETER="${FPA_CONSOLE_TOKEN_PARAMETER_NAME:?set FPA_CONSOLE_TOKEN_PARAMETER_NAME}"
EVIDENCE_INPUT="${FPA_PROMOTION_EVIDENCE_DIR:?set FPA_PROMOTION_EVIDENCE_DIR}"
ROLE_NAME="$CONSOLE_FN-role"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/infra/build-console"
BUNDLE="$BUILD/bundle"
API_ID="${FPA_CONSOLE_API_ID:-}"
LOG_GROUP="/aws/lambda/$CONSOLE_FN"
LOGGING_CONFIG="LogFormat=JSON,ApplicationLogLevel=INFO,SystemLogLevel=WARN,LogGroup=$LOG_GROUP"
EMPTY_ALIAS_ROUTING='{"AdditionalVersionWeights":{}}'

for required_command in aws curl find git jq openssl sort uv; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "$required_command is required" >&2
    exit 2
  }
done

for lambda_name in "$RIDER_FN" "$CONSOLE_FN"; do
  [[ "$lambda_name" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || {
    echo "Lambda function names must be unqualified names containing only letters, numbers, _ and -" >&2
    exit 2
  }
done
[[ "$CONSOLE_FN" != "$RIDER_FN" ]] || {
  echo "the console must use a function separate from the rider Lambda" >&2
  exit 2
}
for alias_name in "$LIVE_ALIAS" "$ROLLBACK_ALIAS"; do
  [[ "$alias_name" =~ ^[A-Za-z0-9_-]{1,128}$ \
    && ! "$alias_name" =~ ^[0-9]+$ ]] || {
    echo "console aliases must be valid non-numeric Lambda alias names" >&2
    exit 2
  }
done
[[ "$LIVE_ALIAS" != "$ROLLBACK_ALIAS" ]] || {
  echo "console live and rollback aliases must be distinct" >&2
  exit 2
}
[[ -z "$API_ID" || "$API_ID" =~ ^[a-z0-9]+$ ]] || {
  echo "FPA_CONSOLE_API_ID must be an API Gateway API id" >&2
  exit 2
}

SOURCE_REVISION="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || {
  echo "source revision is not a full lowercase Git object id" >&2
  exit 2
}
if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]]; then
  echo "working tree is dirty; refusing an unattributable console release" >&2
  exit 2
fi
[[ "$RIDER_BASE_URL" =~ ^https://[^[:space:]]+$ ]] || {
  echo "FPA_RIDER_BASE_URL must be an HTTPS origin" >&2
  exit 2
}
[[ "$RIDER_ALIAS" == "live" ]] || {
  echo "FPA_RIDER_ALIAS must be live; the console cannot observe a mutable or rollback target" >&2
  exit 2
}
RIDER_BASE_URL="${RIDER_BASE_URL%/}"
[[ "$CONSOLE_TOKEN_PARAMETER" =~ ^/[A-Za-z0-9_./-]+$ ]] || {
  echo "FPA_CONSOLE_TOKEN_PARAMETER_NAME must be an absolute SSM parameter name" >&2
  exit 2
}
[[ -d "$EVIDENCE_INPUT" && ! -L "$EVIDENCE_INPUT" ]] || {
  echo "promotion evidence must be a non-symlink directory" >&2
  exit 2
}
[[ "$EVIDENCE_INPUT" == /* ]] || {
  echo "FPA_PROMOTION_EVIDENCE_DIR must be an absolute path" >&2
  exit 2
}
EVIDENCE_DIR="$(cd "$EVIDENCE_INPUT" && pwd -P)"
for evidence_name in summary.json results.jsonl promotion.json; do
  evidence_path="$EVIDENCE_DIR/$evidence_name"
  [[ -f "$evidence_path" && ! -L "$evidence_path" ]] || {
    echo "promotion evidence is missing regular file $evidence_name" >&2
    exit 2
  }
done
EVIDENCE_EXTRA="$(
  find "$EVIDENCE_DIR" -mindepth 1 -maxdepth 1 \
    ! -name summary.json ! -name results.jsonl ! -name promotion.json \
    -print -quit
)"
if [[ -n "$EVIDENCE_EXTRA" ]]; then
  echo "promotion evidence directory must contain exactly summary.json, results.jsonl, promotion.json" >&2
  exit 2
fi

# Use the same closed verifier the deployed console uses. Stale-but-valid
# evidence is allowed so the console can expose its explicit warning state;
# structural or identity disagreement is fatal before any AWS mutation.
EVIDENCE_STATUS="$(
  cd "$ROOT"
  FPA_DEPLOY_EVIDENCE_DIR="$EVIDENCE_DIR" uv run --frozen python -c '
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from assistant.promotion_evidence import verify_promotion_evidence

root = Path(os.environ["FPA_DEPLOY_EVIDENCE_DIR"])
evidence = verify_promotion_evidence(
    summary_path=root / "summary.json",
    results_path=root / "results.jsonl",
    promotion_path=root / "promotion.json",
    freshness_budget=timedelta(days=7),
    clock=lambda: datetime.now(UTC),
)
runtime = evidence.attestation.runtime_release
print(json.dumps(
    {
        "status": evidence.status,
        "summary_sha256": evidence.summary_sha256,
        "results_sha256": evidence.results_sha256,
        "promotion_sha256": evidence.promotion_sha256,
        "runtime_release": {
            "source_revision": runtime.source_revision,
            "config_version": runtime.config_version,
            "content_version": runtime.content_version,
            "snapshot_version": runtime.snapshot_version,
            "release_version": runtime.release_version,
            "corpus_version": runtime.corpus_version,
            "artifact_code_sha256": runtime.artifact_code_sha256,
            "function_version": runtime.function_version,
        },
    },
    sort_keys=True,
    separators=(",", ":"),
))
'
)"
jq -e '
  (.status == "verified" or .status == "warning")
  and (.summary_sha256 | test("^[0-9a-f]{64}$"))
  and (.results_sha256 | test("^[0-9a-f]{64}$"))
  and (.promotion_sha256 | test("^[0-9a-f]{64}$"))
  and (.runtime_release.source_revision | test("^[0-9a-f]{40}$"))
  and (.runtime_release.config_version | test("^[0-9a-f]{64}$"))
  and (.runtime_release.content_version | test("^[0-9a-f]{64}$"))
  and (.runtime_release.snapshot_version | test("^[0-9a-f]{64}$"))
  and (.runtime_release.release_version | test("^[0-9a-f]{64}$"))
  and (.runtime_release.corpus_version | test("^[0-9a-f]{12}$"))
  and (.runtime_release.artifact_code_sha256 | test("^[A-Za-z0-9+/]{43}=$"))
  and (.runtime_release.function_version | test("^[1-9][0-9]*$"))
' <<<"$EVIDENCE_STATUS" >/dev/null || {
  echo "promotion evidence verifier returned an invalid receipt" >&2
  exit 2
}
PROMOTION_SHA256="$(jq -r '.promotion_sha256' <<<"$EVIDENCE_STATUS")"
PROMOTION_RUNTIME="$(jq -c '.runtime_release' <<<"$EVIDENCE_STATUS")"

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

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
[[ "$ACCOUNT" =~ ^[0-9]{12}$ ]] || {
  echo "could not resolve a canonical AWS account id" >&2
  exit 1
}

assert_unweighted_alias() {
  local alias_json="$1"
  local alias_name="$2"
  jq -e '((.RoutingConfig.AdditionalVersionWeights // {}) | length) == 0' \
    <<<"$alias_json" >/dev/null || {
    echo "Lambda alias $alias_name has weighted routing" >&2
    return 1
  }
}

verify_rider_evidence() {
  local before
  local after
  local rider_version
  local rider_revision
  local config_json
  local runtime_json

  before="$(
    aws lambda get-alias \
      --function-name "$RIDER_FN" --name "$RIDER_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$before" "$RIDER_FN:$RIDER_ALIAS"
  rider_version="$(jq -r '.FunctionVersion // ""' <<<"$before")"
  rider_revision="$(jq -r '.RevisionId // ""' <<<"$before")"
  [[ "$rider_version" =~ ^[1-9][0-9]*$ && -n "$rider_revision" ]] || {
    echo "rider alias is not a guarded numeric release" >&2
    exit 1
  }
  config_json="$(
    aws lambda get-function-configuration \
      --function-name "$RIDER_FN" --qualifier "$rider_version" \
      --region "$REGION" --output json
  )"
  runtime_json="$(
    curl --silent --show-error --fail --location \
      --connect-timeout 5 --max-time 15 \
      "$RIDER_BASE_URL/version"
  )"
  after="$(
    aws lambda get-alias \
      --function-name "$RIDER_FN" --name "$RIDER_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$after" "$RIDER_FN:$RIDER_ALIAS"
  jq -e \
    --arg version "$rider_version" \
    --arg revision "$rider_revision" '
      .FunctionVersion == $version
      and (.RevisionId // "") == $revision
    ' <<<"$after" >/dev/null || {
    echo "rider alias changed while its promotion evidence was verified" >&2
    exit 1
  }
  jq -e --argjson promotion "$PROMOTION_RUNTIME" --arg version "$rider_version" '
    .Version == $version
    and .CodeSha256 == $promotion.artifact_code_sha256
    and .Environment.Variables.FPA_SOURCE_REVISION == $promotion.source_revision
    and .Environment.Variables.FPA_CONFIG_VERSION == $promotion.config_version
    and .Environment.Variables.FPA_PINNED_CONTENT_VERSION == $promotion.content_version
    and .Environment.Variables.FPA_PINNED_SNAPSHOT_VERSION == $promotion.snapshot_version
    and .Environment.Variables.FPA_RELEASE_VERSION == $promotion.release_version
    and .Environment.Variables.FPA_PINNED_CORPUS_VERSION == $promotion.corpus_version
    and $version == $promotion.function_version
  ' <<<"$config_json" >/dev/null || {
    echo "qualified rider configuration does not match promotion evidence" >&2
    exit 1
  }
  jq -e --argjson promotion "$PROMOTION_RUNTIME" --arg version "$rider_version" '
    .identity_status == "verified"
    and .function_version == $version
    and .source_revision == $promotion.source_revision
    and .config_version == $promotion.config_version
    and .content_version == $promotion.content_version
    and .snapshot_version == $promotion.snapshot_version
    and .release_version == $promotion.release_version
    and .corpus_version == $promotion.corpus_version
    and .artifact_code_sha256 == $promotion.artifact_code_sha256
  ' <<<"$runtime_json" >/dev/null || {
    echo "public rider runtime does not match promotion evidence" >&2
    exit 1
  }
}

# Resolve the rider/evidence tuple before spending time on the console bundle.
verify_rider_evidence

# ── deterministic bundle ─────────────────────────────────────────────────────
rm -rf "$BUNDLE"
rm -f "$BUILD/bundle.zip"
mkdir -p \
  "$BUNDLE/src" \
  "$BUNDLE/corpus/processed" \
  "$BUNDLE/evals/promoted" \
  "$BUNDLE/web" \
  "$BUILD/generated"

# This is intentionally the repository's full hash-pinned runtime export. It is
# larger than the console's transitive subset, but it guarantees every imported
# package is locked and hash-verified until a dedicated console lock is added.
uv pip install --quiet --target "$BUNDLE" \
  --python-platform aarch64-manylinux_2_28 --python-version 3.12 --only-binary :all: \
  --require-hashes -r "$ROOT/infra/requirements-deploy.txt"

(
  cd "$ROOT"
  uv run --frozen python scripts/copy_tracked_bundle.py \
    --repo-root "$ROOT" \
    --destination "$BUNDLE" \
    --tree src/assistant \
    --file corpus/processed/chunks.jsonl \
    --file web/__init__.py \
    --file web/console.py \
    --file web/a11y.py
)
(
  cd "$ROOT"
  uv run --frozen python -m assistant.corpus history >"$BUILD/generated/version_history.json"
)
cp "$BUILD/generated/version_history.json" "$BUNDLE/corpus/version_history.json"
for evidence_name in summary.json results.jsonl promotion.json; do
  cp "$EVIDENCE_DIR/$evidence_name" "$BUNDLE/evals/promoted/$evidence_name"
  case "$evidence_name" in
    summary.json)
      expected_evidence_sha="$(jq -r '.summary_sha256' <<<"$EVIDENCE_STATUS")"
      ;;
    results.jsonl)
      expected_evidence_sha="$(jq -r '.results_sha256' <<<"$EVIDENCE_STATUS")"
      ;;
    promotion.json)
      expected_evidence_sha="$PROMOTION_SHA256"
      ;;
  esac
  [[ "$(sha256_hex "$BUNDLE/evals/promoted/$evidence_name")" \
    == "$expected_evidence_sha" ]] || {
    echo "bundled promotion evidence differs from the verified bytes: $evidence_name" >&2
    exit 1
  }
done
(
  cd "$ROOT"
  uv run --frozen python scripts/build_lambda_zip.py "$BUNDLE" "$BUILD/bundle.zip"
)
LOCAL_CODE_SHA="$(
  openssl dgst -sha256 -binary "$BUILD/bundle.zip" | openssl base64 -A
)"
[[ "$LOCAL_CODE_SHA" =~ ^[A-Za-z0-9+/]{43}=$ ]] || {
  echo "could not compute the AWS-style console bundle digest" >&2
  exit 1
}

# Recheck after all local generation. No generated evidence or history is
# allowed to conceal a concurrent source edit.
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$SOURCE_REVISION" \
  && -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
  echo "source changed while the console artifact was built" >&2
  exit 1
}

# ── guarded alias cleanup ────────────────────────────────────────────────────
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

restore_unverified_live() {
  local current_alias
  local current_version
  local current_revision
  local current_description
  local restored_alias

  [[ "$PROMOTION_GUARD_ACTIVE" == "true" ]] || return 0
  PROMOTION_GUARD_ACTIVE=false
  current_alias="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )" || return 1
  current_version="$(jq -r '.FunctionVersion // ""' <<<"$current_alias")"
  current_revision="$(jq -r '.RevisionId // ""' <<<"$current_alias")"
  current_description="$(jq -r '.Description // ""' <<<"$current_alias")"
  if [[ "$current_version" == "$PROMOTION_GUARD_RESTORE_VERSION" \
    && "$current_description" == "$PROMOTION_GUARD_RESTORE_DESCRIPTION" ]]; then
    assert_unweighted_alias "$current_alias" "$LIVE_ALIAS"
    return 0
  fi
  if [[ "$current_version" != "$PROMOTION_GUARD_EXPECTED_VERSION" \
    || "$current_description" != "$PROMOTION_GUARD_EXPECTED_DESCRIPTION" \
    || ( -n "$PROMOTION_GUARD_EXPECTED_REVISION" \
      && "$current_revision" != "$PROMOTION_GUARD_EXPECTED_REVISION" ) ]]; then
    echo "WARNING: console live alias changed; cleanup did not overwrite it" >&2
    return 1
  fi
  restored_alias="$(
    aws lambda update-alias \
      --function-name "$CONSOLE_FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$PROMOTION_GUARD_RESTORE_VERSION" \
      --revision-id "$current_revision" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$PROMOTION_GUARD_RESTORE_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )" || return 1
  assert_unweighted_alias "$restored_alias" "$LIVE_ALIAS"
  jq -e \
    --arg version "$PROMOTION_GUARD_RESTORE_VERSION" \
    --arg description "$PROMOTION_GUARD_RESTORE_DESCRIPTION" '
      .FunctionVersion == $version and (.Description // "") == $description
    ' <<<"$restored_alias" >/dev/null
}

restore_previous_rollback_pointer() {
  local current_alias
  local current_version
  local current_revision
  local current_description
  local restored_alias

  [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]] || return 0
  ROLLBACK_POINTER_GUARD_ACTIVE=false
  current_alias="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json
  )" || return 1
  current_version="$(jq -r '.FunctionVersion // ""' <<<"$current_alias")"
  current_revision="$(jq -r '.RevisionId // ""' <<<"$current_alias")"
  current_description="$(jq -r '.Description // ""' <<<"$current_alias")"
  if [[ "$current_version" == "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" \
    && "$current_description" == "$ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION" ]]; then
    assert_unweighted_alias "$current_alias" "$ROLLBACK_ALIAS"
    return 0
  fi
  if [[ "$current_version" != "$ROLLBACK_POINTER_GUARD_EXPECTED_VERSION" \
    || "$current_description" != "$ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION" \
    || ( -n "$ROLLBACK_POINTER_GUARD_EXPECTED_REVISION" \
      && "$current_revision" != "$ROLLBACK_POINTER_GUARD_EXPECTED_REVISION" ) ]]; then
    echo "WARNING: console rollback alias changed; cleanup did not overwrite it" >&2
    return 1
  fi
  restored_alias="$(
    aws lambda update-alias \
      --function-name "$CONSOLE_FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$ROLLBACK_POINTER_GUARD_RESTORE_VERSION" \
      --revision-id "$current_revision" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION" \
      --region "$REGION" \
      --output json
  )" || return 1
  assert_unweighted_alias "$restored_alias" "$ROLLBACK_ALIAS"
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

normalized_console_config() {
  jq -S -c '{
    CodeSha256,
    Runtime,
    Handler,
    Architectures: (.Architectures // []),
    Timeout,
    MemorySize,
    Role,
    PackageType: (.PackageType // "Zip"),
    Environment: {Variables: (.Environment.Variables // {})},
    Layers: [(.Layers // [])[] | .Arn],
    VpcConfig: {
      SubnetIds: ((.VpcConfig.SubnetIds // []) | sort),
      SecurityGroupIds: ((.VpcConfig.SecurityGroupIds // []) | sort),
      Ipv6AllowedForDualStack: (.VpcConfig.Ipv6AllowedForDualStack // false)
    },
    DeadLetterConfig: (.DeadLetterConfig // {}),
    TracingConfig: (.TracingConfig // {}),
    KMSKeyArn: (.KMSKeyArn // ""),
    FileSystemConfigs: (.FileSystemConfigs // []),
    EphemeralStorage: (.EphemeralStorage // {Size: 512}),
    SnapStart: {ApplyOn: (.SnapStart.ApplyOn // "None")},
    LoggingConfig: (.LoggingConfig // {})
  }' <<<"$1"
}

same_console_config() {
  [[ "$(normalized_console_config "$1")" == "$(normalized_console_config "$2")" ]]
}

exact_published_version() {
  local candidate="$1"
  local versions
  local version
  local version_config

  versions="$(
    aws lambda list-versions-by-function \
      --function-name "$CONSOLE_FN" --region "$REGION" \
      --query "Versions[?Version!=\`\$LATEST\`].Version" --output json
  )"
  while IFS= read -r version; do
    [[ "$version" =~ ^[1-9][0-9]*$ ]] || continue
    version_config="$(
      aws lambda get-function-configuration \
        --function-name "$CONSOLE_FN" --qualifier "$version" \
        --region "$REGION" --output json
    )"
    if same_console_config "$candidate" "$version_config"; then
      printf '%s\n' "$version"
      return 0
    fi
  done < <(jq -r '.[]' <<<"$versions" | sort -rn)
}

console_static_health() {
  local version="$1"
  local health_dir
  local response
  local payload

  health_dir="$(mktemp -d "${TMPDIR:-/tmp}/fare-console-health.XXXXXX")"
  response="$(
    aws lambda invoke \
      --function-name "$CONSOLE_FN" \
      --qualifier "$version" \
      --cli-binary-format raw-in-base64-out \
      --payload '{"version":"2.0","rawPath":"/console","requestContext":{"http":{"method":"GET","path":"/console"}}}' \
      --region "$REGION" \
      "$health_dir/response.json" \
      --output json
  )"
  jq -e '.StatusCode == 200 and (has("FunctionError") | not)' <<<"$response" >/dev/null || {
    echo "console candidate invocation failed for version $version" >&2
    return 1
  }
  payload="$(<"$health_dir/response.json")"
  jq -e '
    .statusCode == 200
    and (.headers["content-type"] | startswith("text/html"))
    and (.body | contains("Agency operator console"))
  ' <<<"$payload" >/dev/null || {
    echo "console candidate did not return the expected static shell" >&2
    return 1
  }
}

console_candidate_health() {
  local version="$1"
  local health_dir
  local response
  local payload

  console_static_health "$version"
  health_dir="$(mktemp -d "${TMPDIR:-/tmp}/fare-console-auth-health.XXXXXX")"
  response="$(
    aws lambda invoke \
      --function-name "$CONSOLE_FN" \
      --qualifier "$version" \
      --cli-binary-format raw-in-base64-out \
      --payload '{"version":"2.0","rawPath":"/console/api/status","headers":{},"requestContext":{"http":{"method":"GET","path":"/console/api/status"}}}' \
      --region "$REGION" \
      "$health_dir/response.json" \
      --output json
  )"
  jq -e '.StatusCode == 200 and (has("FunctionError") | not)' <<<"$response" >/dev/null || {
    echo "console candidate authentication probe failed for version $version" >&2
    return 1
  }
  payload="$(<"$health_dir/response.json")"
  jq -e '.statusCode == 401 and (.body | fromjson | .error == "Unauthorized.")' \
    <<<"$payload" >/dev/null || {
    echo "console candidate did not fail closed without an operator token" >&2
    return 1
  }
}

# ── API Gateway: always targets the qualified console alias ──────────────────
ALIAS_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$CONSOLE_FN:$LIVE_ALIAS"
UNQUALIFIED_ARN="arn:aws:lambda:$REGION:$ACCOUNT:function:$CONSOLE_FN"
ALIAS_INTEGRATION_URI="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$ALIAS_ARN/invocations"
UNQUALIFIED_INTEGRATION_URI="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$UNQUALIFIED_ARN/invocations"
API_EXISTS=false
INTEGRATION_ID=""
INTEGRATION_URI=""
API_URL=""

discover_api() {
  local ids
  local count
  if [[ -n "$API_ID" ]]; then
    aws apigatewayv2 get-api --api-id "$API_ID" --region "$REGION" >/dev/null
    API_EXISTS=true
    return
  fi
  ids="$(
    aws apigatewayv2 get-apis --region "$REGION" \
      --query "Items[?Name=='$CONSOLE_FN'].ApiId" --output json
  )"
  count="$(jq 'length' <<<"$ids")"
  if [[ "$count" == "0" ]]; then
    API_EXISTS=false
  elif [[ "$count" == "1" ]]; then
    API_ID="$(jq -r '.[0]' <<<"$ids")"
    API_EXISTS=true
  else
    echo "multiple HTTP APIs are named $CONSOLE_FN; set FPA_CONSOLE_API_ID" >&2
    exit 1
  fi
}

refresh_integration() {
  local integrations
  [[ "$API_EXISTS" == "true" ]] || return 0
  integrations="$(
    aws apigatewayv2 get-integrations \
      --api-id "$API_ID" --region "$REGION" --query Items --output json
  )"
  [[ "$(jq 'length' <<<"$integrations")" == "1" ]] || {
    echo "console API must have exactly one integration" >&2
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
  local policy
  local source_arn="arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*"
  local before
  local after
  local before_version

  before="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$before" "$LIVE_ALIAS"
  before_version="$(jq -r '.FunctionVersion // ""' <<<"$before")"
  if policy="$(
    aws lambda get-policy \
      --function-name "$CONSOLE_FN" --qualifier "$LIVE_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    if jq -e --arg source "$source_arn" --arg resource "$ALIAS_ARN" '
      .Policy | fromjson | any(.Statement[];
        .Sid == "apigw-live"
        and .Effect == "Allow"
        and .Action == "lambda:InvokeFunction"
        and .Resource == $resource
        and .Principal.Service == "apigateway.amazonaws.com"
        and .Condition.ArnLike["AWS:SourceArn"] == $source)
    ' <<<"$policy" >/dev/null; then
      return 0
    fi
    if jq -e '.Policy | fromjson | any(.Statement[]; .Sid == "apigw-live")' \
      <<<"$policy" >/dev/null; then
      echo "existing apigw-live permission has unexpected scope" >&2
      exit 1
    fi
  elif [[ "$policy" != *"ResourceNotFoundException"* ]]; then
    echo "could not inspect qualified console permission" >&2
    echo "$policy" >&2
    exit 1
  fi
  aws lambda add-permission \
    --function-name "$CONSOLE_FN" \
    --qualifier "$LIVE_ALIAS" \
    --statement-id apigw-live \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-account "$ACCOUNT" \
    --source-arn "$source_arn" \
    --region "$REGION" >/dev/null
  after="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$after" "$LIVE_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$after")" == "$before_version" ]] || {
    echo "console live alias changed during permission setup" >&2
    exit 1
  }
  BASELINE_LIVE_VERSION="$before_version"
  BASELINE_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$after")"
  BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$after")"
}

public_console_smoke() {
  local body
  [[ -n "$API_URL" ]] || {
    API_URL="$(
      aws apigatewayv2 get-api \
        --api-id "$API_ID" --region "$REGION" \
        --query ApiEndpoint --output text
    )"
  }
  body="$(
    curl --silent --show-error --fail --location \
      --connect-timeout 5 --max-time 15 \
      "$API_URL/console"
  )"
  [[ "$body" == *"Agency operator console"* ]] || {
    echo "public console smoke returned an unexpected document" >&2
    return 1
  }
}

remove_unqualified_permission() {
  local removal
  if ! removal="$(
    aws lambda remove-permission \
      --function-name "$CONSOLE_FN" --statement-id apigw \
      --region "$REGION" 2>&1
  )"; then
    [[ "$removal" == *"ResourceNotFoundException"* ]] || {
      echo "could not remove obsolete unqualified console permission" >&2
      echo "$removal" >&2
      exit 1
    }
  fi
}

ensure_api_targets_live() {
  local original_uri
  local observed_uri
  local attempt

  discover_api
  if [[ "$API_EXISTS" != "true" ]]; then
    API_ID="$(
      aws apigatewayv2 create-api \
        --region "$REGION" \
        --name "$CONSOLE_FN" \
        --protocol-type HTTP \
        --target "$ALIAS_ARN" \
        --query ApiId --output text
    )"
    API_EXISTS=true
  fi
  refresh_integration
  ensure_alias_permission
  if integration_targets_live_alias; then
    public_console_smoke
    remove_unqualified_permission
    return 0
  fi
  integration_targets_unqualified_function || {
    echo "console API targets unexpected integration $INTEGRATION_URI" >&2
    exit 1
  }
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
  if [[ "$observed_uri" != "$ALIAS_INTEGRATION_URI" ]] || ! public_console_smoke; then
    echo "qualified console route migration failed; restoring the prior integration" >&2
    aws apigatewayv2 update-integration \
      --api-id "$API_ID" \
      --integration-id "$INTEGRATION_ID" \
      --integration-uri "$original_uri" \
      --region "$REGION" >/dev/null
    exit 1
  fi
  INTEGRATION_URI="$ALIAS_INTEGRATION_URI"
  remove_unqualified_permission
}

ensure_rollback_alias_exists() {
  local rollback_json
  local rollback_version

  if rollback_json="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
    rollback_version="$(jq -r '.FunctionVersion // ""' <<<"$rollback_json")"
    [[ "$rollback_version" =~ ^[1-9][0-9]*$ ]] || {
      echo "console rollback alias is not a numeric immutable release" >&2
      return 1
    }
    return 0
  fi
  [[ "$rollback_json" == *"ResourceNotFoundException"* ]] || {
    echo "could not inspect console rollback alias" >&2
    echo "$rollback_json" >&2
    return 1
  }
  rollback_json="$(
    aws lambda create-alias \
      --function-name "$CONSOLE_FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$BASELINE_LIVE_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$BASELINE_LIVE_DESCRIPTION" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$rollback_json" "$ROLLBACK_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$rollback_json")" \
    == "$BASELINE_LIVE_VERSION" ]] || {
    echo "created console rollback alias points to an unexpected release" >&2
    return 1
  }
}

# ── IAM and immutable Lambda state ───────────────────────────────────────────
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:$REGION:$ACCOUNT:log-group:$LOG_GROUP*"
    },
    {
      "Effect": "Allow",
      "Action": "lambda:GetAlias",
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$RIDER_FN:live"
    },
    {
      "Effect": "Allow",
      "Action": "lambda:GetFunctionConfiguration",
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$RIDER_FN:*"
    },
    {
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:$REGION:$ACCOUNT:parameter$CONSOLE_TOKEN_PARAMETER"
    }
  ]
}
EOF
)

# Prove the configured token exists without reading its plaintext value.
aws ssm get-parameter \
  --name "$CONSOLE_TOKEN_PARAMETER" \
  --with-decryption \
  --query Parameter.ARN \
  --output text \
  --region "$REGION" >/dev/null

ROLE_CREATED=false
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  ROLE_CREATED=true
fi
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "$CONSOLE_FN-policy" \
  --policy-document "$POLICY"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/$ROLE_NAME"
if [[ "$ROLE_CREATED" == "true" ]]; then
  sleep 10
fi

aws logs create-log-group \
  --log-group-name "$LOG_GROUP" --region "$REGION" 2>/dev/null || true
aws logs put-retention-policy \
  --log-group-name "$LOG_GROUP" --retention-in-days 14 --region "$REGION"

CONSOLE_ENV="$(
  jq -nc \
    --arg token_parameter "$CONSOLE_TOKEN_PARAMETER" \
    --arg rider_function "$RIDER_FN" \
    --arg rider_alias "$RIDER_ALIAS" \
    --arg rider_base_url "$RIDER_BASE_URL" '{
      Variables: {
        FPA_CONSOLE_TOKEN_PARAMETER_NAME: $token_parameter,
        FPA_RIDER_FUNCTION_NAME: $rider_function,
        FPA_RIDER_ALIAS: $rider_alias,
        FPA_RIDER_BASE_URL: $rider_base_url
      }
    }'
)"

FUNCTION_EXISTS=false
HAS_LIVE_ALIAS=false
BASELINE_LIVE_VERSION=""
BASELINE_LIVE_REVISION=""
BASELINE_LIVE_DESCRIPTION=""
if aws lambda get-function \
  --function-name "$CONSOLE_FN" --region "$REGION" >/dev/null 2>&1; then
  FUNCTION_EXISTS=true
  if LIVE_ALIAS_JSON="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    HAS_LIVE_ALIAS=true
    assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
    BASELINE_LIVE_VERSION="$(jq -r '.FunctionVersion // ""' <<<"$LIVE_ALIAS_JSON")"
    BASELINE_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
    BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$LIVE_ALIAS_JSON")"
    [[ "$BASELINE_LIVE_VERSION" =~ ^[1-9][0-9]*$ \
      && -n "$BASELINE_LIVE_REVISION" ]] || {
      echo "console live alias is not a guarded numeric release" >&2
      exit 1
    }
  elif [[ "$LIVE_ALIAS_JSON" != *"ResourceNotFoundException"* ]]; then
    echo "could not inspect console live alias" >&2
    echo "$LIVE_ALIAS_JSON" >&2
    exit 1
  fi
fi

# One-time migration from the historical unqualified console. Freeze the exact
# current state, route traffic through aliases, and only then stage new code.
if [[ "$FUNCTION_EXISTS" == "true" && "$HAS_LIVE_ALIAS" != "true" ]]; then
  BOOTSTRAP_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$CONSOLE_FN" --region "$REGION" --output json
  )"
  BOOTSTRAP_CODE_SHA="$(jq -r '.CodeSha256 // ""' <<<"$BOOTSTRAP_CONFIG")"
  BOOTSTRAP_REVISION="$(jq -r '.RevisionId // ""' <<<"$BOOTSTRAP_CONFIG")"
  [[ -n "$BOOTSTRAP_CODE_SHA" && -n "$BOOTSTRAP_REVISION" ]] || {
    echo "mutable console has no publishable code/revision snapshot" >&2
    exit 1
  }
  BOOTSTRAP_VERSION="$(exact_published_version "$BOOTSTRAP_CONFIG")"
  if [[ -z "$BOOTSTRAP_VERSION" ]]; then
    BOOTSTRAP_VERSION="$(
      aws lambda publish-version \
        --function-name "$CONSOLE_FN" \
        --code-sha256 "$BOOTSTRAP_CODE_SHA" \
        --revision-id "$BOOTSTRAP_REVISION" \
        --description "bootstrap immutable console" \
        --region "$REGION" \
        --query Version --output text
    )"
  fi
  aws lambda wait published-version-active \
    --function-name "$CONSOLE_FN" --qualifier "$BOOTSTRAP_VERSION" --region "$REGION"
  aws lambda put-runtime-management-config \
    --function-name "$CONSOLE_FN" --qualifier "$BOOTSTRAP_VERSION" \
    --update-runtime-on FunctionUpdate --region "$REGION" >/dev/null
  console_static_health "$BOOTSTRAP_VERSION"
  LIVE_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$CONSOLE_FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$BOOTSTRAP_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "retained pre-hardening console" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  ROLLBACK_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$CONSOLE_FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$BOOTSTRAP_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "retained pre-hardening console" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$ROLLBACK_ALIAS_JSON" "$ROLLBACK_ALIAS"
  HAS_LIVE_ALIAS=true
  BASELINE_LIVE_VERSION="$BOOTSTRAP_VERSION"
  BASELINE_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
  BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$LIVE_ALIAS_JSON")"
  ensure_api_targets_live
elif [[ "$HAS_LIVE_ALIAS" == "true" ]]; then
  # Complete a prior route migration before touching mutable $LATEST.
  ensure_rollback_alias_exists
  ensure_api_targets_live
fi

# Stage the new artifact only after existing traffic is immutable.
if [[ "$FUNCTION_EXISTS" == "true" ]]; then
  PRESTAGE_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$CONSOLE_FN" --region "$REGION" --output json
  )"
  LATEST_REVISION="$(jq -r '.RevisionId // ""' <<<"$PRESTAGE_CONFIG")"
  [[ -n "$LATEST_REVISION" ]] || {
    echo "mutable console configuration has no revision id" >&2
    exit 1
  }
  aws lambda update-function-configuration \
    --function-name "$CONSOLE_FN" \
    --region "$REGION" \
    --runtime python3.12 \
    --handler web.console.console_handler \
    --timeout 15 \
    --memory-size 256 \
    --role "$ROLE_ARN" \
    --revision-id "$LATEST_REVISION" \
    --environment "$CONSOLE_ENV" \
    --logging-config "$LOGGING_CONFIG" >/dev/null
  aws lambda wait function-updated --function-name "$CONSOLE_FN" --region "$REGION"
  SETTLED_CONFIG="$(
    aws lambda get-function-configuration \
      --function-name "$CONSOLE_FN" --region "$REGION" --output json
  )"
  LATEST_REVISION="$(jq -r '.RevisionId // ""' <<<"$SETTLED_CONFIG")"
  [[ -n "$LATEST_REVISION" ]] || {
    echo "settled console configuration has no revision id" >&2
    exit 1
  }
  aws lambda update-function-code \
    --function-name "$CONSOLE_FN" \
    --region "$REGION" \
    --architectures arm64 \
    --revision-id "$LATEST_REVISION" \
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
else
  aws lambda create-function \
    --function-name "$CONSOLE_FN" \
    --region "$REGION" \
    --runtime python3.12 \
    --handler web.console.console_handler \
    --architectures arm64 \
    --timeout 15 \
    --memory-size 256 \
    --role "$ROLE_ARN" \
    --environment "$CONSOLE_ENV" \
    --logging-config "$LOGGING_CONFIG" \
    --zip-file "fileb://$BUILD/bundle.zip" >/dev/null
  FUNCTION_EXISTS=true
fi
aws lambda wait function-updated --function-name "$CONSOLE_FN" --region "$REGION"

CANDIDATE_CONFIG="$(
  aws lambda get-function-configuration \
    --function-name "$CONSOLE_FN" --region "$REGION" --output json
)"
CANDIDATE_CODE_SHA="$(jq -r '.CodeSha256 // ""' <<<"$CANDIDATE_CONFIG")"
CANDIDATE_REVISION="$(jq -r '.RevisionId // ""' <<<"$CANDIDATE_CONFIG")"
[[ "$CANDIDATE_CODE_SHA" == "$LOCAL_CODE_SHA" && -n "$CANDIDATE_REVISION" ]] || {
  echo "staged console does not match the deterministic local artifact" >&2
  exit 1
}
jq -e \
  --argjson environment "$CONSOLE_ENV" \
  --arg role "$ROLE_ARN" \
  --arg log_group "$LOG_GROUP" '
    .Runtime == "python3.12"
    and .Handler == "web.console.console_handler"
    and .Architectures == ["arm64"]
    and .Timeout == 15
    and .MemorySize == 256
    and .Role == $role
    and .Environment == $environment
    and .LoggingConfig.LogFormat == "JSON"
    and .LoggingConfig.ApplicationLogLevel == "INFO"
    and .LoggingConfig.SystemLogLevel == "WARN"
    and .LoggingConfig.LogGroup == $log_group
  ' <<<"$CANDIDATE_CONFIG" >/dev/null || {
  echo "staged console configuration does not match the reviewed contract" >&2
  exit 1
}

RELEASE_DESCRIPTION="source=${SOURCE_REVISION:0:12} evidence=${PROMOTION_SHA256:0:12} artifact=${CANDIDATE_CODE_SHA:0:12}"
MATCHING_VERSION="$(exact_published_version "$CANDIDATE_CONFIG")"
if [[ -n "$MATCHING_VERSION" ]]; then
  NEW_VERSION="$MATCHING_VERSION"
  echo "reusing exact console candidate version $NEW_VERSION"
else
  NEW_VERSION="$(
    aws lambda publish-version \
      --function-name "$CONSOLE_FN" \
      --code-sha256 "$CANDIDATE_CODE_SHA" \
      --revision-id "$CANDIDATE_REVISION" \
      --description "$RELEASE_DESCRIPTION" \
      --region "$REGION" \
      --query Version --output text
  )"
fi
aws lambda wait published-version-active \
  --function-name "$CONSOLE_FN" --qualifier "$NEW_VERSION" --region "$REGION"
aws lambda put-runtime-management-config \
  --function-name "$CONSOLE_FN" --qualifier "$NEW_VERSION" \
  --update-runtime-on FunctionUpdate --region "$REGION" >/dev/null
[[ "$(
  aws lambda get-runtime-management-config \
    --function-name "$CONSOLE_FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --query UpdateRuntimeOn --output text
)" == "FunctionUpdate" ]] || {
  echo "console candidate runtime mode is not frozen" >&2
  exit 1
}
PUBLISHED_CONFIG="$(
  aws lambda get-function-configuration \
    --function-name "$CONSOLE_FN" --qualifier "$NEW_VERSION" \
    --region "$REGION" --output json
)"
same_console_config "$CANDIDATE_CONFIG" "$PUBLISHED_CONFIG" || {
  echo "published console version differs from the staged candidate" >&2
  exit 1
}
console_candidate_health "$NEW_VERSION"

aws lambda put-function-concurrency \
  --function-name "$CONSOLE_FN" \
  --region "$REGION" \
  --reserved-concurrent-executions 1 >/dev/null

# Verify the rider tuple again immediately before any console alias movement.
verify_rider_evidence
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$SOURCE_REVISION" \
  && -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
  echo "source changed before console promotion" >&2
  exit 1
}

if [[ "$HAS_LIVE_ALIAS" != "true" ]]; then
  LIVE_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$CONSOLE_FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$NEW_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$RELEASE_DESCRIPTION" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$LIVE_ALIAS_JSON" "$LIVE_ALIAS"
  ROLLBACK_ALIAS_JSON="$(
    aws lambda create-alias \
      --function-name "$CONSOLE_FN" \
      --name "$ROLLBACK_ALIAS" \
      --function-version "$NEW_VERSION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "no prior console release retained yet" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$ROLLBACK_ALIAS_JSON" "$ROLLBACK_ALIAS"
  HAS_LIVE_ALIAS=true
  BASELINE_LIVE_VERSION="$NEW_VERSION"
  BASELINE_LIVE_REVISION="$(jq -r '.RevisionId // ""' <<<"$LIVE_ALIAS_JSON")"
  BASELINE_LIVE_DESCRIPTION="$(jq -r '.Description // ""' <<<"$LIVE_ALIAS_JSON")"
  ensure_api_targets_live
elif [[ "$NEW_VERSION" == "$BASELINE_LIVE_VERSION" ]]; then
  echo "console candidate is already live at version $NEW_VERSION"
  public_console_smoke
else
  CURRENT_LIVE_JSON="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$CURRENT_LIVE_JSON" "$LIVE_ALIAS"
  [[ "$(jq -r '.FunctionVersion // ""' <<<"$CURRENT_LIVE_JSON")" == "$BASELINE_LIVE_VERSION" \
    && "$(jq -r '.RevisionId // ""' <<<"$CURRENT_LIVE_JSON")" == "$BASELINE_LIVE_REVISION" ]] || {
    echo "console live alias changed during deployment; candidate was not promoted" >&2
    exit 1
  }

  if PREVIOUS_ROLLBACK_JSON="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$ROLLBACK_ALIAS" \
      --region "$REGION" --output json 2>&1
  )"; then
    assert_unweighted_alias "$PREVIOUS_ROLLBACK_JSON" "$ROLLBACK_ALIAS"
    PREVIOUS_ROLLBACK_VERSION="$(jq -r '.FunctionVersion // ""' <<<"$PREVIOUS_ROLLBACK_JSON")"
    PREVIOUS_ROLLBACK_REVISION="$(jq -r '.RevisionId // ""' <<<"$PREVIOUS_ROLLBACK_JSON")"
    PREVIOUS_ROLLBACK_DESCRIPTION="$(jq -r '.Description // ""' <<<"$PREVIOUS_ROLLBACK_JSON")"
    if [[ "$PREVIOUS_ROLLBACK_VERSION" != "$BASELINE_LIVE_VERSION" \
      || "$PREVIOUS_ROLLBACK_DESCRIPTION" != "$BASELINE_LIVE_DESCRIPTION" ]]; then
      # Arm before the AWS mutation. If AWS applies the compare-and-swap but
      # the CLI loses its response, cleanup can identify the intended semantic
      # target without requiring a RevisionId that was never observed.
      ROLLBACK_POINTER_GUARD_ACTIVE=true
      ROLLBACK_POINTER_GUARD_RESTORE_VERSION="$PREVIOUS_ROLLBACK_VERSION"
      ROLLBACK_POINTER_GUARD_RESTORE_DESCRIPTION="$PREVIOUS_ROLLBACK_DESCRIPTION"
      ROLLBACK_POINTER_GUARD_EXPECTED_VERSION="$BASELINE_LIVE_VERSION"
      ROLLBACK_POINTER_GUARD_EXPECTED_REVISION=""
      ROLLBACK_POINTER_GUARD_EXPECTED_DESCRIPTION="$BASELINE_LIVE_DESCRIPTION"
    fi
    UPDATED_ROLLBACK_JSON="$(
      aws lambda update-alias \
        --function-name "$CONSOLE_FN" \
        --name "$ROLLBACK_ALIAS" \
        --function-version "$BASELINE_LIVE_VERSION" \
        --revision-id "$PREVIOUS_ROLLBACK_REVISION" \
        --routing-config "$EMPTY_ALIAS_ROUTING" \
        --description "$BASELINE_LIVE_DESCRIPTION" \
        --region "$REGION" --output json
    )"
    UPDATED_ROLLBACK_REVISION="$(jq -r '.RevisionId // ""' <<<"$UPDATED_ROLLBACK_JSON")"
    if [[ "$ROLLBACK_POINTER_GUARD_ACTIVE" == "true" ]]; then
      ROLLBACK_POINTER_GUARD_EXPECTED_REVISION="$UPDATED_ROLLBACK_REVISION"
    fi
  elif [[ "$PREVIOUS_ROLLBACK_JSON" == *"ResourceNotFoundException"* ]]; then
    UPDATED_ROLLBACK_JSON="$(
      aws lambda create-alias \
        --function-name "$CONSOLE_FN" \
        --name "$ROLLBACK_ALIAS" \
        --function-version "$BASELINE_LIVE_VERSION" \
        --routing-config "$EMPTY_ALIAS_ROUTING" \
        --description "$BASELINE_LIVE_DESCRIPTION" \
        --region "$REGION" --output json
    )"
  else
    echo "could not inspect console rollback alias" >&2
    echo "$PREVIOUS_ROLLBACK_JSON" >&2
    exit 1
  fi
  assert_unweighted_alias "$UPDATED_ROLLBACK_JSON" "$ROLLBACK_ALIAS"

  PROMOTION_DESCRIPTION="$RELEASE_DESCRIPTION previous=$BASELINE_LIVE_VERSION"
  PROMOTION_GUARD_ACTIVE=true
  PROMOTION_GUARD_EXPECTED_VERSION="$NEW_VERSION"
  PROMOTION_GUARD_EXPECTED_DESCRIPTION="$PROMOTION_DESCRIPTION"
  PROMOTION_GUARD_RESTORE_VERSION="$BASELINE_LIVE_VERSION"
  PROMOTION_GUARD_RESTORE_DESCRIPTION="$BASELINE_LIVE_DESCRIPTION"
  PROMOTED_LIVE_JSON="$(
    aws lambda update-alias \
      --function-name "$CONSOLE_FN" \
      --name "$LIVE_ALIAS" \
      --function-version "$NEW_VERSION" \
      --revision-id "$BASELINE_LIVE_REVISION" \
      --routing-config "$EMPTY_ALIAS_ROUTING" \
      --description "$PROMOTION_DESCRIPTION" \
      --region "$REGION" --output json
  )"
  PROMOTION_GUARD_EXPECTED_REVISION="$(
    jq -r '.RevisionId // ""' <<<"$PROMOTED_LIVE_JSON"
  )"
  assert_unweighted_alias "$PROMOTED_LIVE_JSON" "$LIVE_ALIAS"
  [[ -n "$PROMOTION_GUARD_EXPECTED_REVISION" ]] || {
    echo "promoted console alias returned no revision id" >&2
    exit 1
  }
  if ! public_console_smoke; then
    echo "promoted console failed public smoke; restoring prior live" >&2
    exit 1
  fi
  VERIFIED_LIVE_JSON="$(
    aws lambda get-alias \
      --function-name "$CONSOLE_FN" --name "$LIVE_ALIAS" \
      --region "$REGION" --output json
  )"
  assert_unweighted_alias "$VERIFIED_LIVE_JSON" "$LIVE_ALIAS"
  jq -e \
    --arg version "$NEW_VERSION" \
    --arg revision "$PROMOTION_GUARD_EXPECTED_REVISION" \
    --arg description "$PROMOTION_DESCRIPTION" '
      .FunctionVersion == $version
      and (.RevisionId // "") == $revision
      and (.Description // "") == $description
    ' <<<"$VERIFIED_LIVE_JSON" >/dev/null || {
    echo "console live alias changed before verification completed" >&2
    exit 1
  }
  refresh_integration
  integration_targets_live_alias || {
    echo "console API stopped targeting the qualified live alias" >&2
    exit 1
  }
  PROMOTION_GUARD_ACTIVE=false
  ROLLBACK_POINTER_GUARD_ACTIVE=false
fi

aws apigatewayv2 update-stage \
  --region "$REGION" \
  --api-id "$API_ID" \
  --stage-name "\$default" \
  --default-route-settings '{"ThrottlingRateLimit":2,"ThrottlingBurstLimit":5}' \
  >/dev/null
aws logs put-retention-policy \
  --log-group-name "$LOG_GROUP" --retention-in-days 14 --region "$REGION"

FINAL_ROLLBACK_VERSION="$(
  aws lambda get-alias \
    --function-name "$CONSOLE_FN" --name "$ROLLBACK_ALIAS" \
    --region "$REGION" --query FunctionVersion --output text
)"
API_URL="$(
  aws apigatewayv2 get-api \
    --api-id "$API_ID" --region "$REGION" \
    --query ApiEndpoint --output text
)"
echo "console deployed: $API_URL/console"
echo "console live Lambda version: $NEW_VERSION"
echo "console retained rollback version: $FINAL_ROLLBACK_VERSION"
echo "console source revision: $SOURCE_REVISION"
echo "console artifact code sha256 (base64): $CANDIDATE_CODE_SHA"
echo "rider promotion evidence sha256: $PROMOTION_SHA256"
echo "remember to add the agency-managed API authorizer before broad operator use"
