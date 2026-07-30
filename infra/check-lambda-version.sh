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
if [[ ${FPA_EXPECTED_DISABLED_DOC_IDS+x} ]]; then
  EXPECTED_DISABLED_DOC_IDS="$FPA_EXPECTED_DISABLED_DOC_IDS"
else
  EXPECTED_DISABLED_DOC_IDS="yolobus-fares"
fi
DEADLINE_EPOCH=""

usage() {
  cat <<'EOF'
Usage: infra/check-lambda-version.sh --qualifier VERSION [options]

Options:
  --function-name NAME          Lambda function (default: fare-policy-assistant-demo)
  --qualifier VERSION           Required numeric published version
  --expected-corpus VERSION     Required 12-character corpus identity
  --expected-disabled-docs IDS  Required comma-separated disabled document ids
                                (default: yolobus-fares; "" means none)
  --deadline-epoch EPOCH        Stop network checks by this Unix timestamp
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
    --deadline-epoch)
      (($# >= 2)) || fail "--deadline-epoch requires a value"
      DEADLINE_EPOCH="$2"
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

HEALTH_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/fare-assistant-version-health.XXXXXX")"
chmod 700 "$HEALTH_TMPDIR"
trap 'rm -rf "$HEALTH_TMPDIR"' EXIT

LAST_PAYLOAD=""
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
  local event_path
  local metadata_path
  local payload_path

  REQUEST_NUMBER=$((REQUEST_NUMBER + 1))
  event_path="$HEALTH_TMPDIR/event-$REQUEST_NUMBER.json"
  metadata_path="$HEALTH_TMPDIR/metadata-$REQUEST_NUMBER.json"
  payload_path="$HEALTH_TMPDIR/payload-$REQUEST_NUMBER.json"
  LAST_PAYLOAD="$payload_path"

  jq -nc \
    --arg method "$method" \
    --arg path "$path" \
    --arg body "$body" \
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
    } + if $body == "" then {} else {body: $body} end' >"$event_path"

  local -a aws_timeout_options=()
  local remaining
  local connect_timeout
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
}

invoke_event GET / "" "root"
jq -e '
  (.body | type == "string" and contains("Transit Fare Policy Assistant"))
  and .headers["cache-control"] == "no-store"
' "$LAST_PAYLOAD" >/dev/null || fail "root response failed content/security checks"
echo "version health: ok: root"

invoke_event GET /version "" "version"
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
  ' "$LAST_PAYLOAD" >/dev/null \
  || fail "/version did not match the approved corpus and containment state"
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
invoke_event POST /api/ask "$SAFE_BODY" "safe paid answer"
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

echo "version health: PASS: $FN:$QUALIFIER"
