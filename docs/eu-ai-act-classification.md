# EU AI Act classification

Reference implementation, dated 2026-07-11. A good-faith classification of this
system under Regulation (EU) 2024/1689 (the EU AI Act), written because a
European public body — or a US agency benchmarking against the strictest
available framework — would ask for one. It is an engineering assessment, not
legal advice.

## Summary

**This system, as designed, is not high-risk.** It is a limited-/minimal-risk
information tool subject mainly to the Article 50 transparency obligation. The
reason it stays out of the high-risk tier is the same design choice the whole
project is built on: **it does not evaluate anyone's eligibility.** A variant
that did would land squarely in Annex III, and that boundary is drawn
deliberately.

## Walking the tiers

**Prohibited (Article 5).** None apply. No social scoring, no biometric
categorisation, no manipulation, no emotion inference.

**High-risk (Article 6 + Annex III).** The relevant entry is Annex III(5)(a):
AI systems intended to be used *by public authorities to evaluate the
eligibility of natural persons for essential public benefits and services*, or
to grant, reduce, or revoke them.

This system is adjacent to that category and deliberately outside it:

- It **explains published criteria** ("the criteria are 65 and older") and
  **routes the decision to the agency**. It never outputs an eligibility
  determination about the individual asking.
- That line is **enforced, not merely promised**: an output guard blocks
  determination language, every answer must cite the corpus, and a dedicated
  refusal eval suite tests both. See `src/assistant/guards.py` and
  `evals/suites/refusal.yaml`.
- It performs **no profiling** and makes **no automated decision producing legal
  or similarly significant effects** on a person (also the Article 22 GDPR line;
  see `docs/dpia.md`).

Because the system does not *evaluate eligibility* and is not used to grant or
reduce a benefit, Annex III(5)(a) is not triggered. The design keeps the
human/agency as the decision-maker and positions the tool as published-policy
retrieval, not adjudication.

**The boundary, stated plainly.** If this were changed to tell a rider whether
they qualify, or to issue or deny a reduced-fare credential, it would become a
high-risk system under Annex III(5)(a), pulling in Article 9–15 obligations
(risk management, data governance, logging, human oversight, accuracy/robustness
documentation). The project treats "never determine eligibility" as the
architectural invariant that holds it below that line.

**Limited risk (Article 50 — transparency).** This is where the system sits. It
interacts with natural persons, so users must be informed they are dealing with
AI. That obligation is met: the live page opens with a "reference
implementation" banner stating it is an AI demonstration and not an official
agency service, the "will not do" list is shown before the input, and every
answer is dated and cited.

**Minimal risk.** The remainder of the system's behaviour (information retrieval
over public documents) falls here, with no additional obligations.

## GPAI note

The system uses a general-purpose model (Claude) via a provider, not a
first-party GPAI model placed on the market, so the GPAI-provider obligations
(Article 51+) fall on the model provider, not this deployer.

## What a production deployment would still owe

Even at limited risk, an agency putting this in front of real riders should keep
the Article 50 disclosure prominent, maintain the DPIA and the AI risk register
(`docs/dpia.md`, `docs/ai-risk-register.md`), and re-classify if the scope ever
moves toward eligibility decisions. The classification is only as durable as the
no-determination invariant, which is why that invariant is guarded in code and
tested, not left to the prompt.
