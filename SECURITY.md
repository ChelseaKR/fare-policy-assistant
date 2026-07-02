# Security

This is a reference implementation and portfolio project, not an official
service of any transit agency. It is deployed as a public demo, so it is built to
be safe to expose, but treat it accordingly: no accounts, no authentication, no
storage of anything a user types.

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than opening a public
issue. Use GitHub's private vulnerability reporting for this repository, or email
ckellyreif@gmail.com. A short proof of concept and the affected file help. There
is no bounty; this is a personal project, and fixes are best effort.

## What protects the assistant

The design treats a rider's question as untrusted input and the published corpus
as the only source of truth.

- **No personal data.** Input is checked for PII (ID numbers, contact details,
  birth dates) and refused before retrieval. Nothing a user types is logged or
  stored; request logs carry only the response kind, language, character count,
  and timing (ADR 0004). The answer cache is in memory and dies with the
  container.
- **No eligibility decisions.** An output guard blocks and replaces any answer
  that decides a person's eligibility, and every answer must carry a citation
  that resolves to the corpus or it is not returned.
- **Prompt-injection and scope guards.** Inputs matching injection patterns or
  adjacent topics the assistant must not advise on (medical, immigration, legal)
  are refused and redirected.
- **Browser output.** Answer text is HTML-escaped before it reaches the DOM,
  citation fields are set as text, and citation links are validated to http(s)
  at the point an answer leaves the server, so a malformed corpus URL cannot
  inject a script-on-click link.
- **Transport and headers.** Responses set `default-src 'none'` CSP, `nosniff`,
  `no-referrer`, and `no-store`; the main page is `x-frame-options: DENY`.
- **Cost and abuse bounds.** A per-container request budget, Lambda reserved
  concurrency, an API Gateway throttle, a 500-character question cap, a request
  body size cap, and an IAM policy scoped to `bedrock:InvokeModel` on the pinned
  model only.

## Deployment hardening checklist

The demo defaults are safe, but a real deployment should confirm each of these.

- **Embedding.** The embeddable widget (`/embed`) defaults to
  `frame-ancestors 'self'`. To let agencies embed it, set `FPA_EMBED_ANCESTORS`
  to their origins, not a wildcard, and redeploy.
- **CI OIDC trust policy.** The CI workflow assumes an AWS role by OIDC for live
  eval runs. The workflow already refuses to assume it on fork pull requests, but
  the role's trust policy is the real boundary: restrict its `sub` claim to this
  repository's `main` ref (for example
  `repo:OWNER/REPO:ref:refs/heads/main`), not `:pull_request` or a wildcard, so a
  fork cannot assume it.
- **Corpus pinning.** Approve a corpus version in `corpus/CHANGELOG.md` and set
  `FPA_PINNED_CORPUS_VERSION`; the `/version` endpoint then reports whether the
  running deploy matches.
- **Forged conversation history.** A follow-up carries prior turns the client
  holds; by default the server accepts any well-formed turn as context (this is
  not a trust boundary — the output guard polices every new answer — but a
  tampered prior "answer" can still be fed back as a leading premise). To
  restrict history to turns this server actually issued, set
  `FPA_HISTORY_HMAC_KEY`: `/api/ask` then returns an HMAC `sig` over each answer,
  the client echoes it back with the turn, and the handler drops any turn whose
  signature does not verify. Off by default for the demo; the `conversation`
  eval suite's `conv-forged-*` cases assert the assistant re-grounds even when
  history is fabricated.

## Known accepted risks

- **Client-supplied conversation history.** A follow-up request may carry prior
  turns supplied by the client. This is context, not a trust boundary: the output
  guard polices every new answer regardless of history, and there is no user
  state to escalate against. A deployment that wants to remove the vector can
  echo only prior questions, not client-provided answer text, or set
  `FPA_HISTORY_HMAC_KEY` to restrict history to server-signed turns (see the
  deployment hardening checklist above).
- **The corpus is trusted input.** Manifest URLs and snapshots are operator
  controlled and committed. Document ingestion (HTML and PDF) is not hardened
  against a hostile corpus; do not point the manifest at untrusted sources.
