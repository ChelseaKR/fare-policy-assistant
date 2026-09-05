# 0019 — Privacy-safe runtime observability

Date: 2026-07-30. Status: accepted.

## Context

The rider Lambda already avoided logging questions and answers, but its
application logger inherited Python's `WARNING` threshold. The GenAI call
record was emitted at `INFO`, so production discarded it. The remaining
`print()` records were JSON-shaped text without invocation correlation, actual
token-derived cost, or request/model latency separation. CloudWatch therefore
counted answer calls as a cost proxy rather than observing the measurements
needed to explain a slow or unexpectedly expensive release.

This service deliberately stores no rider queries. Improving operational
visibility must not create a shadow conversation store, a high-cardinality
identity signal, or a deployment check that merely proves a synthetic fixture
matches its own filter.

## Decision

Lambda application logs use the managed advanced-logging configuration:

- JSON log format;
- application level `INFO`;
- system level `WARN`;
- log group `/aws/lambda/fare-policy-assistant-demo`; and
- 14-day retention.

The deployment asserts this exact versioned configuration before publication.
It remains part of exact-version reuse, so a plaintext or `WARNING`-only
version cannot be mistaken for the structured candidate.

Application records use fixed messages and structured `LogRecord` extras:

| Event | Level | Purpose |
|---|---|---|
| `genai_call` | `INFO` on a recorded completion, `ERROR` on failure | Canonical `gen_ai.*` model, token, and duration fields plus filter-safe aliases and application-estimated USD |
| `answer_request` | `INFO` | Terminal result kind, bounded language/length/turn counts, cache disposition, request duration, model-called state, and status |
| `handler_error` | `ERROR` | Route class and exception class only |
| `feedback` | `INFO` | Allowlisted verdict, result kind, and language, plus the corpus version the deployment is serving |

`context.aws_request_id` is the sole correlation source. It is serialized as
`runtime_request_id` because Lambda's Python JSON formatter reserves and omits
an `aws_request_id` extra; promotion requires the alias to equal Lambda's
built-in `requestId`. The same anonymous identifier and immutable
`AWS_LAMBDA_FUNCTION_VERSION` appear on the model and answer records. The
handler does not accept a correlation identifier from a body or header and
does not log request headers, IP address, user agent, question, answer, prompt,
history, citations, exception message, or stack. Request IDs are fields for
log investigation, never metric dimensions.

Cost is calculated from observed token usage and the repository-pinned pricing
table. `EstimatedModelCostUsd` is an application estimate, not an AWS billing
metric. An unknown model price is represented as
`cost_estimate_available=false` with no canonical cost value; it is never
silently recorded as zero. `UnpricedModelCalls` alarms on that condition. The
account-level AWS Budget remains the billing-authoritative spend backstop.
Count/sum filters may emit a zero default for quiet minutes. Request and model
duration filters deliberately have no default: synthetic zero samples would
falsify their p50/p95/p99 statistics whenever unrelated logs arrive.

The deploy installs and then re-reads the complete metric-filter contracts.
Before moving `live`, it directly invokes the numeric candidate with the
ordinary paid `/api/ask` path and the top-level marker
`fare_assistant_health: "release-v1"`. Only this Lambda event field activates
the check; body and header copies do nothing. The marker bypasses cache
read/write and the warm-container budget so the check must produce a fresh
model completion, without changing its public answer contract.

The invocation requests Lambda's bounded log tail and the deploy fails closed
unless it contains exactly one correlated `genai_call` and one
`answer_request` with:

- the expected numeric function version and `INFO` level;
- non-negative tokens and request/model durations;
- a priced completion whose canonical and filter-safe values agree;
- `direct_health=true`, `cache=bypass`, and `model_called=true`; and
- none of the prohibited content or request fields.

The deploy then runs CloudWatch's metric-filter tester against those actual
candidate events. Promotion is blocked if the completed-call, estimated-cost,
model-duration, answer-duration, or legacy rollback-compatible call filter
does not match as intended, or if a negative filter overlaps.

The dashboard separates application-estimated daily cost/call counts, traffic,
request/model/Lambda durations, and alarm state. Deployment warns when the
alerts topic has no confirmed subscriber; subscriber identity remains an
operator-controlled setting.

Legacy `print()`-era filters remain for one rollback-compatible release. They
observe the retained plaintext version if an alias rollback occurs and may be
removed only after both `live` and `rollback` target structured-capable
versions.

## Consequences

- A candidate cannot become public merely because its HTTP response is healthy;
  its real model telemetry and the installed filter grammar must also work.
- INFO logging increases record volume, bounded by short retention and the
  existing gateway/concurrency ceilings.
- Alias rollback restores versioned code and logging configuration, but metric
  filters, alarms, dashboards, retention, and SNS subscriptions are shared
  infrastructure. The deploy validates/reconciles them; an alias move alone
  does not.
- The candidate check consumes one real model call and shares reserved
  concurrency with public traffic.
- Fixed schemas sacrifice ad hoc debugging detail in exchange for a reviewable
  privacy boundary.

## Rejected alternatives

- **Log prompts or responses behind a debug flag.** A forgotten flag would
  turn CloudWatch into a rider-content store and contradict the service's
  public privacy promise.
- **Take the feedback record's corpus version from the request body.** The
  `/api/ask` response already hands the page a `corpus_version`, so echoing it
  back is the shortest path. It would also be the only client-controlled
  string on a record whose value is that it has none, and it would let a caller
  attribute verdicts to a corpus the deployment never served. The handler reads
  its own serving version instead, and records `None` if it cannot.
- **Record a hash of the question alongside the verdict.** It would group
  verdicts by question without appearing to store one. Fare questions are short
  and formulaic, so the space is enumerable and the digest is a reversible copy
  of the question rather than a de-identified token. Grouping by corpus version
  and result kind answers the same operational question without the liability.
- **Use client request IDs, IPs, or user agents for correlation.** They add
  spoofable or identifying signals and are unnecessary for one Lambda
  invocation.
- **Treat call count as cost.** Token mix and cache behavior vary; a count is a
  useful surge signal but not a cost estimate.
- **Record unknown prices as zero.** That hides the exact condition most likely
  to make a cost dashboard misleading.
- **Validate filters with hand-authored sample JSON only.** It would not prove
  Python logging extras survive Lambda serialization or that the numbered
  candidate emits the reviewed schema.
- **Add an external collector or tracing backend now.** The low-traffic service
  can answer its current operational questions with bounded native logs and
  metrics. A collector would add a data processor and failure surface without
  a demonstrated need.

## References

- [AWS Lambda advanced logging controls](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs-logformat.html)
- [CloudWatch Logs filter-pattern syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [ADR 0004 — demo deploy](0004-demo-deploy.md)
- [ADR 0018 — immutable Lambda release control](0018-immutable-lambda-release-control.md)
