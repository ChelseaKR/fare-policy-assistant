# infra/

`deploy.sh` is the whole deployment: it bundles the package, corpus snapshot,
prompts, and web handler into one Lambda behind a public HTTP API, with an IAM
role scoped to the pinned answer model. It publishes an immutable numbered
version, verifies that version directly, and only then moves the stable `live`
alias. Architecture and cost guards are in ADR 0004. Release control and
rollback are in ADR 0018
(`docs/decisions/0018-immutable-lambda-release-control.md`); the structured
logging and promotion gate are in ADR 0019
(`docs/decisions/0019-privacy-safe-runtime-observability.md`).

The API Gateway stage throttle is the true cross-container *aggregate* rate
limit (its rate and burst are derived from `RESERVED_CONCURRENCY` at the top
of the script, so they cannot drift independently);
`tests/test_deploy_rate_limit.py` guards that. It bounds what the service
spends in total but not who spends it, so a per-caller limiter and a spend
breaker sit alongside it (ADR 0025, `docs/decisions/0025-per-caller-limiting-and-spend-cutoff.md`).
See "Per-caller rate limiting" and "Spend cutoff" below.

The rider bundle's dependencies are hash-pinned (roadmap M-7 / audit P1-6):
`deploy.sh` installs only from `infra/requirements-deploy.txt` with
`--require-hashes`, and that file is a `uv export` of the locked runtime set,
so the deployed artifact carries exactly the versions the test suite ran
against. Regenerate it with `make deploy-reqs` after any dependency change;
`tests/test_deploy_requirements.py` fails if it drifts from `uv.lock`. The
operator console bundle (`deploy-console.sh`) still installs from loose
ranges and is not covered by this pin file.

`scripts/build_lambda_zip.py` writes the rider ZIP with sorted paths and fixed
timestamps, modes, and ZIP metadata. Rebuilding unchanged inputs therefore
reuses the same Lambda `CodeSha256` instead of consuming another numbered
version because of installation-time mtimes. The builder rejects symlinks and
special filesystem entries and preserves the existing `__pycache__` and wheel
`RECORD` exclusions. It also omits unused dependency console scripts under
top-level `bin/`, whose generated shebangs otherwise expose the builder's
checkout-specific virtual-environment path and change the artifact digest.

`scripts/copy_tracked_bundle.py` admits only the explicitly selected regular
files recorded in the Git index. It verifies a clean worktree, rejects
symlinks/submodules and unsafe destinations, compares each worktree file with
its reviewed Git blob, and writes the immutable blob bytes. Ignored bytecode,
editor state, credentials, and other checkout debris therefore cannot enter
the first-party portion of the ZIP.

## Applying the ADR 0025 controls (one time)

The per-caller limiter and the spend cutoff need three operator actions, in
this order. Run them from a clean checkout of the merged release.

**1. Deploy the rider, allowing the IAM change.** The execution role gains
`dynamodb:UpdateItem` and `dynamodb:GetItem` on one table. That is a
shared-IAM edit, which an alias rollback cannot undo, so `deploy.sh` refuses it
until you say you have reviewed it. A plain `./infra/deploy.sh` **will fail**
with `shared IAM policy drift detected` until you pass the flag once:

```sh
make verify
FPA_ALLOW_SHARED_IAM_CHANGE=1 AWS_REGION=us-west-2 ./infra/deploy.sh
```

Subsequent releases need no flag. This run also creates the DynamoDB table
`fare-policy-assistant-demo-limits` (on-demand billing, TTL on `expires_at`)
and adds `FPA_RATE_LIMIT_TABLE` and a generated `FPA_RATE_LIMIT_HMAC_KEY` to
the function environment. The deploy credentials additionally need
`dynamodb:CreateTable`, `dynamodb:DescribeTable`, `dynamodb:DescribeTimeToLive`,
`dynamodb:UpdateTimeToLive`, and `dynamodb:TagResource`.

