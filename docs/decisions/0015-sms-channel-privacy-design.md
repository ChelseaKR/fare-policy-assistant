# ADR 0015: SMS access channel — privacy design (gate before build)

Date: 2026-07-08. Status: proposed — design only, pending privacy review. No SMS
code ships in this ADR; per EXP-08 (`docs/ideation/03-expansions.md`), nothing
about the gateway is built until this design (or a revision of it) survives a
skeptical review. This document is that review's input, not its output.

## Decision under review

Add SMS as a second access channel for riders without smartphones or data
plans, fronting the existing `answer_question` pipeline unchanged. The
question this ADR answers is not "should we build it" but "what would a
compliant design look like, given that SMS necessarily hands us a phone
number" — because the hard rule in `CLAUDE.md` ("no PII collection") and SMS
transport ("every inbound message carries the sender's number") are in direct
tension unless the design resolves it explicitly.

## Why gated instead of built

Every other surface in this repo (web chat, `/offline`, `/guide`) reaches the
rider without collecting anything that identifies them. SMS breaks that for
free: the carrier hands the webhook a caller ID on every inbound message,
whether or not the assistant wants it. Building first and reviewing later
would mean a phone number sitting in provider logs, Lambda logs, or a database
before anyone has decided how long that is acceptable, who can see it, or
what happens on a data request or breach. That ordering is backwards for a
project whose whole credibility argument is "we said what we won't do, and we
enforce it" (README will-not-do list, `guards.py` PII checks, model card
§ Data & Privacy). This ADR exists so the privacy posture is decided, named,
and reviewable *before* any provider contract, webhook, or line of routing
code exists — matching the model already used for cost and abuse controls in
ADR 0004 (guards decided at design time, not discovered in production).

## Threat and data-flow model

**What the provider (e.g. Twilio) gives the webhook on every inbound
message:** sender phone number (E.164), message body, a provider-assigned
message SID, receive timestamp, and the number the rider texted (ours).

**What the rider expects to happen:** ask a fare question by text, get an
answer by text, nothing about them retained anywhere the way a fare-policy
question about age, disability, or veteran status implies something about
them.

**What must never happen, mapped to existing hard rules:**

| Existing rule (source) | SMS-specific failure mode it forbids |
|---|---|
| "no PII collection" (`CLAUDE.md`) | Persisting a rider's raw phone number anywhere: application logs, provider console history beyond the minimum retention needed for delivery, a database row, an eval trace. |
| "no persistence of user queries beyond anonymous eval logging in dev" (`CLAUDE.md`, README) | Storing message *bodies* — which, unlike the web chat, arrive pre-linked to a phone number by the transport itself, so body + number together is a re-identifiable rider record even if each is stored "separately." |
| Every answer must cite its source; the assistant never determines eligibility (`CLAUDE.md`, `guards.py`) | None of this changes over SMS. The pipeline is reused, not re-implemented, specifically so the citation and determination-language guards do not need a second implementation to drift from the first. |

## Design (proposed)

1. **Immediate number hashing, nothing raw persisted.** On receipt, the
   webhook handler computes `HMAC-SHA256(secret, e164_number)` before any
   other processing and discards the raw number from memory as soon as the
   provider SDK call needed to send the reply is issued. The HMAC secret is a
   deployment secret (same posture as the Bedrock IAM role in ADR 0004), never
   committed, rotatable, and rotation intentionally invalidates old hashes —
   that is a feature, not a bug, since there is nothing to "recover" by design.
   The hash is used only as a short-lived routing key to match an outbound
   reply to the right inbound webhook invocation within a single
   request/response cycle; it is not a stable rider identifier and nothing
   keys long-lived state off it, because there is no long-lived state.
2. **No message-body logging, no application-level conversation history.**
   The handler passes the message text directly to `answer_question` in
   memory and never writes it to a log, file, or store. This mirrors ADR
   0004's existing rule for the web handler (logs response kind, language,
   length, duration — never question or answer text); SMS reuses the same
   logging call, not a new one, so the two channels cannot silently diverge.
   Each inbound SMS is treated as a stateless, single-turn question exactly
   like a web request — no session, no "reply STOP to unsubscribe" history
   beyond what the carrier/provider requires for opt-out compliance (which is
   the provider's compliance surface, not this application's).
