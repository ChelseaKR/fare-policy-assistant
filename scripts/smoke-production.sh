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
#     --evidence-base-url https://staging-evals.example.org
set -euo pipefail

DEFAULT_ASSISTANT_BASE_URL="https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com"
DEFAULT_EVIDENCE_BASE_URL="https://evals.chelseakr.com"

ASSISTANT_BASE_URL="${FPA_SMOKE_ASSISTANT_BASE_URL:-$DEFAULT_ASSISTANT_BASE_URL}"
EVIDENCE_BASE_URL="${FPA_SMOKE_EVIDENCE_BASE_URL:-$DEFAULT_EVIDENCE_BASE_URL}"
CONNECT_TIMEOUT="${FPA_SMOKE_CONNECT_TIMEOUT:-10}"
MAX_TIME="${FPA_SMOKE_MAX_TIME:-45}"

usage() {
  cat <<'EOF'
Usage: scripts/smoke-production.sh [options]

Options:
  --assistant-base-url URL  Rider assistant origin (API Gateway)
  --evidence-base-url URL   Public evidence/report origin
  --connect-timeout SEC     Per-request connection timeout (default: 10)
  --max-time SEC            Per-request total timeout (default: 45)
  -h, --help                Show this help

Environment equivalents:
  FPA_SMOKE_ASSISTANT_BASE_URL
  FPA_SMOKE_EVIDENCE_BASE_URL
  FPA_SMOKE_CONNECT_TIMEOUT
  FPA_SMOKE_MAX_TIME
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
[[ "$EVIDENCE_BASE_URL" =~ ^https?://[^[:space:]]+$ ]] \
  || fail "evidence base URL must be an absolute http(s) URL"
[[ "$CONNECT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
  || fail "connect timeout must be a positive integer"
[[ "$MAX_TIME" =~ ^[1-9][0-9]*$ ]] \
  || fail "max time must be a positive integer"

ASSISTANT_BASE_URL="${ASSISTANT_BASE_URL%/}"
EVIDENCE_BASE_URL="${EVIDENCE_BASE_URL%/}"

SMOKE_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_TMPDIR"' EXIT

CURL_OPTIONS=(
  --silent
  --show-error
  --location
  --connect-timeout "$CONNECT_TIMEOUT"
  --max-time "$MAX_TIME"
  --retry 2
  --retry-delay 1
  --retry-all-errors
  --user-agent "fare-policy-assistant-production-smoke/1"
)

LAST_STATUS=""
LAST_HEADERS=""
LAST_BODY=""
REQUEST_NUMBER=0

request() {
  local method="$1"
  local url="$2"
  local payload="${3:-}"
  local curl_status

  REQUEST_NUMBER=$((REQUEST_NUMBER + 1))
  LAST_HEADERS="$SMOKE_TMPDIR/headers-$REQUEST_NUMBER"
  LAST_BODY="$SMOKE_TMPDIR/body-$REQUEST_NUMBER"

  if [[ "$method" == "POST" ]]; then
    if ! curl_status="$(
      curl "${CURL_OPTIONS[@]}" \
        --request POST \
        --header "content-type: application/json" \
        --data "$payload" \
        --dump-header "$LAST_HEADERS" \
        --output "$LAST_BODY" \
        --write-out "%{http_code}" \
        "$url"
    )"; then
      fail "$method $url could not be reached"
    fi
  else
    if ! curl_status="$(
      curl "${CURL_OPTIONS[@]}" \
        --request GET \
        --dump-header "$LAST_HEADERS" \
        --output "$LAST_BODY" \
        --write-out "%{http_code}" \
        "$url"
    )"; then
      fail "$method $url could not be reached"
    fi
  fi
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

echo "smoke: evidence=$EVIDENCE_BASE_URL"
echo "smoke: assistant=$ASSISTANT_BASE_URL"

# Static evidence entrypoints: these prove the report origin is healthy without
# treating it as the rider assistant.
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

# Every public, read-only rider entrypoint.
check_assistant_html "/" "Transit Fare Policy Assistant"
check_assistant_html "/offline" "Offline fare reference"
if grep -Fq "All below fares are effective July 1, 2025" "$LAST_BODY"; then
  fail "assistant /offline exposes the contained Yolobus fare period"
fi
check_assistant_html "/guide" "Which fare applies to me?"
if grep -Fq "All below fares are effective July 1, 2025" "$LAST_BODY"; then
  fail "assistant /guide exposes the contained Yolobus fare period"
fi
check_assistant_html "/embed" "Transit fare policy assistant" true

request GET "$ASSISTANT_BASE_URL/version"
expect_status_200 "assistant /version"
expect_header_contains "assistant /version" "content-type" "application/json"
check_no_store_security_headers "assistant /version"
jq -e '
  (.corpus_version | type == "string" and length > 0)
  and (.as_of | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))
  and (.agencies | type == "array" and length > 0)
  and (.matches_pin == true)
  and (.disabled_documents | type == "array" and index("yolobus-fares") != null)
' "$LAST_BODY" >/dev/null || fail "assistant /version returned an invalid corpus payload"
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
