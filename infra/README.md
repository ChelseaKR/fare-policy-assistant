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

The spend backstop beneath the alarms is an account-level AWS Budget. It needs
billing permissions the deploy role may lack, so it is a one-time manual step:

```sh
aws budgets create-budget --account-id <id> --budget \
  '{"BudgetName":"fare-demo","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}'
```

Constraints inherited from the rest of the repo: no user query persistence
(the handler logs counts and timings, never content; 14-day retention),
pinned model versions, and the deployed corpus is the committed snapshot set.

```sh
AWS_REGION=us-west-2 ./infra/deploy.sh
```
