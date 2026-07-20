# infra/

`deploy.sh` is the whole deployment: it bundles the package, corpus snapshot,
prompts, and web handler into one Lambda behind a public HTTP API, with an IAM
role scoped to the pinned answer model. Idempotent; re-run it after any change.
Architecture, cost guards, and rejected alternatives are in ADR 0004
(`docs/decisions/0004-demo-deploy.md`).

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

```sh
AWS_REGION=us-west-2 ./infra/deploy.sh
```

## Agency operator console (EXP-09)

`deploy-console.sh` deploys a second, separate Lambda + API Gateway route: the
agency operator console (`web/console.py`). It exists so approving a corpus
version, reviewing the changelog/diff, and setting the embed widget's allowed
origins are a page with a button instead of an `aws lambda
update-function-configuration` command someone has to remember the flags for.
It never shares code, a Lambda, or an IAM role with the rider-facing deploy
above; its role holds only `lambda:GetFunctionConfiguration` /
`lambda:UpdateFunctionConfiguration` scoped to the one rider function ARN.

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
way the AWS Budget setup above is manual. An operator can pin a version,
review a diff, and update embed origins entirely from the console page once
deployed — see that script for the full walkthrough.

The console's corpus changelog is a static file
(`corpus/version_history.json`, `make history`), not a live `git` query: the
standard Lambda Python runtime has no git binary, and reading committed
history at request time would mean bundling a full `.git` directory for no
good reason. `deploy-console.sh` regenerates and bundles it fresh on every
deploy.
