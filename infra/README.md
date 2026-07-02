# infra/

`deploy.sh` is the whole deployment: it bundles the package, corpus snapshot,
prompts, and web handler into one Lambda behind a public HTTP API, with an IAM
role scoped to the pinned answer model. Idempotent; re-run it after any change.
Architecture, cost guards, and rejected alternatives are in ADR 0004
(`docs/decisions/0004-demo-deploy.md`).

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