**2. Deploy the spend breaker.**

```sh
AWS_REGION=us-west-2 ./infra/deploy-cutoff.sh
```

**3. Scope the budget.** See "AWS Budget, scoped to this project" below. The
existing `fare-demo` budget has no `CostFilters`, so it currently watches the
whole account.

### What these controls cost per month

Real figures for us-west-2, not "minimal". Sources are linked in ADR 0025.

| Item | List price | Source |
|---|---|---|
| CloudWatch alarm on `EstimatedModelCostUsd` (1 standard-resolution alarm) | **$0.10 / month** | $0.10 per standard-resolution alarm metric |
| DynamoDB writes, at 10,000 limited requests a month | **$0.006 / month** | $0.625 per million write units; one `UpdateItem` under 1 KB is 1 unit |
| DynamoDB reads for the breaker check, at 20,000 reads a month | **$0.002 / month** | $0.125 per million read units; an eventually-consistent `GetItem` under 4 KB is 0.5 units, and it is cached 30s per container |
| DynamoDB storage | **$0.00** | items are a few dozen bytes and expire in about two minutes; the first 25 GB-month is free |
| Breaker Lambda | **under $0.01 / month** | $0.20 per million requests and $0.0000133334 per GB-second on arm64; it runs only when a cutoff signal fires |
| SNS delivery to Lambda | **$0.00** | AWS does not charge for SNS deliveries to Lambda, and the first million requests a month are free |

**Total: about $0.12 a month at list price**, and in practice closer to
**$0.02**, because CloudWatch's free tier covers 10 standard-resolution alarm
metrics and this deployment would have seven. The figure is insensitive to
traffic at this scale: ten times the request volume adds under a cent.

The alternative, one AWS WAF web ACL with one rate-based rule, is **$6.00 a
month** before the first request ($5.00 per web ACL plus $1.00 per rule in
us-west-2), which is about 30% of the project's $20 monthly budget. It also
cannot be attached to an HTTP API at all without first adding CloudFront or
migrating to a REST API. That is the reason for this design; ADR 0025 records
the comparison and the sources for every figure above (verified against the
AWS Price List API for us-west-2, August 2026).

Deleting the table or the breaker stack does not break the service. The
limiter fails open and the rider returns to its previous behaviour.

## Immutable release and rollback

Run the full verification gate, merge the reviewed release, switch to a clean
default branch, and deploy:

```sh
make verify
AWS_REGION=us-west-2 ./infra/deploy.sh
```

The script always refuses a dirty worktree. There is no emergency bypass:
identity-bearing artifacts may name a source revision only when every tracked
and untracked release input is clean. An emergency build must first be
committed and reviewed so the deployed bytes remain attributable.

The one-time transition from a pre-identity production version requires the
operator to name that exact observed numeric baseline:

```sh
FPA_LEGACY_IDENTITY_ROLLBACK_VERSION=<observed-version> \
  AWS_REGION=us-west-2 ./infra/deploy.sh
```

Keep the same variable on an emergency rollback only while `rollback` still
targets that legacy version. After the next successful release advances
`rollback` to an identity-bearing version, omit it; the legacy exception is not
a standing compatibility mode.

The first run against the historical deployment performs a safe bootstrap
before touching `$LATEST`: it publishes the exact current production state,
checks that numbered version, creates `live` and `rollback`, grants API Gateway
permission on `live`, and updates the existing managed integration. The old
unqualified permission is removed only after the qualified route passes and
pre/post-cutover checks prove `$LATEST` did not change during the migration.
Later runs leave the API integration unchanged.

A routine release:

1. inherits operator-owned environment values from the current `live` version;
2. verifies that unmanaged `$LATEST` configuration still matches immutable
   `live`, then stages code and managed configuration;
3. checks the staged code against the locally built ZIP hash and the complete
   intended managed configuration, then publishes or reuses an exact numbered
   snapshot;
