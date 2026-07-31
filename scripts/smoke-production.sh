#!/usr/bin/env bash
# Deterministic, read-only smoke test for the two public production surfaces.
#
# The evidence hub and rider assistant intentionally have different origins:
#   evidence: static reports and evaluation history
#   assistant: Lambda/API Gateway rider flows
#
# Defaults point at production. Override them explicitly for a staging deploy:
#   ./scripts/smoke-production.sh \
#     --assistant-base-url https://example.execute-api.us-west-2.amazonaws.com \
#     --evidence-base-url https://staging-evals.example.org \
#     --require-release-identity \
#     --expected-source ... --expected-config ... --expected-content ... \
#     --expected-snapshot ... --expected-release ... --expected-artifact ... \
#     --expected-function-version ...
#
# Every invocation must deliberately select strict identity verification or
# the narrowly scoped legacy mode; there is no implicit production fallback.
set -euo pipefail

DEFAULT_ASSISTANT_BASE_URL="https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com"
DEFAULT_EVIDENCE_BASE_URL="https://evals.chelseakr.com"

ASSISTANT_BASE_URL="${FPA_SMOKE_ASSISTANT_BASE_URL:-$DEFAULT_ASSISTANT_BASE_URL}"
EVIDENCE_BASE_URL="${FPA_SMOKE_EVIDENCE_BASE_URL:-$DEFAULT_EVIDENCE_BASE_URL}"
CONNECT_TIMEOUT="${FPA_SMOKE_CONNECT_TIMEOUT:-10}"
MAX_TIME="${FPA_SMOKE_MAX_TIME:-45}"
if [[ ${FPA_SMOKE_EXPECTED_DISABLED_DOC_IDS+x} ]]; then
  EXPECTED_DISABLED_DOC_IDS="$FPA_SMOKE_EXPECTED_DISABLED_DOC_IDS"
else
  EXPECTED_DISABLED_DOC_IDS="yolobus-fares"
fi
DEADLINE_EPOCH=""
CHECK_EVIDENCE=true
REQUIRE_RELEASE_IDENTITY=false
ALLOW_LEGACY_RELEASE_IDENTITY=false
EXPECTED_SOURCE=""
EXPECTED_CONFIG=""
EXPECTED_CONTENT=""
EXPECTED_SNAPSHOT=""
EXPECTED_RELEASE=""
EXPECTED_ARTIFACT=""
EXPECTED_FUNCTION_VERSION=""

usage() {
  cat <<'EOF'
Usage: scripts/smoke-production.sh [options]

Options:
  --assistant-base-url URL  Rider assistant origin (API Gateway)
  --evidence-base-url URL   Public evidence/report origin
  --connect-timeout SEC     Per-request connection timeout (default: 10)
  --max-time SEC            Per-request total timeout (default: 45)
  --expected-disabled-docs IDS
                            Required comma-separated disabled document ids
                            (default: yolobus-fares; "" means none)
  --require-release-identity
                            Require the complete identity-bearing release
  --allow-legacy-release-identity
                            Explicitly allow a pre-identity retained release
  --expected-source VERSION Required 40-character source revision
  --expected-config VERSION Required full-width configuration identity
  --expected-content VERSION
                            Required full-width content identity
  --expected-snapshot VERSION
                            Required full-width source-snapshot identity
  --expected-release VERSION
                            Required full-width release identity
  --expected-artifact SHA256
                            Required AWS-style base64 ZIP CodeSha256
  --expected-function-version VERSION
                            Required numeric Lambda function version
  --deadline-epoch EPOCH    Stop network checks by this Unix timestamp
  --assistant-only          Skip the independent static evidence origin
  -h, --help                Show this help

Environment equivalents:
  FPA_SMOKE_ASSISTANT_BASE_URL
  FPA_SMOKE_EVIDENCE_BASE_URL
  FPA_SMOKE_CONNECT_TIMEOUT
  FPA_SMOKE_MAX_TIME
  FPA_SMOKE_EXPECTED_DISABLED_DOC_IDS
EOF
}

