# Data Protection Impact Assessment (DPIA)

Reference implementation, dated 2026-07-11. This DPIA is written to the shape a
UK ICO / GDPR Article 35 assessment expects, because a real agency deploying an
assistant like this would need one. It reflects the demo as built; a production
deployment must re-run it against its own configuration.

## 1. Description of the processing

**What the system does.** A rider asks a natural-language question about
published transit fare and reduced-fare policy. The system retrieves passages
from a versioned, operator-controlled corpus and returns a grounded, cited
answer. It never determines a person's eligibility; it explains published
criteria and routes the decision to the agency.

**What data is involved.**

- *Input:* the rider's typed question. It may incidentally mention a personal
  attribute (an age, a disability, veteran status) because those are the subject
  of fare policy. The system is designed so that such attributes are **not
  needed and not solicited**, and questions containing identifiers (ID numbers,
  birth dates, contact details) are refused before retrieval.
- *Output:* a fare-policy answer drawn only from the corpus.
- *Corpus:* public agency documents, dated and versioned. No personal data.
- *Caller address:* since 2026-08-12 the service processes the requesting IP
  address for rate limiting (ADR 0025). An IP address is personal data. It is
  read from the gateway request context, passed directly into a keyed hash,
  and discarded within the request. It is never logged, never returned, and
  never stored. What is stored is a secret-keyed HMAC digest of
  (window, route, address) truncated to 128 bits, alongside an integer count,
  with a TTL of roughly two minutes. The window is part of the hashed
  material, so the digest changes every 60 seconds and cannot be assembled
  into a session or a usage history. The digest is not reversible to an
  address without the secret, and rotating the secret makes every stored row
  permanently unlinkable. The digest itself is never logged.

**Retention and transient state.** Plaintext rider questions and conversation
history are not written to application logs, databases, or the server answer
cache. The browser keeps visible conversation turns in memory for the current
tab and sends the last three successful turns with a follow-up. A successful
question is processed in Lambda memory and sent to the configured model
provider; the response payload may remain in a bounded in-memory cache keyed by
a process-local HMAC digest of question/history until eviction or container
termination. Guarded and refused inputs are not cached. Request logs carry only
response kind, language, question length, timing, and operational flags;
model-call logs add provider/model, token counts, and estimated cost — never
question or answer text (ADR 0004). CloudWatch log retention is 14 days. There
are no accounts or user profiles.

## 2. Necessity and proportionality

The proportionate design is to avoid identification and minimize rider text.
Answering a fare-policy question does not require an identity, account,
eligibility document, or persistent profile. A rider can still type personal or
special-category information, so the system treats all free text as potentially
personal data: it does not solicit those details, refuses recognized identifiers
before retrieval/model use, keeps no content logs or rider database, and leaves
eligibility decisions to the agency. A production operator must establish and
document its own lawful basis, processor terms, and retention configuration.

**Model-provider logging, verified 2026-08-12.** The application not logging
question text is only half the claim: Amazon Bedrock can be configured to
deliver prompts and completions to CloudWatch or S3 independently of anything
this code does, and until now this document asserted nothing either way. The
account's configuration was read directly:

```
$ aws bedrock get-model-invocation-logging-configuration --region us-west-2
loggingConfig.cloudWatchConfig.logGroupName   /aws/bedrock/invocations
loggingConfig.textDataDeliveryEnabled         false
loggingConfig.imageDataDeliveryEnabled        false
loggingConfig.embeddingDataDeliveryEnabled    false
loggingConfig.videoDataDeliveryEnabled        false
```

Invocation logging is on, so metadata (model, timestamps, token counts) is
captured, and **every data-delivery flag is disabled**, so no prompt or
completion text is delivered to that log group. Rider questions therefore do not
reach `/aws/bedrock/invocations`.

Two limits on that statement, both deliberate. It describes one account at one
moment: the flags are account-wide Bedrock settings, not something this
repository can pin or enforce, so an operator can enable text delivery without
any change here. And it says nothing about the provider's own internal
retention, which is a matter for the processor terms named above. A production
operator should re-run the command above as part of its own review rather than
inherit this finding.

## 3. Risks to data subjects, and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A rider volunteers PII in free text | Medium | Medium | Input guard refuses recognized identifiers before retrieval/model use; refused content is not cached or logged. An unmatched value may still be processed transiently, so the UI warns riders not to provide personal details (`src/assistant/guards.py`, ADR 0004). |
| Sensitive attribute inferred/retained | Low | Medium | No account/profile database and no content logs; attributes are not solicited. Current-tab history and successful answer payloads are transient as described above. |
| Automated eligibility decision affecting a person | Low | High | Out of scope by design: output guard blocks determination language; eval refusal suite tests it; the agency decides (model card, `evals/suites/refusal.yaml`). |
| Re-identification from logs | Low | Low | Logs carry counts/timings only; 14-day retention. No IP, user agent, or rate-limit digest is logged (ADR 0019, ADR 0025); `tests/test_deploy_rate_limit.py` asserts no telemetry helper accepts a caller identifier. |
| Re-identification from rate-limit state | Low | Low | Stored keys are secret-keyed HMAC digests that rotate every 60 seconds and expire in about two minutes. A dump of the table yields no address and no linkage between windows. Rotating the key (a redeploy variable) breaks linkage immediately. Residual: a party holding both the live secret and the table can test whether a *guessed* address is currently being counted, for the length of one window (ADR 0025). |
| One caller denying service to every other rider | Was High, now Low | High | Per-caller quotas on `/api/ask` and `/api/feedback` (ADR 0025). Before that change the gateway throttle was aggregate only, so a single source sustaining 2 rps could turn every rider away at no cost. Distributed floods remain bounded only in aggregate. |
| Third-party processor exposure (model provider) | Low | Medium | Successful question text that passed the identifier guard reaches the configured model; recognized PII is refused first. Production adoption requires review of the operator's current provider terms and retention controls. |

## 4. Residual risk and conclusion

Residual risk is **low**. The dominant control is architectural: the system does
not need or request an identity, creates no rider profile, keeps no content logs,
and does not make decisions about people. The main residual exposure is a rider
typing an identifier the guard does not match, or another person viewing
current-tab history or transient cached output.

One property weakened since the previous revision: the service now processes a
caller's IP address for rate limiting and stores a derived value for about two
minutes (ADR 0025). This is recorded rather than glossed, because "we derive
nothing from the request" was previously true without qualification and is no
longer. The mitigations are described above; the reason for accepting the change
is that the alternative left any single actor able to deny service to every
rider at will, which is a worse outcome for the same people this assessment
protects. A production deployment should
confirm the deployment-hardening checklist in `SECURITY.md`, set appropriate
log/provider retention and processor agreements, conduct legal review, and
re-run this DPIA against its live configuration.

See also: `docs/ai-risk-register.md` (model-specific risks) and
`docs/eu-ai-act-classification.md` (regulatory classification).
