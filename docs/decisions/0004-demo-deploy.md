# 0004 — Demo deploy: one Lambda behind a Function URL

Date: 2026-06-12. Status: amended 2026-06-12 (the public endpoint is an HTTP
API, not a Function URL) and 2026-07-08 (the gateway throttle is now the
documented, tuned, tested cross-container rate limit; see bottom).
**Partially superseded 2026-08-12 by
[ADR 0025](0025-per-caller-limiting-and-spend-cutoff.md):** the rejection of
per-caller rate limiting below, and the reasoning repeated in the 2026-07-08
amendment, no longer hold. The rest of this record stands.

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

1. An API Gateway stage throttle (rate and burst derived from the
   concurrency figure below in `infra/deploy.sh`). This is the true
   cross-container rate limit: AWS enforces it before any container runs, so
   it holds identically across cold starts and concurrent containers. See
   the 2026-07-08 amendment below.
2. Reserved concurrency 2 on the function. This is the hard ceiling on
   parallelism; Lambda throttles everything beyond it before any code runs.
3. A per-container budget of 8 answer requests per minute in the handler
   (page loads are not counted), as defense in depth within a single warm
   container. It is not itself cross-container: it resets on cold start and
   is invisible to sibling containers, which is why it is not layer 1.
4. Questions are capped at 500 characters; answers at the pinned 1024
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

## Amendment (2026-07-08): a true cross-container rate limit (roadmap P1 item 4)

The June amendment above added a gateway throttle mostly as a side effect of
switching to an HTTP API. `docs/ROADMAP.md` kept item 4 open afterward
because that throttle was never tuned against a documented figure, never
named anywhere as *the* cross-container ceiling, and had no test — someone
editing `infra/deploy.sh` could delete or weaken it without anything
noticing. Meanwhile the per-container budget in `web/handler.py` (layer 3
above) was written about, and read, as if it were the real limit, when it is
not: it lives in one container's memory, so it resets on cold start and a
burst spread across several warm containers never trips it.

What changed:

- `infra/deploy.sh` now declares `RESERVED_CONCURRENCY=2` once and derives
  both the Lambda concurrency setting and the gateway throttle's
  `ThrottlingRateLimit` / `ThrottlingBurstLimit` from it (rate equals
  concurrency; burst is `concurrency * 2 + 1`), so the two can no longer
  drift out of sync the way two independent hardcoded numbers could.
- `tests/test_deploy_rate_limit.py` parses the script and fails if the
  throttle is removed, hardcoded back to independent numbers, or the
  rate/burst relationship stops being sane (burst below rate).
- `web/handler.py`'s module docstring, the `REQUESTS_PER_MINUTE` comment, and
  `_over_budget`'s docstring now say plainly that the gateway throttle is the
  cross-container guarantee and the in-process budget is a backstop, not the
  other way around. No behavior in the handler changed.
- This doc's "Abuse and cost guards" list above is reordered so the gateway
  throttle is layer 1.

What did not change, and why: this still does not add per-caller (even
coarse-signal) tracking in a shared store. The roadmap item's second option
("a token-bucket keyed on a coarse, non-PII signal") was considered again —
concretely, a DynamoDB item keyed on the caller's IP truncated to its /24
network, with a short TTL so nothing outlives the window — and rejected for
the same reason the original "Rejected alternatives" section rejected
per-IP rate limiting: it is a shared datastore that persists a
request-derived signal, however coarse, which works against the
no-persistence rule this repo holds to elsewhere (CLAUDE.md, ADR privacy
sections). The gateway throttle achieves the roadmap item's actual "Done"
criterion — a documented, tested request ceiling that holds across
containers without persisting anything identifying — more strictly than a
DIY token bucket would: we never see, store, or key on any signal at all: no
IP, truncated or otherwise. AWS enforces the ceiling internally and reports
back only "throttled" or "not."

Trade-off accepted: the gateway throttle is a single account-wide ceiling,
not per-caller, so one abusive source can still consume the whole budget for
everyone during its window. That trade-off already existed before this
change (the per-container budget was likewise global, not per-caller) and is
judged acceptable at this project's traffic and stakes: reserved concurrency
still caps worst-case spend, and a spend or error spike still pages someone
per the P1 item 3 observability work.
