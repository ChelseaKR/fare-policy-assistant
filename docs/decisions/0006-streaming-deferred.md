# ADR 0006: Streaming responses stay deferred

Date: 2026-06-16. Status: accepted.

## Decision

The demo does not stream answers. It returns the complete, guarded answer in a
single response. This revisits the streaming question raised in ADR 0004 and
defers it deliberately, for two independent reasons, with the answer cache as
the latency mitigation that actually ships.

## Why streaming does not pay here

**The deployment cannot stream.** ADR 0004's amendment records that this
account denies anonymous `lambda:InvokeFunctionUrl`, so the public endpoint is
an API Gateway HTTP API. HTTP APIs buffer the Lambda response — they do not
support server-sent events or chunked streaming. Lambda response streaming
needs a Function URL with the `RESPONSE_STREAM` invoke mode, which is the exact
path the org policy blocks for anonymous callers. So streaming would require
either authenticating the endpoint (it is a public demo) or an org-policy
change. Neither is worth it for a portfolio demo.

**The guard limits the win even with the infra.** The output guard must see the
complete answer before any of it reaches a rider: a determination phrase or a
missing citation can only be judged on the whole text, and an answer that fails
the guard is replaced wholesale (`answer.py`). Streaming raw model tokens would
risk showing forbidden content that the guard later removes. The safe forms are
(a) generate fully, guard, then replay the validated text as a typing
animation — no latency win, only a cosmetic effect — or (b) guard each
completed sentence before releasing it, which is real but complex and still
gates on sentence boundaries, not first token. ADR 0004 already noted the
first; neither clears the bar.

## What ships instead

The per-container answer cache (P1) is the latency change that matters in
practice: a repeated question drops from roughly twelve seconds to under a
fifth of a second, verified live. First-time questions still wait on the model,
and the UI shows a clear "looking through the published policies…" state during
that wait.

## Revisit if

The demo moves to a Lambda Function URL with `RESPONSE_STREAM` (e.g. the org
policy changes, or the demo gains a light auth layer), and there is appetite
for sentence-level guarded streaming. At that point the win is real and the
guard can be preserved at the sentence granularity it already uses
(`redact_determination_language` splits on sentences). Until then, streaming is
complexity without a safe payoff.