4. freezes its runtime patch mode at `FunctionUpdate`;
5. runs `infra/check-lambda-version.sh` against that number, including one
   paid cache-bypassing answer whose actual JSON log tail must satisfy the
   privacy, correlation, token, cost, and duration contract;
6. retains the old target under `rollback`;
7. moves `live` with a revision guard; and
8. restores the old target and prior rollback pointer automatically if the
   public assistant smoke fails or the process receives `EXIT`, `INT`, or
   `TERM` before verification completes.

Layers, VPC/DLQ/tracing/KMS/EFS settings, ephemeral storage, SnapStart, and
unknown future Lambda configuration fields are release-reviewed state.
`deploy.sh` does not inherit changes to them from `$LATEST`; drift blocks the
release until it is reconciled with the immutable `live` version. Logging is
also release-reviewed, but is intentionally managed to exact JSON/INFO/WARN
settings under ADR 0019 rather than inherited.
The initial `live` alias revision is also held as a release-wide baseline, so
a concurrent promotion aborts this deployment instead of mixing settings from
two releases.

The direct health check includes a paid Bedrock answer, so run deployments
sequentially and away from a scheduled demo. It shares the function's reserved
concurrency of two with public traffic. Both the direct check and public smoke
require `yolobus-fares` by default. Their
`--expected-disabled-docs` option accepts a reviewed comma-separated list;
passing an explicit empty string requires no disabled document and omits the
Yolobus refusal probe.

For an operator-initiated rollback:

```sh
AWS_REGION=us-west-2 ./infra/rollback.sh
```

The command rejects `$LATEST`, weighted aliases, and any API shape other than
one integration targeting the qualified `live` alias. It verifies the retained
target's corpus pin, disabled-document state, runtime mode, PII refusal, and
paid answer before moving `live`, then rechecks the integration and runs the
public assistant smoke. The full operation is measured against the 15-minute
recovery objective. A single deadline bounds AWS calls, direct invocation, curl
retries, and the overall command; the public verification window reserves up
to 60 seconds inside that deadline for the guarded restore. If smoke or final
verification fails, times out, or the process is interrupted, the
revision-guarded exit handler restores the displaced version without
overwriting a concurrent alias change.

By default the retained version must still contain `yolobus-fares` in its
disabled-document list. Set `FPA_REQUIRED_DISABLED_DOC_IDS` to a comma-separated
reviewed list; setting it explicitly to the empty string means no document ID
is required.

Both aliases must target numbered versions and must not use weighted routing.
Never delete either target. A
retained version is not safe forever: policy expiry or withdrawal can make an
old artifact unsuitable even when it still returns HTTP 200.

Lambda execution-role policy changes are shared infrastructure and cannot be
rolled back by moving an alias. `deploy.sh` refuses drift from its expected
inline policy. After separate review, set
`FPA_ALLOW_SHARED_IAM_CHANGE=1`; the script applies the policy, smokes the
existing live release, and restores the old policy on failure.

`FunctionUpdate` keeps each published version on the runtime patch it was
tested with. This transfers responsibility for receiving Lambda runtime
security patches to the release process. Redeploy regularly and whenever AWS
publishes a relevant Python runtime update.

## Observability

The Lambda uses advanced JSON logging with application `INFO`, system `WARN`,
and 14-day retention. Fixed-schema records expose anonymous invocation
correlation, answer/model duration, canonical provider/model/token fields, and
token-derived estimated cost. They never contain questions, answers, prompts,
history, citations, request headers, IPs, user agents, exception messages, or
stacks.

The deploy creates CloudWatch alarms for handler errors, Lambda errors and
throttles, p99 Lambda latency, unpriced model completions, and a call surge. It
wires them to `fare-policy-assistant-demo-alerts`. Ten filters cover the
structured metrics plus one-release legacy rollback compatibility. Deployment
re-reads every filter contract, then tests the relevant patterns against the
numeric candidate's actual captured model/answer events before promotion.

