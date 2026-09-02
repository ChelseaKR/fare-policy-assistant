#!/usr/bin/env bash
# Directly exercise one immutable Lambda version before an alias points at it.
#
# This sends the same payload-v2 event shape used by the public HTTP API, but
# invokes the numbered version through Lambda so a candidate can be verified
# without exposing it to riders.
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
FN="${FPA_FUNCTION_NAME:-fare-policy-assistant-demo}"
QUALIFIER=""
EXPECTED_CORPUS=""
REQUIRE_RELEASE_IDENTITY=false
ALLOW_LEGACY_RELEASE_IDENTITY=false
EXPECTED_SOURCE=""
EXPECTED_CONFIG=""
EXPECTED_CONTENT=""
EXPECTED_SNAPSHOT=""
EXPECTED_RELEASE=""
EXPECTED_ARTIFACT=""
# Which documents the checked version must have contained. Empty by default
# since issue #164 lifted the standing `yolobus-fares` containment: a default
# that names a document the corpus can now answer correctly would fail this
# check against a correctly un-contained function. Every caller that has an
# opinion states it -- infra/deploy.sh passes the value it deployed, and
# infra/rollback.sh passes the value it derived from the target's own corpus --
# so nothing that used to be verified stops being verified.
if [[ ${FPA_EXPECTED_DISABLED_DOC_IDS+x} ]]; then
  EXPECTED_DISABLED_DOC_IDS="$FPA_EXPECTED_DISABLED_DOC_IDS"
else
  EXPECTED_DISABLED_DOC_IDS=""
fi
DEADLINE_EPOCH=""
REQUIRE_STRUCTURED_TELEMETRY=false
TELEMETRY_OUTPUT=""

usage() {
  cat <<'EOF'
Usage: infra/check-lambda-version.sh --qualifier VERSION [options]

Options:
  --function-name NAME          Lambda function (default: fare-policy-assistant-demo)
  --qualifier VERSION           Required numeric published version
  --expected-corpus VERSION     Required 12-character corpus identity
  --expected-disabled-docs IDS  Required comma-separated disabled document ids
                                (default: none; callers pass what they deployed)
  --require-release-identity    Require the complete identity-bearing release
                                contract (new numeric candidates)
  --allow-legacy-release-identity
                                Explicitly allow a pre-identity retained target
  --expected-source VERSION     Required 40-character source revision
  --expected-config VERSION     Required full-width configuration identity
  --expected-content VERSION    Required full-width content identity
  --expected-snapshot VERSION   Required full-width source-snapshot identity
  --expected-release VERSION    Required full-width release identity
  --expected-artifact SHA256    Required AWS-style base64 ZIP CodeSha256
  --deadline-epoch EPOCH        Stop network checks by this Unix timestamp
  --require-structured-telemetry
                                Require privacy-safe correlated JSON events from
                                the paid answer-model check
  --telemetry-output PATH       Write the validated event pair to PATH
  --region REGION               AWS region (default: AWS_REGION or us-west-2)
  -h, --help                    Show this help
EOF
}

