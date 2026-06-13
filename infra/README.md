# infra/

`deploy.sh` is the whole deployment: it bundles the package, corpus snapshot,
prompts, and web handler into one Lambda behind a public Function URL, with
an IAM role scoped to the pinned answer model. Idempotent; re-run it after
any change. Architecture, cost guards, and rejected alternatives are in
ADR 0004 (`docs/decisions/0004-demo-deploy.md`).

Constraints inherited from the rest of the repo: no user query persistence
(the handler logs counts and timings, never content; 14-day retention),
pinned model versions, and the deployed corpus is the committed snapshot set.

```sh
AWS_REGION=us-west-2 ./infra/deploy.sh
```