To actually be paged, subscribe an endpoint once:

```sh
aws sns subscribe --topic-arn <printed by deploy.sh> \
  --protocol email --notification-endpoint you@example.com
```

Deployment warns if the topic has no confirmed subscriber. It also creates (or
overwrites) the `fare-policy-assistant-demo` dashboard with application-
estimated model cost and call counts, 5-minute traffic, request/model/Lambda
duration, and alarm status. `deploy.sh` prints its console URL at the end.

`EstimatedModelCostUsd` comes from observed tokens and the pinned application
price table. It is not an AWS bill. Unknown prices produce
`UnpricedModelCalls`, never a misleading zero-cost sample.

Constraints inherited from the rest of the repo: no user query persistence
(the handler logs counts and timings, never content; 14-day retention),
pinned model versions, and the deployed corpus is the committed snapshot set.

## Per-caller rate limiting

The gateway throttle above is aggregate. One actor sustaining two requests per
second occupies the whole public allowance and every real rider gets 429, at no
cost to the actor. `web/ratelimit.py` adds the per-caller layer: a fixed-window
counter in DynamoDB, on separate quotas for `/api/ask` (10 per 60s) and
`/api/feedback` (20 per 60s). Quotas are release inputs in
`src/assistant/config.py`, so changing one is a reviewed release with a new
config version, not a console edit.

`deploy.sh` creates the table (`<function-name>-limits`, on-demand billing, TTL
on `expires_at`) and passes its name and a caller-digest secret to the function.
Both are created idempotently; nothing about the table is release state, and
deleting it degrades the service to its pre-ADR-0025 posture rather than
breaking it.

**The counter key is not an IP address.** It is
`HMAC-SHA256(secret, schema || window || route || address)` truncated to 128
bits. The address is hashed and dropped inside the request; it is never logged,
returned, or stored. The window index is inside the hash, so the key rotates
every 60 seconds and two windows of one rider cannot be linked. Rotating
`FPA_RATE_LIMIT_HMAC_KEY` makes every stored row permanently unlinkable to any
address, at the cost of one abandoned window. ADR 0025 states plainly what this
costs relative to the previous "nothing derived from a request is persisted"
posture, and `docs/dpia.md` carries it as processing.

The limiter fails open. If the table is unreachable, missing, or the secret is
unset, requests are admitted and `rate_limit_unavailable` is logged. A DynamoDB
blip must not become a rider-facing outage, and the pre-existing gateway
throttle, reserved concurrency, and in-process budget all still apply.

## Spend cutoff

An alarm pages someone. This actually stops spend. `infra/deploy-cutoff.sh`
deploys a second, tiny Lambda that writes one well-known row into the limiter
table; the rider function reads that row (cached 30s per container) and stops
making new model calls while every route that needs no model keeps serving.

A tripped breaker degrades **to `/offline` and `/guide`**, not to an error
page. `/`, `/offline`, `/guide`, `/embed`, and `/version` keep working, answers
already in the container cache are still returned, and only a new model call is
refused, with a 503 naming the two offline routes. Those pages cover the same
published policies, so a rider still gets an answer.

Two signals reach the breaker's topic:

- a CloudWatch alarm on `EstimatedModelCostUsd`, this deployment's own
  token-derived cost estimate. This is the fast path, landing within one alarm
  period (default 15 minutes);
- the tag-scoped AWS Budget below, which is billing-authoritative but refreshes
  about three times a day and lags real usage by 8 to 12 hours.

Deploy it once, after `deploy.sh` has created the table:

```sh
AWS_REGION=us-west-2 ./infra/deploy-cutoff.sh
```

Tune the trip point with `FPA_CUTOFF_THRESHOLD_USD` (default `0.50`) and
`FPA_CUTOFF_PERIOD_SECONDS` (default `900`). The default is about 104 answers in
a quarter hour at the measured $0.0048 per answer, which is far above real
portfolio traffic and would burn the $20 monthly budget in roughly ten hours if
sustained.