3. **Provider-side retention set to the minimum the provider allows, and
   documented, not assumed.** Twilio (or equivalent) retains message content
   and metadata on its own systems regardless of what this application does;
   the design's job is to (a) configure the account's data-retention/duration
   settings to the shortest offered window, (b) not use provider features
   that create secondary copies (e.g., message logs downstream tools, unless
   scoped identically), and (c) record the provider's actual retention
   default and configured override in this ADR's Consequences section once a
   provider is selected — an unverified retention claim is worse than an
   honest "not yet confirmed."
4. **Content-free operational logs only.** What is safe to log and required
   for operating the channel: timestamp, response kind (answered / partial /
   refused), detected language, message length, duration, guard flags
   tripped (boolean, not content) — the same schema ADR 0004 already uses for
   the web handler, extended with a `channel: sms` field. No phone number,
   hashed or raw, appears in these logs; the hash from step 1 lives only
   inside the single request's execution, not in anything written to disk.
5. **Short-code / provider cost containment.** Reuse the existing per-
   container rate limit and reserved-concurrency pattern from ADR 0004
   (8 answer requests/minute/container, hard concurrency ceiling) so SMS
   cannot become a second, uncapped spend surface; per-message provider
   cost (distinct from Bedrock cost) is a real, non-zero recurring expense
   that a demo-scale deployment should not carry by default — see Open
   questions.
6. **Pilot scope, not general availability.** If this design survives review,
   the excellence bar in EXP-08 calls for "a pilot in one agency's service
   area," not a public number for all five agencies at once — smaller blast
   radius while the design gets its first real-traffic read.

## What this design deliberately does not do

- It does not propose storing a hashed number for any purpose beyond the
  single reply round-trip (no "recognize returning riders," no analytics on
  repeat senders — that would recreate a PII-adjacent identifier under a
  different name).
- It does not propose a rider-facing opt-in/consent flow beyond whatever the
  provider's short-code/A2P registration already requires — carrier-level
  consent (the rider texted first) is the provider's compliance layer; this
  ADR's job is what happens to the number *after* it arrives, not whether
  texting in is allowed.
- It does not select a provider. Twilio is used above as the running example
  because it is the most likely candidate (mentioned in EXP-08's own pitch),
  not a procurement decision.

## Open questions this ADR does not resolve

1. **Provider selection and confirmed retention settings.** Needs an actual
   account and a read of the current (2026) data-processing terms; the table
   above states the *requirement*, not a verified vendor answer.
2. **Short-code vs. long-code vs. toll-free, and their per-message and
   monthly costs.** Materially changes whether a pilot is a few dollars or a
   few hundred a month; ADR 0004's "worst case a few dollars an hour" cost
   story does not currently include SMS.
3. **HMAC secret custody and rotation cadence** — who holds it, where it is
   stored in the deployed Lambda's environment (same mechanism as other
   secrets already in `infra/`), and what rotation cadence is defensible
   without breaking in-flight replies (rotation only needs to survive a
   single request/response window, so this is a smaller problem than typical
   secret rotation, but should be stated explicitly, not assumed).
4. **Who is the "skeptical reviewer"** named in EXP-08's excellence bar. This
   repo has no privacy officer role; for a portfolio project the reviewer is
   plausibly an external reader (the DPIA-style structure of this document is
   written so an outside privacy-minded reader — not just the author — can
   evaluate it) or a real privacy-review checklist run against this document
   before EXP-08 is considered gated open.
5. **Accessibility and language parity over SMS.** The existing Spanish
   parity requirement (`CLAUDE.md`, `evals/suites/multilingual.yaml`) applies
   unchanged, but SMS carriers vary in Unicode/GSM-7 handling for accented
   Spanish characters — segmenting a long accented reply may cost more
   message segments than the equivalent English one. Worth a line in a future
   revision, not a blocker to this design's privacy review.

## Consequences

- Nothing changes yet. No dependency is added, no infra provisioned, no
  webhook route exists. This ADR is the artifact EXP-08 asked for first: "do
  the one-page DPIA-style design first."
- The next step, if this design (or a revised version of it) is accepted, is
  a build PR that implements exactly this: hashing at the boundary, the
  reused `answer_question` call, the extended content-free log schema, and
  the pilot-scope limitation — not a general SMS rollout.
- If this design is rejected or substantially revised, that decision and its
  reasoning should be recorded as an amendment to this ADR (matching ADR
  0004's amendment precedent), not a silent drop of EXP-08.

## Status of the gate

**Not yet reviewed.** This document is the input to that review, drafted so a
privacy-minded reader can evaluate it on its own terms. It should not be read
as "reviewed and passed" — no such review has occurred. EXP-08 remains
build-nothing until that review happens and this ADR (or a revision) is moved
to `Status: accepted`.
