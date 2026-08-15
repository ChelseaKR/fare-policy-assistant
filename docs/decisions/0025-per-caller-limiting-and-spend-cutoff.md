# 0025 — Per-caller rate limiting and a hard spend cutoff

Date: 2026-08-12. Status: accepted. Supersedes the "per-IP rate limiting"
rejection in [ADR 0004](0004-demo-deploy.md) and the reasoning in its
2026-07-08 amendment.

## Context

ADR 0004 built a layered cost story: a gateway stage throttle, reserved
concurrency of 2, a per-container request budget, and a question length cap.
Its 2026-07-08 amendment named the gateway throttle the true cross-container
rate limit and recorded, honestly, the trade it was making:

> the gateway throttle is a single account-wide ceiling, not per-caller, so
> one abusive source can still consume the whole budget for everyone during
> its window.

That trade has two consequences, and both are worse than the amendment
assumed.

**Denial of service at zero cost to the attacker.** The stage throttle is
`ThrottlingRateLimit=2`, `ThrottlingBurstLimit=5`, derived from
`RESERVED_CONCURRENCY=2`. One actor sustaining two requests per second
occupies the entire public allowance. Every real rider gets 429. This costs
the attacker nothing, needs no botnet, and leaves no fingerprint the service
can act on, because nothing anywhere is per-caller. A transit rider trying to
find out whether they qualify for a reduced fare is exactly the person this
service exists for, and they are the one who gets turned away.

**Spend.** `REQUESTS_PER_MINUTE = 8` is per container, and reserved
concurrency is 2, so roughly 16 paid answers a minute can be in flight. At
this repository's own measured cost of about $0.0048 per answer that is
roughly $110 a day against a documented $20 a month budget. The in-process
budget says in its own docstring that it is not a cross-container limit and
resets on cold start. The AWS Budget documented in `infra/README.md` is a
notification, not a control, and as written carries no `CostFilters`, so it
watches the whole account rather than this project.

`POST /api/feedback` had no limit of any kind and skipped the budget check
entirely. It calls no model, but an unlimited endpoint still bills for log
ingestion and can skew the `FeedbackDown` metric an operator is paged on.

## Decision

Add a per-caller limiter and a spend breaker sharing one DynamoDB table, and
scope the budget to this project's cost allocation tag.

**Per-caller limiting** (`web/ratelimit.py`). Both `/api/ask` and
`/api/feedback` count each caller against a fixed-window quota, on separate
quotas so leaving feedback cannot use up a rider's ability to ask a question.
The counter key is
`HMAC-SHA256(secret, schema || window || route || address)` truncated to 128
bits. One `UpdateItem` increments and returns the count atomically. Items
carry a TTL of one window plus slack. Quotas live in `assistant.config` as
reviewed release inputs, so changing one is a release with a new config
version rather than a console edit.

The quotas are deliberately loose: 10 asks and 20 feedback posts per 60
seconds. This limiter is not the spend ceiling. The gateway throttle and
reserved concurrency remain the ceiling, and they are unchanged. Its one job
is to stop a single source from taking the whole allowance, and at 10 per
minute one source can hold at most about 8% of what the gateway admits.

**A hard spend cutoff** (`web/spend_breaker.py`, `infra/deploy-cutoff.sh`). A
second, tiny Lambda subscribes to one SNS topic and writes a single
well-known row into the same table. The rider function reads that row (cached
for 30 seconds per container) and, when it is set, stops making new model
calls. Two independent signals reach the topic:

- A CloudWatch alarm on `EstimatedModelCostUsd`, the token-derived cost
  estimate this service already publishes. This is the fast path and lands
  within minutes.
- The tag-scoped AWS Budget. This is the billing-authoritative path and lags
  8 to 12 hours, so it confirms a runaway rather than catching one.

A tripped breaker degrades to the routes that need no model call. `/`,
`/offline`, `/guide`, `/embed`, and `/version` keep serving, and the cutoff
check sits after the answer-cache lookup so answers already paid for are
still returned. Only a new model call is refused, with a 503 naming
`/offline` and `/guide`. The offline fare reference and the guided fare finder
cover the same published policies, so a rider in a cutoff still gets an
answer, from a static page rather than a model.

**Budget scoping.** The documented budget command gains
`"CostFilters": {"TagKeyValue": ["user:project$fare-assistant"]}`, matching
the `project` tag the deploy scripts already apply.

## Why not AWS WAF