**Nothing clears the breaker on its own.** That is deliberate: an automatic
reset the moment a window looked quiet is not a cutoff. Inspect and clear it by
hand:

```sh
# why did it trip?
aws dynamodb get-item --region us-west-2 \
  --table-name fare-policy-assistant-demo-limits \
  --key '{"pk":{"S":"spend-breaker"}}'

# resume answering (riders recover within ~30s)
aws dynamodb delete-item --region us-west-2 \
  --table-name fare-policy-assistant-demo-limits \
  --key '{"pk":{"S":"spend-breaker"}}'

# trip it by hand, without waiting for the alarm
aws dynamodb put-item --region us-west-2 \
  --table-name fare-policy-assistant-demo-limits \
  --item '{"pk":{"S":"spend-breaker"},"open":{"BOOL":true},"reason":{"S":"manual"}}'
```

The last resort, if the breaker itself cannot be reached, is still to take the
function offline entirely. This stops the offline guide too, so prefer the
breaker:

```sh
aws lambda put-function-concurrency --region us-west-2 \
  --function-name fare-policy-assistant-demo --reserved-concurrent-executions 0
```

### Why not WAF, and what this costs instead

AWS WAF cannot be attached to an API Gateway **HTTP** API at all; it supports
REST APIs, CloudFront, ALB, and a few others, and this service is an HTTP API.
Using it would require fronting the API with CloudFront or migrating to a REST
API first. It also costs $5.00 per web ACL per month plus $1.00 per rule per
month in us-west-2, so $6.00 a month before the first request: about 30% of
this project's $20 monthly budget. ADR 0025 records the full comparison,
including why REST API usage plans (which throttle per API key, and riders have
none) and AWS Budgets actions (which cannot set Lambda concurrency, and lag 8
to 12 hours) were also rejected.

### AWS Budget, scoped to this project

The billing-authoritative backstop. It needs billing permissions the deploy
role may lack, so it stays a one-time manual step. `CostFilters` scopes it to
the `project` cost-allocation tag the deploy scripts apply; without that it
watches every dollar in the account and will alarm on spend that has nothing to
do with this demo. Point its subscribers at the **cutoff** topic printed by
`deploy-cutoff.sh` so a breach trips the breaker as well as paging:

```sh
aws budgets create-budget --account-id <id> \
  --budget '{
    "BudgetName":"fare-demo",
    "BudgetLimit":{"Amount":"20","Unit":"USD"},
    "TimeUnit":"MONTHLY",
    "BudgetType":"COST",
    "CostFilters":{"TagKeyValue":["user:project$fare-assistant"]}
  }' \
  --notifications-with-subscribers '[
    {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"SNS","Address":"arn:aws:sns:us-west-2:<id>:fare-policy-assistant-demo-spend-cutoff"}]},
    {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"SNS","Address":"arn:aws:sns:us-west-2:<id>:fare-policy-assistant-demo-alerts"}]}
  ]'
```

Actual spend over 80% trips the breaker. A *forecast* of 100% only pages, since
a forecast is not evidence that money has been spent. An existing `fare-demo`
budget is updated with `aws budgets update-budget`, not `create-budget`.

`deploy-cutoff.sh` already grants `budgets.amazonaws.com` permission to publish
to the cutoff topic. The `-alerts` topic used by the forecast notification above
is created by `deploy.sh` and does **not** carry that grant, so a forecast
notification sent there will be discarded unless you add it:

```sh
aws sns add-permission --region us-west-2 \
  --topic-arn arn:aws:sns:us-west-2:<id>:fare-policy-assistant-demo-alerts \
  --label AWSBudgets --aws-account-id budgets.amazonaws.com \
  --action-name Publish
```

Or point both notifications at the cutoff topic and subscribe your email there
instead.

## Cost allocation

