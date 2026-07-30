# infra/

`deploy.sh` is the whole deployment: it bundles the package, corpus snapshot,
prompts, and web handler into one Lambda behind a public HTTP API, with an IAM
role scoped to the pinned answer model. It publishes an immutable numbered
version, verifies that version directly, and only then moves the stable `live`
alias. Architecture and cost guards are in ADR 0004. Release control and
rollback are in ADR 0018
(`docs/decisions/0018-immutable-lambda-release-control.md`).

The API Gateway stage throttle is the true cross-container rate limit (its
rate and burst are derived from `RESERVED_CONCURRENCY` at the top of the
script, so they cannot drift independently); `tests/test_deploy_rate_limit.py`
guards that. See the 2026-07-08 amendment in ADR 0004 for why it, not a
per-caller store, is the answer to roadmap P1 item 4.

The rider bundle's dependencies are hash-pinned (roadmap M-7 / audit P1-6):
`deploy.sh` installs only from `infra/requirements-deploy.txt` with
`--require-hashes`, and that file is a `uv export` of the locked runtime set,
so the deployed artifact carries exactly the versions the test suite ran
against. Regenerate it with `make deploy-reqs` after any dependency change;
`tests/test_deploy_requirements.py` fails if it drifts from `uv.lock`. The
operator console bundle (`deploy-console.sh`) still installs from loose
ranges and is not covered by this pin file.

## Immutable release and rollback

Run the full verification gate, merge the reviewed release, switch to a clean
default branch, and deploy:

```sh
make verify
AWS_REGION=us-west-2 ./infra/deploy.sh
```

The script refuses a dirty worktree by default. An emergency operator may set
`FPA_ALLOW_DIRTY_DEPLOY=1`, but that deliberately weakens the source-revision
record and must be noted in the incident timeline.

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
5. runs `infra/check-lambda-version.sh` against that number;
6. retains the old target under `rollback`;
7. moves `live` with a revision guard; and
8. restores the old target and prior rollback pointer automatically if the
   public assistant smoke fails or the process receives `EXIT`, `INT`, or
   `TERM` before verification completes.

Layers, VPC/DLQ/tracing/KMS/EFS settings, ephemeral storage, SnapStart, logging,
and unknown future Lambda configuration fields are release-reviewed state.
`deploy.sh` does not inherit changes to them from `$LATEST`; drift blocks the
release until it is reconciled with the immutable `live` version.
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

The deploy also creates CloudWatch alarms (handler errors, Lambda errors and
throttles, p99 latency, and a Bedrock-call surge as a spend proxy) wired to an
SNS topic `fare-policy-assistant-demo-alerts`. Two metric filters derive the
custom metrics from the handler's structured logs (counts only, never rider
content). To actually be paged, subscribe an endpoint once:

```sh
aws sns subscribe --topic-arn <printed by deploy.sh> \
  --protocol email --notification-endpoint you@example.com
```

The deploy also creates (or overwrites) a CloudWatch dashboard named
`fare-policy-assistant-demo` — same name as the function — carrying the per-day
Bedrock-call cost proxy, a 5-minute traffic panel, p99 duration, and an
alarm-status panel. `deploy.sh` prints its console URL at the end.

The spend backstop beneath the alarms is an account-level AWS Budget. It needs
billing permissions the deploy role may lack, so it is a one-time manual step.
Attach `--notifications-with-subscribers` so the budget actually pages: one
notification at 80% of actual spend and one at a forecasted 100% of the $20
limit, both pointing at the same alerts topic (or email) as the alarms. Swap
the SNS `Address` for the topic ARN printed by `deploy.sh`, or use
`SubscriptionType=EMAIL` with an address:

```sh
aws budgets create-budget --account-id <id> \
  --budget '{"BudgetName":"fare-demo","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[
    {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"SNS","Address":"arn:aws:sns:us-west-2:<id>:fare-policy-assistant-demo-alerts"}]},
    {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"SNS","Address":"arn:aws:sns:us-west-2:<id>:fare-policy-assistant-demo-alerts"}]}
  ]'
```

Constraints inherited from the rest of the repo: no user query persistence
(the handler logs counts and timings, never content; 14-day retention),
pinned model versions, and the deployed corpus is the committed snapshot set.

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