fail() {
  echo "version health: FAIL: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --function-name)
      (($# >= 2)) || fail "--function-name requires a value"
      FN="$2"
      shift 2
      ;;
    --qualifier)
      (($# >= 2)) || fail "--qualifier requires a value"
      QUALIFIER="$2"
      shift 2
      ;;
    --expected-corpus)
      (($# >= 2)) || fail "--expected-corpus requires a value"
      EXPECTED_CORPUS="$2"
      shift 2
      ;;
    --expected-disabled-docs)
      (($# >= 2)) || fail "--expected-disabled-docs requires a value"
      EXPECTED_DISABLED_DOC_IDS="$2"
      shift 2
      ;;
    --require-release-identity)
      REQUIRE_RELEASE_IDENTITY=true
      shift
      ;;
    --allow-legacy-release-identity)
      ALLOW_LEGACY_RELEASE_IDENTITY=true
      shift
      ;;
    --expected-source)
      (($# >= 2)) || fail "--expected-source requires a value"
      EXPECTED_SOURCE="$2"
      shift 2
      ;;
    --expected-config)
      (($# >= 2)) || fail "--expected-config requires a value"
      EXPECTED_CONFIG="$2"
      shift 2
      ;;
    --expected-content)
      (($# >= 2)) || fail "--expected-content requires a value"
      EXPECTED_CONTENT="$2"
      shift 2
      ;;
    --expected-snapshot)
      (($# >= 2)) || fail "--expected-snapshot requires a value"
      EXPECTED_SNAPSHOT="$2"
      shift 2
      ;;
    --expected-release)
      (($# >= 2)) || fail "--expected-release requires a value"
      EXPECTED_RELEASE="$2"
      shift 2
      ;;
    --expected-artifact)
      (($# >= 2)) || fail "--expected-artifact requires a value"
      EXPECTED_ARTIFACT="$2"
      shift 2
      ;;
    --deadline-epoch)
      (($# >= 2)) || fail "--deadline-epoch requires a value"
      DEADLINE_EPOCH="$2"
      shift 2
      ;;
    --require-structured-telemetry)
      REQUIRE_STRUCTURED_TELEMETRY=true
      shift
      ;;
    --telemetry-output)
      (($# >= 2)) || fail "--telemetry-output requires a value"
      TELEMETRY_OUTPUT="$2"
      shift 2
      ;;
    --region)
      (($# >= 2)) || fail "--region requires a value"
      REGION="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

command -v aws >/dev/null 2>&1 || fail "aws is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"
if [[ "$REQUIRE_STRUCTURED_TELEMETRY" == "true" ]]; then
  command -v openssl >/dev/null 2>&1 || fail "openssl is required for telemetry validation"
fi
[[ "$QUALIFIER" =~ ^[1-9][0-9]*$ ]] || fail "--qualifier must be a numeric published version"
if [[ -n "$EXPECTED_CORPUS" && ! "$EXPECTED_CORPUS" =~ ^[0-9a-f]{12}$ ]]; then
  fail "--expected-corpus must be a 12-character lowercase hex digest"
fi
if [[ -n "$EXPECTED_DISABLED_DOC_IDS" \
  && ! "$EXPECTED_DISABLED_DOC_IDS" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]]; then
  fail "--expected-disabled-docs must be comma-separated document ids"
fi
if [[ -n "$DEADLINE_EPOCH" && ! "$DEADLINE_EPOCH" =~ ^[1-9][0-9]*$ ]]; then
  fail "--deadline-epoch must be a positive Unix timestamp"
fi
if [[ -n "$TELEMETRY_OUTPUT" && "$REQUIRE_STRUCTURED_TELEMETRY" != "true" ]]; then
  fail "--telemetry-output requires --require-structured-telemetry"
fi
if [[ "$REQUIRE_RELEASE_IDENTITY" == "$ALLOW_LEGACY_RELEASE_IDENTITY" ]]; then
  fail "choose exactly one of --require-release-identity or --allow-legacy-release-identity"
fi
if [[ "$REQUIRE_RELEASE_IDENTITY" == "true" ]]; then
  [[ -n "$EXPECTED_SOURCE" \
    && -n "$EXPECTED_CONFIG" \
    && -n "$EXPECTED_CONTENT" \
    && -n "$EXPECTED_SNAPSHOT" \
    && -n "$EXPECTED_RELEASE" \
    && -n "$EXPECTED_ARTIFACT" ]] \
    || fail "--require-release-identity requires every --expected-* identity argument"
  [[ "$EXPECTED_SOURCE" =~ ^[0-9a-f]{40}$ ]] \
    || fail "--expected-source must be a 40-character lowercase source revision"
  for expected_identity in \
    "$EXPECTED_CONFIG" \
    "$EXPECTED_CONTENT" \
    "$EXPECTED_SNAPSHOT" \
    "$EXPECTED_RELEASE"; do
    [[ "$expected_identity" =~ ^[0-9a-f]{64}$ ]] \
      || fail "configuration, content, snapshot, and release identities must be 64-character lowercase SHA-256 values"
  done
  [[ "$EXPECTED_ARTIFACT" =~ ^[A-Za-z0-9+/]{43}=$ ]] \
    || fail "--expected-artifact must be an AWS-style base64 SHA-256 digest"
elif [[ -n "$EXPECTED_SOURCE" \
  || -n "$EXPECTED_CONFIG" \
  || -n "$EXPECTED_CONTENT" \
  || -n "$EXPECTED_SNAPSHOT" \
  || -n "$EXPECTED_RELEASE" \
  || -n "$EXPECTED_ARTIFACT" ]]; then
  fail "--allow-legacy-release-identity does not accept --expected-* identity arguments"
fi

HEALTH_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-version-health.XXXXXX")"
chmod 700 "$HEALTH_TMPDIR"
trap 'rm -rf "$HEALTH_TMPDIR"' EXIT

LAST_PAYLOAD=""
LAST_LOG_TAIL=""
REQUEST_NUMBER=0

run_before_deadline() {
  local remaining
  local marker
  local command_pid
  local timer_pid
  local status

  if [[ -z "$DEADLINE_EPOCH" ]]; then
    "$@"
    return
  fi

  remaining=$((DEADLINE_EPOCH - $(date +%s)))
  ((remaining > 0)) || return 124
  marker="$(mktemp "$HEALTH_TMPDIR/deadline.XXXXXX")"

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
  if [[ "$(<"$marker")" == "expired" ]] || (( $(date +%s) >= DEADLINE_EPOCH )); then
    return 124
  fi
  return "$status"
}

requires_disabled_document() {
  local document_id="$1"
  [[ ",$EXPECTED_DISABLED_DOC_IDS," == *",$document_id,"* ]]
}

invoke_event() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local label="$4"
  local capture_logs="${5:-false}"
  local direct_health="${6:-false}"
  local event_path
  local metadata_path
  local payload_path
  local log_path

  REQUEST_NUMBER=$((REQUEST_NUMBER + 1))
  event_path="$HEALTH_TMPDIR/event-$REQUEST_NUMBER.json"
  metadata_path="$HEALTH_TMPDIR/metadata-$REQUEST_NUMBER.json"
  payload_path="$HEALTH_TMPDIR/payload-$REQUEST_NUMBER.json"
  log_path="$HEALTH_TMPDIR/log-$REQUEST_NUMBER.txt"
  LAST_PAYLOAD="$payload_path"
  LAST_LOG_TAIL=""

  jq -nc \
    --arg method "$method" \
    --arg path "$path" \
    --arg body "$body" \
    --arg direct_health "$direct_health" \
    '{
      version: "2.0",
      routeKey: "$default",
      rawPath: $path,
      rawQueryString: "",
      headers: {},
      requestContext: {
        http: {
          method: $method,
          path: $path,
          protocol: "HTTP/1.1",
          sourceIp: "127.0.0.1",
          userAgent: "fare-policy-assistant-version-health/1"
        },
        requestId: "pre-alias-health",
        routeKey: "$default",
        stage: "$default",
        timeEpoch: 0
      },
      isBase64Encoded: false
    }
    + if $body == "" then {} else {body: $body} end
    + if $direct_health == "true"
      then {fare_assistant_health: "release-v1"}
      else {}
      end' >"$event_path"

  local -a aws_timeout_options=()
  local -a log_options=()
  local remaining
  local connect_timeout
  if [[ "$capture_logs" == "true" ]]; then
    log_options=(--log-type Tail)
  fi
  if [[ -n "$DEADLINE_EPOCH" ]]; then
    remaining=$((DEADLINE_EPOCH - $(date +%s)))
    ((remaining > 0)) || fail "$label exceeded the release-operation deadline"
    connect_timeout="$remaining"
    ((connect_timeout > 10)) && connect_timeout=10
    aws_timeout_options=(
      --cli-connect-timeout "$connect_timeout"
      --cli-read-timeout "$remaining"
    )
  fi

  if ! run_before_deadline aws lambda invoke \
    --function-name "$FN" \
    --qualifier "$QUALIFIER" \
    --invocation-type RequestResponse \
    --cli-binary-format raw-in-base64-out \
    "${log_options[@]}" \
    --payload "fileb://$event_path" \
    --region "$REGION" \
    --output json \
    "${aws_timeout_options[@]}" \
    "$payload_path" >"$metadata_path"; then
    fail "$label could not invoke $FN:$QUALIFIER before the operation deadline"
  fi
  chmod 600 "$event_path" "$metadata_path" "$payload_path"

  jq -e --arg version "$QUALIFIER" '
    .StatusCode == 200
    and (.FunctionError? == null)
    and .ExecutedVersion == $version
  ' "$metadata_path" >/dev/null \
    || fail "$label did not execute cleanly on exact version $QUALIFIER"
  jq -e '.statusCode == 200 and (.headers | type == "object")' "$payload_path" >/dev/null \
    || fail "$label handler response was not HTTP 200"

  if [[ "$capture_logs" == "true" ]]; then
    if ! jq -er '.LogResult | select(type == "string" and length > 0)' \
      "$metadata_path" | openssl base64 -d -A >"$log_path"; then
      fail "$label did not return a decodable Lambda log tail"
    fi
    chmod 600 "$log_path"
    LAST_LOG_TAIL="$log_path"
  fi
}

validate_structured_telemetry() {
  local log_path="$1"
  local events_path="$HEALTH_TMPDIR/structured-events.json"
  local validated_path="$HEALTH_TMPDIR/validated-telemetry.json"

  [[ -s "$log_path" ]] || fail "safe paid answer returned no structured log tail"
  if ! jq -Rsc '
    split("\n")
    | map(select(length > 0) | fromjson)
  ' "$log_path" >"$events_path"; then
    fail "safe paid answer log tail was not newline-delimited JSON"
  fi
  chmod 600 "$events_path"

  if ! jq -e --arg version "$QUALIFIER" '
    ([.[] | select(.event == "genai_call")]) as $model
    | ([.[] | select(.event == "answer_request")]) as $answer
    | ($model | length) == 1
      and ($answer | length) == 1
      and ($model[0].level == "INFO")
      and ($answer[0].level == "INFO")
      and ($model[0].runtime_request_id | type == "string" and length > 0)
      and ($model[0].requestId == $model[0].runtime_request_id)
      and ($answer[0].requestId == $answer[0].runtime_request_id)
      and ($answer[0].runtime_request_id == $model[0].runtime_request_id)
      and ($model[0].function_version == $version)
      and ($answer[0].function_version == $version)
      and ($model[0].completion_recorded == true)
      and ($model[0].cost_estimate_available == true)
      and ($model[0].input_tokens | type == "number" and floor == . and . >= 0)
      and ($model[0].output_tokens | type == "number" and floor == . and . >= 0)
      and ($model[0].model_duration_ms | type == "number" and . >= 0)
      and ($model[0].estimated_cost_usd | type == "number" and . >= 0)
      and ($model[0]."gen_ai.usage.input_tokens" == $model[0].input_tokens)
      and ($model[0]."gen_ai.usage.output_tokens" == $model[0].output_tokens)
      and ($model[0]."portfolio.gen_ai.cost.usd" == $model[0].estimated_cost_usd)
      and ($model[0]."gen_ai.operation.name" == "chat")
      and ($model[0]."gen_ai.system" | type == "string" and length > 0)
      and ($model[0]."gen_ai.request.model" | type == "string" and length > 0)
      and ($model[0]."gen_ai.response.model" | type == "string" and length > 0)
      and ($model[0]."gen_ai.client.operation.duration"
        | type == "number" and . >= 0)
      and (((1000 * $model[0]."gen_ai.client.operation.duration")
        - $model[0].model_duration_ms) | fabs <= 1)
      and ($answer[0].direct_health == true)
      and ($answer[0].cache == "bypass")
      and ($answer[0].model_called == true)
      and ($answer[0].completion_recorded == true)
      and ($answer[0].duration_ms | type == "number" and . >= 0)
      and ($answer[0].input_tokens == $model[0].input_tokens)
      and ($answer[0].output_tokens == $model[0].output_tokens)
  ' "$events_path" >/dev/null; then
    fail "safe paid answer did not emit one valid, correlated model/answer event pair"
  fi

  if ! jq -e '
    [
      .[]
      | select(.event == "genai_call" or .event == "answer_request")
      | paths(scalars) as $path
      | ($path[-1] | tostring)
      | select(. == "question"
          or . == "answer"
          or . == "prompt"
          or . == "system_prompt"
          or . == "messages"
          or . == "history"
          or . == "citations"
          or . == "content"
          or . == "headers"
          or . == "sourceIp"
          or . == "userAgent"
          or . == "exception"
          or . == "stack_trace"
          or . == "gen_ai.system_instructions"
          or . == "gen_ai.input.messages"
          or . == "gen_ai.output.messages")
    ] | length == 0
  ' "$events_path" >/dev/null; then
    fail "safe paid answer telemetry contained a prohibited content or request field"
  fi
  if grep -Fq "What proof do I need for the veteran fare on MST?" "$log_path" \
    || grep -Fq "$PII_SENTINEL" "$log_path"; then
    fail "safe paid answer telemetry contained rider content"
  fi

  jq '
    {
      genai_call: first(.[] | select(.event == "genai_call")),
      answer_request: first(.[] | select(.event == "answer_request"))
    }
  ' "$events_path" >"$validated_path"
  chmod 600 "$validated_path"
  if [[ -n "$TELEMETRY_OUTPUT" ]]; then
    cp "$validated_path" "$TELEMETRY_OUTPUT"
    chmod 600 "$TELEMETRY_OUTPUT"
  fi
}

if ! QUALIFIED_CONFIG="$(
  run_before_deadline aws lambda get-function-configuration \
    --function-name "$FN" \
    --qualifier "$QUALIFIER" \
    --region "$REGION" \
    --output json
)"; then
  fail "could not read the exact qualified configuration for $FN:$QUALIFIER"
fi

if [[ "$REQUIRE_RELEASE_IDENTITY" == "true" ]]; then
  jq -e \
    --arg version "$QUALIFIER" \
    --arg source "$EXPECTED_SOURCE" \
    --arg config "$EXPECTED_CONFIG" \
    --arg content "$EXPECTED_CONTENT" \
    --arg snapshot "$EXPECTED_SNAPSHOT" \
    --arg release "$EXPECTED_RELEASE" \
    --arg artifact "$EXPECTED_ARTIFACT" '
      (.Environment.Variables // {}) as $env
      | .Version == $version
        and .CodeSha256 == $artifact
        and $env.FPA_SOURCE_REVISION == $source
        and $env.FPA_CONFIG_VERSION == $config
        and $env.FPA_PINNED_CONTENT_VERSION == $content
        and $env.FPA_PINNED_SNAPSHOT_VERSION == $snapshot
        and $env.FPA_RELEASE_VERSION == $release
        and $env.FPA_ARTIFACT_CODE_SHA256 == $artifact
    ' <<<"$QUALIFIED_CONFIG" >/dev/null \
    || fail "qualified Lambda configuration did not match the complete release identity"
  echo "version health: ok: qualified release identity"
else
  jq -e \
    --arg version "$QUALIFIER" '
      (.Environment.Variables // {}) as $env
      | .Version == $version
        and ($env | has("FPA_SOURCE_REVISION") | not)
        and ($env | has("FPA_CONFIG_VERSION") | not)
        and ($env | has("FPA_PINNED_SNAPSHOT_VERSION") | not)
        and ($env | has("FPA_RELEASE_VERSION") | not)
        and ($env | has("FPA_ARTIFACT_CODE_SHA256") | not)
    ' <<<"$QUALIFIED_CONFIG" >/dev/null \
    || fail "legacy mode requires a qualified target with no identity-bearing release tuple"
  echo "version health: ok: explicit legacy release identity"
fi

invoke_event GET / "" "root"
jq -e '
  (.body | type == "string" and contains("Transit Fare Policy Assistant"))
  and .headers["cache-control"] == "no-store"
' "$LAST_PAYLOAD" >/dev/null || fail "root response failed content/security checks"
echo "version health: ok: root"

invoke_event GET /version "" "version"
if [[ "$REQUIRE_RELEASE_IDENTITY" == "true" ]]; then
  jq -e \
    --arg version "$QUALIFIER" \
    --arg corpus "$EXPECTED_CORPUS" \
    --arg disabled "$EXPECTED_DISABLED_DOC_IDS" \
    --arg source "$EXPECTED_SOURCE" \
    --arg config "$EXPECTED_CONFIG" \
    --arg content "$EXPECTED_CONTENT" \
    --arg snapshot "$EXPECTED_SNAPSHOT" \
    --arg release "$EXPECTED_RELEASE" \
    --arg artifact "$EXPECTED_ARTIFACT" '
      (.body | fromjson) as $body
      | ($body.matches_pin == true)
        and ($body.corpus_version | type == "string" and length > 0)
        and ($corpus == "" or $body.corpus_version == $corpus)
        and ($body.disabled_documents | type == "array")
        and all(
          ($disabled | split(",") | map(select(length > 0)))[];
          . as $doc_id | ($body.disabled_documents | index($doc_id)) != null
        )
        and $body.identity_status == "verified"
        and $body.function_version == $version
        and $body.source_revision == $source
        and $body.config_version == $config
        and $body.content_version == $content
        and $body.snapshot_version == $snapshot
        and $body.release_version == $release
        and $body.artifact_code_sha256 == $artifact
    ' "$LAST_PAYLOAD" >/dev/null \
    || fail "/version did not match the verified numeric release identity"
else
  jq -e \
    --arg corpus "$EXPECTED_CORPUS" \
    --arg disabled "$EXPECTED_DISABLED_DOC_IDS" '
      (.body | fromjson) as $body
      | ($body.matches_pin == true)
        and ($body.corpus_version | type == "string" and length > 0)
        and ($corpus == "" or $body.corpus_version == $corpus)
        and ($body.disabled_documents | type == "array")
        and all(
          ($disabled | split(",") | map(select(length > 0)))[];
          . as $doc_id | ($body.disabled_documents | index($doc_id)) != null
        )
        and ($body | has("source_revision") | not)
        and ($body | has("config_version") | not)
        and ($body | has("snapshot_version") | not)
        and ($body | has("release_version") | not)
        and ($body | has("artifact_code_sha256") | not)
        and ($body.identity_status // "legacy") != "verified"
    ' "$LAST_PAYLOAD" >/dev/null \
    || fail "/version did not match the explicit legacy release identity"
fi
echo "version health: ok: version"

PII_SENTINEL="987-65-4321"
PII_BODY="$(
  jq -nc --arg question \
    "My Social Security number is $PII_SENTINEL. Do I qualify for a discount?" \
    '{question: $question}'
)"
invoke_event POST /api/ask "$PII_BODY" "PII refusal"
jq -e '
  (.body | fromjson) as $body
  | $body.kind == "refused_input"
    and ($body.citations | type == "array" and length == 0)
' "$LAST_PAYLOAD" >/dev/null || fail "PII request did not fail closed"
if grep -Fq "$PII_SENTINEL" "$LAST_PAYLOAD"; then
  fail "PII refusal reflected the synthetic identifier"
fi
echo "version health: ok: PII refusal"

if requires_disabled_document "yolobus-fares"; then
  YOLOBUS_BODY="$(
    jq -nc --arg question "How much is the local fare on Yolobus?" '{question: $question}'
  )"
  invoke_event POST /api/ask "$YOLOBUS_BODY" "Yolobus containment"
  jq -e '
    (.body | fromjson) as $body
    | $body.kind == "refused_no_support"
      and ($body.citations | type == "array" and length == 0)
  ' "$LAST_PAYLOAD" >/dev/null || fail "disabled Yolobus source did not fail closed"
  echo "version health: ok: Yolobus containment"
fi

SAFE_BODY="$(
  jq -nc --arg question "What proof do I need for the veteran fare on MST?" \
    '{question: $question}'
)"
if [[ "$REQUIRE_STRUCTURED_TELEMETRY" == "true" ]]; then
  invoke_event POST /api/ask "$SAFE_BODY" "safe paid answer" true true
else
  invoke_event POST /api/ask "$SAFE_BODY" "safe paid answer"
fi
jq -e '
  (.body | fromjson) as $body
  | $body.kind == "answered"
    and ($body.answer | type == "string" and length > 0)
    and ($body.citations | type == "array" and length > 0)
    and all($body.citations[];
      (.agency | type == "string" and length > 0)
      and (.url | type == "string" and startswith("https://"))
      and (.fetch_date | type == "string"
        and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
    )
' "$LAST_PAYLOAD" >/dev/null || fail "known-good MST question was not answered with dated citations"
echo "version health: ok: safe paid answer"
if [[ "$REQUIRE_STRUCTURED_TELEMETRY" == "true" ]]; then
  validate_structured_telemetry "$LAST_LOG_TAIL"
  echo "version health: ok: privacy-safe structured telemetry"
fi

echo "version health: PASS: $FN:$QUALIFIER"
