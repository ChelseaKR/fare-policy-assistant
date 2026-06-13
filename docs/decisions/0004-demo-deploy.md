# 0004 — Demo deploy: one Lambda behind a Function URL

Date: 2026-06-12. Status: amended 2026-06-12 (see bottom): the public
endpoint is an HTTP API, not a Function URL.

## Decision

The public demo is a single Python Lambda that serves the static page at `/`
and one API route at `POST /api/ask`. It is exposed through a Lambda Function
URL with no API Gateway, no CDN, and no custom domain. `infra/deploy.sh` is
the whole deployment: bundle, IAM role, function, URL, log retention.

## Why this shape

The demo exists so a reader of the eval report can try the assistant behind
it. Traffic is a handful of humans, not a product load. Every additional
component (API Gateway, CloudFront, S3 hosting, a container) would add
surface to secure and explain without changing what the reader sees. A
Function URL gives a stable HTTPS endpoint for free and keeps the entire
serving path inside ~150 lines of reviewable handler code.

The bundle mirrors the repo layout (`src/`, `prompts/`, `corpus/`, `web/`),
so the deployed code resolves paths exactly as a checkout does and nothing is
special-cased for Lambda except the handler module itself.

## Abuse and cost guards

The URL is public and unauthenticated, so spend is bounded by layers rather
than identity:

1. Reserved concurrency 2 on the function. This is the hard ceiling; Lambda
   throttles everything beyond it before any code runs.
2. A per-container budget of 8 answer requests per minute in the handler
   (page loads are not counted). With the concurrency cap this bounds
   Bedrock calls to roughly 16 per minute no matter who is asking.
3. Questions are capped at 500 characters; answers at the pinned 1024
   max_tokens, temperature 0.

Worst case sustained abuse is therefore a few dollars per hour of Haiku
usage, visible immediately in the request-kind logs, and stoppable by
setting reserved concurrency to 0. Normal portfolio traffic rounds to zero.

The IAM role can invoke only the pinned answer model. The judge model and
everything else in the account are out of reach of the public endpoint.

## Privacy

Rider questions are answered and discarded. The handler logs response kind,
language, question length, and duration; never question or answer text.
CloudWatch retention is 14 days. This implements the no-persistence rule in
CLAUDE.md and is stated in the UI footer.

## Rejected alternatives

- **API Gateway + S3 static hosting.** More pieces, IAM glue, and cache
  configuration to explain; no benefit at this traffic level. *(Half
  reversed by the amendment below: API Gateway turned out to be required,
  though S3 still is not.)*
- **Streaming responses.** Nicer perceived latency, but the output guard
  must see the complete text before anything reaches a rider, so streaming
  would only stream after the check anyway.
- **Per-IP rate limiting.** Needs shared state (DynamoDB or similar) and
  starts persisting request metadata keyed by IP, which works against the
  no-persistence rule. The concurrency and budget caps bound spend without
  identifying anyone.

## Amendment (2026-06-12): HTTP API instead of a Function URL

The first deploy answered 403 to every request, anonymous or signed, with a
correct auth-NONE URL config and resource policy. The deployment account
denies anonymous `lambda:InvokeFunctionUrl` at the policy layer (an
org-level public-access control), which no function-level configuration can
override. Rather than weaken the account's posture, the public endpoint is
now an API Gateway HTTP API in front of the same function.

What changed and what did not:

- The handler is untouched. HTTP APIs send the same payload-v2 event shape
  as Function URLs; only the hostname differs.
- API Gateway invokes the function as a service principal scoped to this
  API's ARN, so no anonymous-invoke policy is needed on the function.
- The gateway adds a stage-level throttle (2 requests/second, burst 5)
  ahead of the handler's per-container budget, which strengthens the
  cost-guard story from "Why this shape."
- Cost at portfolio traffic stays effectively zero (HTTP APIs bill about a
  dollar per million requests).

The lesson recorded for adapters of this harness: in managed AWS accounts,
prefer the HTTP API path from the start; auth-NONE Function URLs are
commonly blocked by organization policy.
