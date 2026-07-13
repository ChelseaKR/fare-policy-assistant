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

**Retention.** Rider questions are answered and discarded. Nothing a user types
is logged or stored. Request logs carry only response kind, language, question
length, and timing; model-call logs add provider/model, fresh/cache token counts,
and estimated cost — never question or answer text (ADR 0004). The answer cache
is in memory and dies with the serverless container. CloudWatch log retention is
14 days. There are no accounts and no user profiles.

## 2. Necessity and proportionality

The lawful, proportionate design is to process **no personal data at all**. The
task (answering a fare-policy question) does not require identifying the rider,
so the system does not. Special-category data (health/disability, and in some
readings income or veteran status) is never collected: the PII input guard
refuses identifiers, the assistant does not ask for them, and the eligibility
decision is explicitly left to the agency, so there is no profiling of
individuals and no automated decision with legal or similarly significant effect
under Article 22.

## 3. Risks to data subjects, and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A rider volunteers PII in free text | Medium | Medium | Input guard refuses questions containing identifiers before retrieval; content is never logged or stored, so a volunteered identifier is not persisted (`src/assistant/guards.py`, ADR 0004). |
| Sensitive attribute inferred/retained | Low | Medium | No persistence, no profiling; attributes are neither solicited nor stored. |
| Automated eligibility decision affecting a person | Low | High | Out of scope by design: output guard blocks determination language; eval refusal suite tests it; the agency decides (model card, `evals/suites/refusal.yaml`). |
| Re-identification from logs | Low | Low | Logs carry counts/timings only; 14-day retention. |
| Third-party processor exposure (model provider) | Low | Medium | Only the question text (no identifiers) reaches the model; provider is Claude on Amazon Bedrock under the account's data terms; no training on inputs. |

## 4. Residual risk and conclusion

Residual risk is **low**. The dominant control is architectural: the system does
not need, request, or retain personal data, and does not make decisions about
people. The main residual exposure is a rider typing an identifier the guard
does not match; because nothing is stored, the exposure is transient. A
production deployment should confirm the deployment-hardening checklist in
`SECURITY.md`, set an appropriate log-retention and data-processing agreement
with the model provider, and re-run this DPIA against its live configuration.

See also: `docs/ai-risk-register.md` (model-specific risks) and
`docs/eu-ai-act-classification.md` (regulatory classification).