WAF is the reflex answer for per-caller rate limiting, and it cannot be used
here at all: **AWS WAF does not support API Gateway HTTP APIs.** The
supported list is CloudFront, Application Load Balancer, API Gateway REST
API, AppSync, Cognito user pools, App Runner, Bedrock AgentCore Gateway,
Verified Access, and Amplify. The REST versus HTTP comparison table states it
outright, listing WAF as a REST-only feature. This service is an HTTP API
(ADR 0004's June amendment), so buying WAF would first require putting
CloudFront in front of the API or migrating to a REST API.

The cost is disproportionate even setting that aside. In us-west-2 a web ACL
is $5.00 a month and each rule is $1.00 a month, both prorated hourly, plus
$0.60 per million requests. A single rate-based rule therefore costs $6.00 a
month before serving one request: about 30% of this project's entire $20
monthly budget, to protect a service whose actual model spend rounds to a few
dollars. Paying a third of the budget for the control, and needing a
CloudFront distribution or an API migration to be allowed to pay it, is the
wrong trade at this scale.

## What the chosen control costs

Verified against the AWS Price List API for us-west-2 in August 2026:

| Item | List price |
|---|---|
| One standard-resolution CloudWatch alarm | $0.10 per month |
| DynamoDB on-demand writes | $0.625 per million write units; one `UpdateItem` under 1 KB is one unit |
| DynamoDB on-demand reads | $0.125 per million read units; an eventually-consistent `GetItem` under 4 KB is half a unit |
| DynamoDB TTL deletes | free; they consume no write units |
| DynamoDB storage | $0.25 per GB-month, first 25 GB-month free |
| SNS delivery to a Lambda endpoint | free; first million requests a month also free |
| Lambda, arm64 | $0.20 per million requests, $0.0000133334 per GB-second |

At 10,000 limited requests a month that totals about **$0.12 a month**, and in
practice closer to $0.02, since CloudWatch's free tier covers ten standard
alarm metrics and this deployment would have seven. The control costs roughly
0.6% of the monthly budget, against 30% for the WAF option that cannot be
attached to this API anyway.

Note for anyone re-deriving these: DynamoDB's free tier throughput allowance
(25 WCU / 25 RCU) applies to provisioned capacity only, so every on-demand
request unit here is billed from the first one. The storage allowance does
apply.

## Why not a REST API with usage plans

API Gateway usage plans throttle per **API key**. Riders are anonymous and
this project has no accounts by design, so there is no key to throttle
against. Delivering per-caller limiting this way would mean issuing
credentials to transit riders, which is a larger privacy and equity change
than the one being considered here and contradicts CLAUDE.md's "no accounts"
rule. REST APIs also cost about 3.5 times more per request than HTTP APIs,
and switching would mean rebuilding the integration, the alias permission,
and the route reconciliation that ADR 0018's release machinery depends on.

## Why not an AWS Budgets action

A budget action can apply an IAM policy, apply an SCP, or stop EC2 and RDS
instances. That enum is closed. There is no Lambda action type and no
arbitrary SSM document, so a budget action cannot set reserved concurrency or
flip a flag. The only indirect route is an IAM deny, which is a permissions
change with no clean reversal path and would break the health check the
deploy depends on.

Budgets also refresh about three times a day and lag actual usage by 8 to 12
hours. As a circuit breaker that is far too slow. It is kept as the
billing-authoritative second opinion, wired to the same topic.

## Why not just set reserved concurrency to zero

It is the bluntest hard stop and it needs no application change, but it takes
down the static page, the offline fare reference, and the guided fare finder
along with the paid path. That converts a cost event into a rider-facing
outage, when the free routes are exactly what a rider should be handed during
one. Concurrency zero stays documented as an operator's last resort. It is
deliberately not automated.

## Privacy: what this costs, stated plainly

ADR 0004 rejected per-IP limiting twice, most recently in its 2026-07-08
amendment, on the grounds that it "is a shared datastore that persists a
request-derived signal, however coarse, which works against the
no-persistence rule this repo holds to elsewhere." That reasoning was sound
and this ADR overrides it. The honest summary:

**This service now processes a caller's IP address, and stores a value
derived from it for about two minutes.** That is a real reduction in the
"we persist nothing derived from a request" property, and it is the single
most valuable property this project had. It is not being given up quietly.

What the design does to keep the reduction as small as it can be:

1. The address is read from the gateway-populated request context, passed
   straight into an HMAC, and dropped. It is never logged, never returned in a
   response, and never held past the call.
2. What is stored is a keyed digest, not the address and not a truncated
   network. The amendment's own rejected proposal was a /24 truncation, which
   is trivially reversible to a network and identifies a building or a block.
   A secret-keyed digest is not reversible without the secret, so a dump of
   the table does not yield addresses.
3. The window index is part of the digest material, so the digest rotates
   every 60 seconds. Two windows of the same rider produce unrelated keys.
   Nothing in the table can be assembled into a session, a history, or a
   pattern of use, even by someone holding the secret.
4. The digest never reaches the logs either. `log_caller_rate_limited` takes
   a route and a quota and nothing else. CloudWatch gains no pseudonymous
   identifier to correlate on, which keeps ADR 0019's guarantee intact.
5. Rotating the secret makes every existing row permanently unlinkable to any
   address. It is safe to do at any moment and costs one abandoned window.
6. The limiter never reads a client-supplied `X-Forwarded-For`. Trusting one
   would let a caller mint a fresh identity per request anyway.

What is still true after the change, and matters: no question text, no
answer text, no user agent, and no address appears in any log or metric.
`tests/test_deploy_rate_limit.py` asserts several of these mechanically,
including that no telemetry helper accepts a caller identifier as an
argument, so the guarantee is enforced rather than merely promised.

The trade being accepted: a rider whose address is one of several behind a
NAT or carrier-grade NAT shares one key with everyone behind it, so a busy
library or a mobile carrier can hit a quota that an individual would not.
The quotas are set high partly for this reason. The alternative, leaving the
service with no per-caller control at all, means any single actor can deny
every one of those riders service at will, which is the worse outcome for the
same population.

`docs/dpia.md` carries this as processing.

## Consequences

- One actor can no longer starve every rider through the public endpoint
  without distributing across many source addresses.
- A cost runaway now stops in minutes rather than after 8 to 12 hours of
  budget lag, and it stops into the offline guide rather than into an error.
- The rider function gains one dependency and one failure mode. Both fail
  open: an unreachable, misconfigured, or deleted table admits the request and
  logs the condition, returning the service to exactly its pre-0025 posture.
  Failing closed would turn a DynamoDB blip into a rider-facing outage.
- The execution role gains `dynamodb:UpdateItem` and `dynamodb:GetItem` on
  one table. This is a shared-IAM change, so the first deploy after it
  requires `FPA_ALLOW_SHARED_IAM_CHANGE=1` after review, per ADR 0018.
- The breaker's own role may write exactly one item, enforced by a
  `dynamodb:LeadingKeys` condition, so the control plane cannot read or clear
  anyone's rate-limit state.
- Nothing here resets itself. An operator clears the breaker by hand after
  looking at why it tripped.
- Per-caller quotas are release inputs, so the release descriptor and config
  version now cover them. The golden identity values changed accordingly.

## What this does not fix

- The quotas key on a source address, so a distributed flood from many
  addresses is unaffected. Reserved concurrency and the gateway throttle
  remain the only defence there, and they are aggregate.
- The ask quota (10) sits above the per-container in-process budget (8), so a
  burst landing on one warm container can still trip that shared backstop and
  return 429 to everyone on it. That backstop was always aggregate and is
  unchanged.
- The breaker's fast path depends on an application cost estimate, not a
  billing metric. A model priced outside the pinned table records
  `cost_estimate_available=false` and contributes nothing to the alarm, which
  is why `UnpricedModelCalls` alarms separately.
- Up to 30 seconds of model calls per warm container can pass after the
  breaker trips, and up to one alarm period before it trips at all.
- The endpoint remains unauthenticated. Nothing here is a substitute for
  identity, and none of it is claimed to be.

## References

- [Resources you can protect with AWS WAF](https://docs.aws.amazon.com/waf/latest/developerguide/how-aws-waf-works-resources.html)
- [Choosing between REST APIs and HTTP APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html)
- [AWS WAF pricing](https://aws.amazon.com/waf/pricing/)
- [Configuring a budget action](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html)
- [Managing costs with AWS Budgets, including refresh cadence](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
- [ADR 0004 — demo deploy](0004-demo-deploy.md)
- [ADR 0018 — immutable Lambda release control](0018-immutable-lambda-release-control.md)
- [ADR 0019 — privacy-safe runtime observability](0019-privacy-safe-runtime-observability.md)