fail() {
  echo "smoke: FAIL: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --assistant-base-url)
      (($# >= 2)) || fail "--assistant-base-url requires a URL"
      ASSISTANT_BASE_URL="$2"
      shift 2
      ;;
    --evidence-base-url)
      (($# >= 2)) || fail "--evidence-base-url requires a URL"
      EVIDENCE_BASE_URL="$2"
      shift 2
      ;;
    --connect-timeout)
      (($# >= 2)) || fail "--connect-timeout requires seconds"
      CONNECT_TIMEOUT="$2"
      shift 2
      ;;
    --max-time)
      (($# >= 2)) || fail "--max-time requires seconds"
      MAX_TIME="$2"
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
    --expected-function-version)
      (($# >= 2)) || fail "--expected-function-version requires a value"
      EXPECTED_FUNCTION_VERSION="$2"
      shift 2
      ;;
    --deadline-epoch)
      (($# >= 2)) || fail "--deadline-epoch requires a value"
      DEADLINE_EPOCH="$2"
      shift 2
      ;;
    --assistant-only)
      CHECK_EVIDENCE=false
      shift
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

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

[[ "$ASSISTANT_BASE_URL" =~ ^https?://[^[:space:]]+$ ]] \
  || fail "assistant base URL must be an absolute http(s) URL"
if [[ "$CHECK_EVIDENCE" == "true" ]]; then
  [[ "$EVIDENCE_BASE_URL" =~ ^https?://[^[:space:]]+$ ]] \
    || fail "evidence base URL must be an absolute http(s) URL"
fi
[[ "$CONNECT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
  || fail "connect timeout must be a positive integer"
[[ "$MAX_TIME" =~ ^[1-9][0-9]*$ ]] \
  || fail "max time must be a positive integer"
if [[ -n "$EXPECTED_DISABLED_DOC_IDS" \
  && ! "$EXPECTED_DISABLED_DOC_IDS" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]]; then
  fail "expected disabled documents must be comma-separated document ids"
fi
if [[ -n "$DEADLINE_EPOCH" && ! "$DEADLINE_EPOCH" =~ ^[1-9][0-9]*$ ]]; then
  fail "deadline epoch must be a positive Unix timestamp"
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
    && -n "$EXPECTED_ARTIFACT" \
    && -n "$EXPECTED_FUNCTION_VERSION" ]] \
    || fail "--require-release-identity requires every expected release identity argument"
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
  [[ "$EXPECTED_FUNCTION_VERSION" =~ ^[1-9][0-9]*$ ]] \
    || fail "--expected-function-version must be a numeric published version"
elif [[ -n "$EXPECTED_SOURCE" \
  || -n "$EXPECTED_CONFIG" \
  || -n "$EXPECTED_CONTENT" \
  || -n "$EXPECTED_SNAPSHOT" \
  || -n "$EXPECTED_RELEASE" \
  || -n "$EXPECTED_ARTIFACT" \
  || -n "$EXPECTED_FUNCTION_VERSION" ]]; then
  fail "--allow-legacy-release-identity does not accept expected release identity arguments"
fi

ASSISTANT_BASE_URL="${ASSISTANT_BASE_URL%/}"
if [[ "$CHECK_EVIDENCE" == "true" ]]; then
  EVIDENCE_BASE_URL="${EVIDENCE_BASE_URL%/}"
fi

SMOKE_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_TMPDIR"' EXIT

LAST_STATUS=""
LAST_HEADERS=""
LAST_BODY=""
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
  marker="$(mktemp "$SMOKE_TMPDIR/deadline.XXXXXX")"

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

request() {
  local method="$1"
  local url="$2"
  local payload="${3:-}"
  local curl_status
  local status_path
  local request_max_time="$MAX_TIME"
  local request_connect_timeout="$CONNECT_TIMEOUT"
  local remaining
  local -a curl_options

  REQUEST_NUMBER=$((REQUEST_NUMBER + 1))
  LAST_HEADERS="$SMOKE_TMPDIR/headers-$REQUEST_NUMBER"
  LAST_BODY="$SMOKE_TMPDIR/body-$REQUEST_NUMBER"
  status_path="$SMOKE_TMPDIR/status-$REQUEST_NUMBER"

  if [[ -n "$DEADLINE_EPOCH" ]]; then
    remaining=$((DEADLINE_EPOCH - $(date +%s)))
    ((remaining > 0)) || fail "$method $url exceeded the operation deadline"
    ((request_max_time > remaining)) && request_max_time="$remaining"
    ((request_connect_timeout > remaining)) && request_connect_timeout="$remaining"
  fi
  curl_options=(
    --silent
    --show-error
    --location
    --connect-timeout "$request_connect_timeout"
    --max-time "$request_max_time"
    --retry 2
    --retry-delay 1
    --retry-max-time "$request_max_time"
    --retry-all-errors
    --user-agent "fare-policy-assistant-production-smoke/1"
  )

  if [[ "$method" == "POST" ]]; then
    if ! run_before_deadline \
      curl "${curl_options[@]}" \
        --request POST \
        --header "content-type: application/json" \
        --data "$payload" \
        --dump-header "$LAST_HEADERS" \
        --output "$LAST_BODY" \
        --write-out "%{http_code}" \
        "$url" >"$status_path"; then
      fail "$method $url could not be reached before the operation deadline"
    fi
  else
    if ! run_before_deadline \
      curl "${curl_options[@]}" \
        --request GET \
        --dump-header "$LAST_HEADERS" \
        --output "$LAST_BODY" \
        --write-out "%{http_code}" \
        "$url" >"$status_path"; then
      fail "$method $url could not be reached before the operation deadline"
    fi
  fi
  curl_status="$(tr -d '\r\n' <"$status_path")"
  LAST_STATUS="$curl_status"
}

header_value() {
  local wanted="${1,,}"
  awk -v wanted="$wanted" '
    {
      line = $0
      sub(/\r$/, "", line)
      colon = index(line, ":")
      if (colon > 0) {
        name = tolower(substr(line, 1, colon - 1))
        if (name == wanted) {
          value = substr(line, colon + 1)
          sub(/^[[:space:]]+/, "", value)
          found = value
        }
      }
    }
    END { print found }
  ' "$LAST_HEADERS"
}

expect_status_200() {
  local label="$1"
  [[ "$LAST_STATUS" == "200" ]] \
    || fail "$label returned HTTP $LAST_STATUS (body: $(head -c 240 "$LAST_BODY"))"
}

expect_header_contains() {
  local label="$1"
  local name="$2"
  local expected="$3"
  local actual
  actual="$(header_value "$name")"
  [[ "${actual,,}" == *"${expected,,}"* ]] \
    || fail "$label header $name was '$actual'; expected it to contain '$expected'"
}

expect_header_absent() {
  local label="$1"
  local name="$2"
  local actual
  actual="$(header_value "$name")"
  [[ -z "$actual" ]] || fail "$label unexpectedly sent $name: $actual"
}

check_no_store_security_headers() {
  local label="$1"
  local frameable="${2:-false}"
  local csp

  expect_header_contains "$label" "cache-control" "no-store"
  expect_header_contains "$label" "x-content-type-options" "nosniff"
  expect_header_contains "$label" "referrer-policy" "no-referrer"
  expect_header_contains "$label" "content-security-policy" "default-src 'none'"
  csp="$(header_value "content-security-policy")"
  [[ "$csp" != *"'unsafe-inline'"* ]] || fail "$label CSP permits unsafe-inline"

  if [[ "$frameable" == "true" ]]; then
    expect_header_absent "$label" "x-frame-options"
    [[ "$csp" == *"frame-ancestors "* ]] || fail "$label CSP omits frame-ancestors"
  else
    expect_header_contains "$label" "x-frame-options" "DENY"
  fi
}

check_assistant_html() {
  local path="$1"
  local marker="$2"
  local frameable="${3:-false}"
  local label="assistant $path"

  request GET "$ASSISTANT_BASE_URL$path"
  expect_status_200 "$label"
  expect_header_contains "$label" "content-type" "text/html"
  check_no_store_security_headers "$label" "$frameable"
  grep -Fqi "$marker" "$LAST_BODY" || fail "$label body is missing '$marker'"
  echo "smoke: ok: $label"
}

if [[ "$CHECK_EVIDENCE" == "true" ]]; then
  echo "smoke: evidence=$EVIDENCE_BASE_URL"
fi
echo "smoke: assistant=$ASSISTANT_BASE_URL"

# Static evidence entrypoints: these prove the report origin is healthy without
# treating it as the rider assistant.
if [[ "$CHECK_EVIDENCE" == "true" ]]; then
  request GET "$EVIDENCE_BASE_URL/"
  expect_status_200 "evidence /"
  expect_header_contains "evidence /" "content-type" "text/html"
  grep -Fqi "evaluation" "$LAST_BODY" || fail "evidence / is missing its evaluation marker"
  echo "smoke: ok: evidence /"

  request GET "$EVIDENCE_BASE_URL/report.html"
  expect_status_200 "evidence /report.html"
  expect_header_contains "evidence /report.html" "content-type" "text/html"
  grep -Fqi "evaluation" "$LAST_BODY" \
    || fail "evidence /report.html is missing its evaluation marker"
  echo "smoke: ok: evidence /report.html"
fi

# Every public, read-only rider entrypoint.
check_assistant_html "/" "Transit Fare Policy Assistant"
check_assistant_html "/offline" "Offline fare reference"
if requires_disabled_document "yolobus-fares" \
  && grep -Fq "All below fares are effective July 1, 2025" "$LAST_BODY"; then
  fail "assistant /offline exposes the contained Yolobus fare period"
fi
check_assistant_html "/guide" "Which fare applies to me?"
if requires_disabled_document "yolobus-fares" \
  && grep -Fq "All below fares are effective July 1, 2025" "$LAST_BODY"; then
  fail "assistant /guide exposes the contained Yolobus fare period"
fi
check_assistant_html "/embed" "Transit fare policy assistant" true

request GET "$ASSISTANT_BASE_URL/version"
expect_status_200 "assistant /version"
expect_header_contains "assistant /version" "content-type" "application/json"
check_no_store_security_headers "assistant /version"
if [[ "$REQUIRE_RELEASE_IDENTITY" == "true" ]]; then
  jq -e \
    --arg disabled "$EXPECTED_DISABLED_DOC_IDS" \
    --arg source "$EXPECTED_SOURCE" \
    --arg config "$EXPECTED_CONFIG" \
    --arg content "$EXPECTED_CONTENT" \
    --arg snapshot "$EXPECTED_SNAPSHOT" \
    --arg release "$EXPECTED_RELEASE" \
    --arg artifact "$EXPECTED_ARTIFACT" \
    --arg function_version "$EXPECTED_FUNCTION_VERSION" '
      . as $body
      | ($body.corpus_version | type == "string" and length > 0)
        and ($body.as_of | type == "string"
          and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
        and ($body.agencies | type == "array" and length > 0)
        and ($body.matches_pin == true)
        and ($body.disabled_documents | type == "array")
        and all(
          ($disabled | split(",") | map(select(length > 0)))[];
          . as $doc_id | ($body.disabled_documents | index($doc_id)) != null
        )
        and $body.identity_status == "verified"
        and $body.source_revision == $source
        and $body.config_version == $config
        and $body.content_version == $content
        and $body.snapshot_version == $snapshot
        and $body.release_version == $release
        and $body.artifact_code_sha256 == $artifact
        and $body.function_version == $function_version
    ' "$LAST_BODY" >/dev/null \
    || fail "assistant /version returned an invalid verified release identity"
else
  jq -e --arg disabled "$EXPECTED_DISABLED_DOC_IDS" '
    . as $body
    | ($body.corpus_version | type == "string" and length > 0)
      and ($body.as_of | type == "string"
        and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
      and ($body.agencies | type == "array" and length > 0)
      and ($body.matches_pin == true)
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
  ' "$LAST_BODY" >/dev/null \
    || fail "assistant /version returned an invalid explicit legacy release identity"
fi
echo "smoke: ok: assistant /version"

# PII must be refused before any rider detail can enter retrieval/model context,
# and the synthetic identifier must never be reflected in the response.
PII_SENTINEL="987-65-4321"
PII_PAYLOAD="$(
  jq -nc --arg question \
    "My Social Security number is $PII_SENTINEL. Do I qualify for a discount?" \
    '{question: $question}'
)"
request POST "$ASSISTANT_BASE_URL/api/ask" "$PII_PAYLOAD"
expect_status_200 "assistant PII refusal"
expect_header_contains "assistant PII refusal" "content-type" "application/json"
check_no_store_security_headers "assistant PII refusal"
jq -e '
  .kind == "refused_input"
  and (.answer | type == "string" and length > 0)
  and (.citations | type == "array" and length == 0)
' "$LAST_BODY" >/dev/null || fail "PII request was not returned as a citation-free refusal"
if grep -Fq "$PII_SENTINEL" "$LAST_BODY"; then
  fail "PII refusal reflected the synthetic identifier"
fi
echo "smoke: ok: assistant PII refusal"

# The expired Yolobus source is an operational kill switch, not a decorative
# /version flag. Prove the rider path fails closed while that source is disabled.
if requires_disabled_document "yolobus-fares"; then
  YOLOBUS_PAYLOAD="$(
    jq -nc --arg question "How much is the local fare on Yolobus?" \
      '{question: $question}'
  )"
  request POST "$ASSISTANT_BASE_URL/api/ask" "$YOLOBUS_PAYLOAD"
  expect_status_200 "assistant Yolobus containment"
  expect_header_contains "assistant Yolobus containment" "content-type" "application/json"
  check_no_store_security_headers "assistant Yolobus containment"
  jq -e '
    .kind == "refused_no_support"
    and (.answer | type == "string" and length > 0)
    and (.citations | type == "array" and length == 0)
  ' "$LAST_BODY" >/dev/null || fail "disabled Yolobus source did not fail closed"
  echo "smoke: ok: assistant Yolobus containment"
fi

# One rehearsed, known-good answer proves the paid serving path and citations.
SAFE_PAYLOAD="$(
  jq -nc --arg question "What proof do I need for the veteran fare on MST?" \
    '{question: $question}'
)"
request POST "$ASSISTANT_BASE_URL/api/ask" "$SAFE_PAYLOAD"
expect_status_200 "assistant safe answer"
expect_header_contains "assistant safe answer" "content-type" "application/json"
check_no_store_security_headers "assistant safe answer"
jq -e '
  .kind == "answered"
  and (.answer | type == "string" and length > 0)
  and (.corpus_version | type == "string" and length > 0)
  and (.as_of_date | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
  and (.citations | type == "array" and length > 0)
  and all(.citations[];
    (.agency | type == "string" and length > 0)
    and (.title | type == "string" and length > 0)
    and (.url | type == "string" and startswith("https://"))
    and (.fetch_date | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
  )
' "$LAST_BODY" >/dev/null || fail "safe question did not return a dated, cited answer"
echo "smoke: ok: assistant safe answer"

echo "smoke: PASS"