Every AWS resource the deploy scripts create carries `project=fare-assistant`.
`project` is the cost-allocation tag key activated in Cost Explorer; a resource
created without it lands in the account's untagged bucket, where no per-project
budget or report can see it.

The value is the **portfolio project name**, deliberately neither the repo name
(`fare-policy-assistant`) nor the function name (`fare-policy-assistant-demo`):
it is the key the budget and the cross-repo cost report group on, so it has to
survive a rename of either. `tests/test_deploy_tagging.py` guards that, and
guards that no resource silently drops back out of the tagged set.

Both scripts tag on create *and* re-apply the tag on every deploy. The
re-apply is the part that matters in an account that has already been deployed
to: create-time tags never reach a resource that already exists.

**Tagged:** the rider and console Lambda functions (tags cover every published
version), their IAM roles, their CloudWatch log groups, both HTTP APIs, the
`-alerts` SNS topic, and all six CloudWatch alarms. `deploy.sh` also tags the
per-caller limiter DynamoDB table; `deploy-cutoff.sh` tags the breaker
function, its role and log group, the `-spend-cutoff` SNS topic, and the
model-spend alarm.

**Untaggable — AWS accepts no tags on these**, and none of them bills
separately from a tagged parent: CloudWatch metric filters, the CloudWatch
dashboard, Lambda aliases and published versions, the API `$default` stage and
route, and inline IAM role policies.

**Outside the scripts, so not tagged by them:** the `fare-demo` budget and the
console's SSM token parameter, both of which are documented one-time manual
steps above. Tag those by hand if you want them attributed.

The deploy credentials need tag permissions for this to take effect —
`lambda:TagResource`, `iam:TagRole`, `logs:TagResource`, `sns:TagResource`,
`cloudwatch:TagResource`, `dynamodb:TagResource`, and `apigateway:POST` on
`/tags/*`. If any are
missing the deploy still succeeds (the service is live and verified before the
tagging runs) but prints a `WARNING` naming each resource left untagged.

## Agency operator console (EXP-09)

`deploy-console.sh` deploys a second, separate Lambda + API Gateway route: the
agency operator console (`web/console.py`). It is currently a read-only view of
the immutable `live` alias, corpus history/diffs, and evaluation evidence. It
never shares code, a Lambda, or an IAM role with the rider-facing deploy above;
its role holds only `lambda:GetAlias` / `lambda:GetFunctionConfiguration`
scoped to the one rider function and its `live` alias.

The historical pin and embed-setting POST routes now return `409 Conflict`.
Published Lambda versions cannot be edited, so reporting success after changing
unqualified `$LATEST` would be false. Re-enable those controls only after a
durable approval store and promotion workflow can turn an operator request into
a reviewed immutable release.

```sh
FPA_RIDER_FUNCTION_NAME=fare-policy-assistant-demo \
FPA_CONSOLE_TOKEN_PARAMETER_NAME=/fare-policy-assistant/demo-console-token \
AWS_REGION=us-west-2 ./infra/deploy-console.sh
```

**Authentication is the operator's job to finish, not this script's.** Out of
the box the console is gated by a shared bearer token
stored as an encrypted SSM parameter — the handler (`web/console.py`) fails
closed if it cannot resolve that parameter, but a shared token is not identity.
Before handing the
console URL to a non-technical agency operator, put a real authorizer (JWT or
IAM, backed by the agency's own SSO/IdP) in front of the console's API Gateway
route; the exact `aws apigatewayv2 create-authorizer` invocation depends on
that agency's identity provider, so it is documented as a one-time manual step
in `deploy-console.sh`'s header comment rather than automated here, the same
way the AWS Budget setup above is manual.

The console's corpus changelog is a static file
(`corpus/version_history.json`, `make history`), not a live `git` query: the
standard Lambda Python runtime has no git binary, and reading committed
history at request time would mean bundling a full `.git` directory for no
good reason. `deploy-console.sh` regenerates and bundles it fresh on every
deploy.
