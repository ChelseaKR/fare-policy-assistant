# Research-Backed Roadmap — fare-policy assistant

> **What this is.** A roadmap derived from the synthetic persona panel in
> [`USER-RESEARCH.md`](USER-RESEARCH.md), grounded in real external evidence (fare
> policy, language-access law, chatbot-evaluation research, AI-liability precedent).
> It **complements** the existing [`docs/ROADMAP.md`](ROADMAP.md) and the earlier
> [`research/synthetic-personas-feedback.md`](research/synthetic-personas-feedback.md)
> backlog; it does not restate or replace either. `docs/ROADMAP.md` covers
> productionalization (P0 integrity, P1 hardening, P2 features, P3 breadth);
> `synthetic-personas-feedback.md` covers the R0–R3 rider/trust backlog already
> partly executed. This document adds the layer both lack: **outside evidence** for
> *why* each item matters, and the items that only the research surfaces.
>
> Every item is tagged **[corroborates …]** (an existing roadmap/backlog item that
> external evidence now reinforces — triangulation, not noise) or **[NET-NEW]** (only
> this research surfaced it). The hard rules in [`CLAUDE.md`](../CLAUDE.md) bind every
> item: no eligibility determinations, no PII, every answer cited, corpus dated.
> Nothing here relaxes them; several items strengthen them.
>
> **Assembled 2026-06-30.** All source access dates 2026-06-30.

## How to read priority and effort

- **Priority.** **P0** integrity/trust and legal-duty gaps (do first) · **P1**
  rider-facing correctness and reach · **P2** expansions · **P3** larger bets.
- **Effort.** S ≈ an afternoon · M ≈ a day or two · L ≈ a week+.
- **Evidence.** Each item cites the `E#` evidence below. High-stakes claims are
  cross-checked against ≥2 sources.

---

## Research basis / evidence

| E# | Finding (with sources) | Why it bears on this repo |
|---|---|---|
| E1 | **Operators are liable for their chatbot's wrong answers.** BC tribunal held an airline liable for its bot's fare advice ([CBC](https://www.cbc.ca/news/canada/british-columbia/air-canada-chatbot-lawsuit-1.7116416), [ABA](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/)). | A wrong fare or eligibility line is an agency liability, not a demo bug. Justifies louder confirm-with-agency framing and the no-determination boundary. |
| E2 | **Government chatbots have shipped illegal/wrong advice and stayed live.** NYC MyCity told businesses to break the law ([THE CITY](https://www.thecity.nyc/2024/04/02/malfunctioning-nyc-ai-chatbot-still-active-false-information/), [SHRM](https://www.shrm.org/topics-tools/employment-law-compliance/nyc-ai-chatbot-faulty-legal-advice), [OECD.AI incident](https://oecd.ai/en/incidents/2024-03-29-3dce)). | The failure mode this project is built to prevent. Strengthens the case for visible failures, refusal, and the independent audit. |
| E3 | **RAG lowers but does not eliminate hallucination; faithfulness is the metric, and LLM-judges carry biases needing human calibration.** ([Patronus](https://www.patronus.ai/llm-testing/rag-evaluation-metrics), [faithfulness-metrics review](https://arxiv.org/pdf/2501.00269), [faithfulness leaderboard](https://arxiv.org/html/2505.04847v2), [K2view](https://www.k2view.com/blog/rag-hallucination/)). | Faithfulness drift is the real frontier (`conv-004/005`, `ground-026`); calibration on n=16 is thin by the literature's own standard. |
| E4 | **Cal-ITP Benefits is a permanent CA service verifying CalFresh (low-income proxy), Medicare, and more for reduced fares; 50+ agencies onboarding.** ([launch](https://www.calitp.org/press/cal-itp-benefits-launch), [low-income pathway](https://docs.calitp.org/benefits/enrollment-pathways/low-income/), [Medicare pathway](https://docs.calitp.org/benefits/explanation/enrollment-pathways/medicare-cardholders/)). | MST/SBMTD are live on it. The verification boundary is real; the means-based path is the corpus's thinnest, churniest area. |
| E5 | **Means-based programs are complex and change.** Clipper START (≤200% FPL; launched [20% in 2020](https://www.bart.gov/news/articles/2020/news20200715), now 50% per [MTC](https://mtc.ca.gov/planning/transportation/access-equity-mobility/clipperr-startsm)); [LA Metro LIFE](https://www.metro.net/about/l-a-metros-low-income-fare-is-easy-life-program-hits-over-250000-enrollments/) (250k+ enrollments, income self-certification). | Income thresholds, self-certification, and discount-rate churn are exactly what an assistant must describe precisely, date carefully, and never adjudicate. |
| E6 | **Reduced-fare take-up is gated by process, not desire** — in-person application, clinician documentation, no online path, weak outreach ([NADTC](https://www.nadtc.org/news/blog/understanding-half-farereduced-fare-requirements/), [RPA](https://rpa.org/work/reports/reduced-fares)). | Validates the "last step is missing" theme: riders need where/cost/hours joined to the criterion. |
| E7 | **Language access is a legal duty.** FTA [Title VI Circular 4702.1B](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf) + [DOT LEP guidance](https://www.transportation.gov/civil-rights/civil-rights-awareness-enforcement/dots-lep-guidance): meaningful access, four-factor analysis, safe harbor at 5% / 1,000 persons. | Reframes multilingual parity as compliance evidence an agency Title VI officer needs, not a nice-to-have. |
| E8 | **California's top-five threshold languages are Spanish, Chinese, Tagalog, Vietnamese, Korean** ([CalHHS/DHCS](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/APL%202025/Threshold-and-Concentration-Languages-for-All-Counties.pdf)). | Names the concrete stretch-language targets and their priority order. |
| E9 | **Multilingual LLM quality is uneven.** Training corpora skew English; models score 13.8–16.7 pts lower in low-resource languages; Spanish strong but not parity ([survey](https://pmc.ncbi.nlm.nih.gov/articles/PMC11783891/), [MuBench](https://arxiv.org/pdf/2506.19468)). | Justifies measuring parity, tagging stretch languages as non-parity, and being honest about cross-lingual retrieval over English docs. |
| E10 | **Chatbot accessibility needs a manual pass.** Polite live regions, `role=log`, focus discipline, manual NVDA/JAWS testing ([W3C ARIA23](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA23), [Orange chatbot guidelines](https://a11y-guidelines.orange.com/en/articles/chatbot/)). | The pending walkthrough is the credibility gate; automation is explicitly not the sign-off. |
| E11 | **Transit chatbots are deploying — and stumbling.** CTA's multilingual bot covers 5 languages incl. Chinese & Tagalog ([Google public sector](https://publicsector.google/ai/chicago-transit-authority-launches-a-multi-lingual-chatbot-for-more-a-more-seamless-commute/)); MTA's OMNY assistant needed +$3M amid complaints ([THE CITY reporter](https://www.thecityreporter.nyc/2025/05/28/mta-omny-ai-chat-support-customer-complaints/)); [WMATA MetroAccess](https://www.wmata.com/about/news/Metro-Access-launches-24-7-digital-assistant-to-help-customers-book-and-manage-trips-faster.cfm) launched a digital assistant. | A live, imperfect market with no public accuracy claim — the gap a stamped, evaluated harness fills. |
| E12 | **Fare policy churns; fare-free pilots reverse.** NYC ended its free-bus pilot Sept 2024 ([NY1](https://ny1.com/nyc/all-boroughs/traffic_and_transit/2024/09/01/free-bus-fare-program-comes-to-an-end)); Philadelphia continued its Zero Fare pilot ([phila.gov](https://www.phila.gov/2024-10-30-city-of-philadelphias-free-public-transportation-pilot-program-zero-fare-showing-positive-results-in-its-first-year/)). | Freshness is not optional. Validates corpus-version pinning, staleness disclosure, and the freshness suite. |
| E13 | **NIST AI RMF is the procurement lingua franca.** Govern/Map/Measure/Manage; vague vendor answers are themselves a risk signal ([NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework), [GenAI Profile NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)). | Gives the procurement persona a recognizable crosswalk to map the existing docs onto. |

---

## Remediation backlog (close gaps in what exists)

| ID | Item | Personas | Pri | Effort | Evidence & tag |
|---|---|---|---|---|---|
| RR1 | **Close the loop on the next physical step.** When a fare/eligibility question has a matching application or ID-card passage, the answer must include where to apply, the cost, and hours — not just the criterion. Add edge cases asserting the next step is present. ✅ Implemented 2026-06-30 (working tree, uncommitted) | R1,R4,O1,O3 | P0 | M | E6 · **[corroborates** `synthetic-personas-feedback` R1-2 / theme "the last step is missing"**]** |
| RR2 | **Above-the-fold liability framing.** Lead the GovChat lexical-judge floor (groundedness 0.04, multilingual 0.667) with its explanation in README and the embed; make the "confirm with the agency" frame and staleness note unmissable inside `/embed`. ✅ Implemented 2026-06-30 (working tree, uncommitted) | A4,B1,B3 | P0 | S | E1,E2 · **[corroborates** `synthetic-personas-feedback` R0-2; **NET-NEW** liability rationale**]** |
| RR3 | **Means-based / Cal-ITP path depth.** Strengthen retrieval and prompting for income-based eligibility (CalFresh proxy, Medicare-vs-age, Cal-ITP verification) so "am I low-income enough?" returns the published criterion plus a clean handoff to verification, never an adjudication. Add edge cases per agency on the MST/SBMTD Cal-ITP overlap. | R4,B2 | P0 | M | E4,E5 · **[NET-NEW]** (deeper than ROADMAP P3 "more agencies") |
| RR4 | **Positive verification handoff, tested.** On every eligibility-adjacent answer, state "the agency / Cal-ITP decides; here is how to start," as a deterministic check. Strengthens, never relaxes, the no-determination rule. ✅ Implemented 2026-06-30 (working tree, uncommitted) | B2,O1,R4 | P0 | S | E1,E4 · **[corroborates** `synthetic-personas-feedback` R1-4**]** |
| RR5 | **Faithfulness-fix validation.** Run the prepared prompt v6/answer v3 fixes (`conv-004` under-claim, `conv-005` contact over-attribution, `ground-026` buried price) through a live `make eval` with the regression gate green, since these sit on the judge's decision boundary. | R3,R5,O1,A3 | P1 | M | E3 · **[corroborates** ROADMAP P0-2 / `synthetic-personas-feedback` R0-1; **blocked: live-judge creds]** |
| RR6 | **Grow + harden judge calibration.** Expand the calibration sample beyond n=16 to deliberately span failures; state in EVALS.md that current κ is weak evidence; commit a `--judge llm` audit run beside the lexical one. | A3,A4,B1 | P1 | M | E3 · **[corroborates** `synthetic-personas-feedback` R0-4 / R0-2; **blocked: creds]** |
| RR7 | **Record the manual a11y walkthrough.** Perform and commit NVDA + VoiceOver + keyboard passes (focus on new turns, polite status, citations not read as brackets, reflow at 400%, target sizes), filling `audits/a11y-walkthrough.md`. | R2,A1 | P1 | M | E10 · **[corroborates** ROADMAP P2-5 / `synthetic-personas-feedback` R1a-1; **blocked: human]** |
| RR8 | **Title VI / LEP evidence one-pager.** Package the Spanish-parity numbers, language scope, and the no-determination rule as a compliance artifact an agency Title VI officer can attach to a four-factor LEP analysis. | O2,B1 | P1 | S | E7 · **[NET-NEW]** |
| RR9 | **Two-ways-to-qualify pattern.** A reusable answer template for "same discount, two paths" (Medicare card vs. 65+; free program vs. paid fallback), with edge cases per agency. | R3,R6,O1 | P2 | M | E4,E6 · **[corroborates** `synthetic-personas-feedback` R1-6**]** |
| RR10 | **NIST AI RMF crosswalk in the procurement brief.** Map the existing docs (guards, eval suites, audit, model card, data provenance) onto Govern/Map/Measure/Manage so a buyer recognizes the posture. ✅ Implemented 2026-07-17 (`docs/procurement-brief.md`) | B1,D2 | P2 | S | E13 · **[NET-NEW]** |

## Expansion backlog (new capability)

| ID | Item | Personas | Pri | Effort | Evidence & tag |
|---|---|---|---|---|---|
| RE1 | **Threshold-language expansion plan.** Add one CA threshold language at clearly-tagged non-parity (Tagalog already has a retrieval lexicon; Chinese/Vietnamese/Korean next), honest about cross-lingual retrieval over English docs, with a mirrored stretch suite. | R6,O2,D2 | P1 | L | E7,E8,E9 · **[corroborates** ROADMAP P3-3 / `synthetic-personas-feedback` R2-3**]** |
| RE2 | **Base-fare / visitor fast path.** A clean "how much is the bus" answer (adult base fare, transfer rule, payment options) without a discount detour, for occasional/tourist riders. | R7 | P2 | S | E11 · **[NET-NEW]** |
| RE3 | **Staff mode.** A denser, citation-first answer style behind a flag with an explicit graded "verify here" when confidence is low, for call-center and navigator use. | O1,O3 | P2 | M | E11 · **[corroborates** `synthetic-personas-feedback` R2-2**]** |
| RE4 | **Adversarial-faithfulness eval expansion.** Leading multi-turn questions that bait over/under-claiming, plus obfuscated-injection probes; this is the project's real risk frontier, and the conversation suite covers it in only six cases. | A2,A3,O1 | P2 | L | E2,E3 · **[corroborates** `synthetic-personas-feedback` R3-1**]** |
| RE5 | **Stamped, citable audit profile + HTTP adapter with auth.** A versioned audit profile a vendor can run against a hosted API and cite in an RFP, with a tamper-evident bundle. | D2,B1 | P2 | L | E11,E13 · **[NET-NEW]** |
| RE6 | **Cross-agency trip handling.** Retrieve and answer across two named agencies ("Monterey then Santa Barbara") without losing citations; retrieval half exists, synthesis is live-gated. | R7 | P3 | L | E11 · **[corroborates** `synthetic-personas-feedback` R3-2**]** |
| RE7 | **Fare-change changelog surfaced to riders/agencies.** Pair the existing `/version` pin and `diff_corpus` with a rider-readable "what changed since DATE," given how often fare policy churns. | B3,O2 | P3 | M | E5,E12 · **[corroborates** `synthetic-personas-feedback` R2-6 / ROADMAP P1-1**]** |

---

## Sequenced roadmap

The sequencing assumes `docs/ROADMAP.md` P0 (integrity) and the offline-doable items
in `synthetic-personas-feedback.md` are already done, which they are. This roadmap
slots the research-backed work into the gaps that remain.

- **Phase A — Trust and legal duty (P0, do first).** RR2 (liability framing) and RR4
  (positive handoff) are small and high-credibility; RR1 (close the loop) and RR3
  (means-based depth) are the two items that most change rider outcomes and map
  directly to the strongest external evidence (E4–E6). These are the difference
  between "explains a number" and "actually helps a rider get the fare."
- **Phase B — Correctness and compliance (P1).** RR5 + RR6 are the live-judge work that
  validates the prepared faithfulness fixes and hardens the measurement the whole
  thesis rests on (E3). RR7 is the human a11y gate every agency persona named (E10).
  RR8 turns the parity numbers into Title VI evidence (E7). RE1 starts the
  threshold-language expansion (E7–E9).
- **Phase C — Expansions (P2).** RR9 (two-ways-to-qualify), RR10 (NIST crosswalk), RE2
  (visitor fast path), RE3 (staff mode), RE4 (adversarial-faithfulness evals), RE5
  (audit profile for vendors).
- **Phase D — Larger bets (P3).** RE6 (cross-agency), RE7 (rider-readable fare-change
  log).

## Recommended first sprint

The highest-leverage start, weighted to evidence strength and the no-determination
thesis:

1. **RR2 — above-the-fold liability framing.** Cheapest, and it neutralizes the single
   biggest reputational risk (A4) while turning the audit from apparent weakness into
   the buyer's (B1) headline. Motivated by real liability precedent (E1, E2).
2. **RR1 — close the loop on the next physical step.** Touches every rider interview
   (R1, R3, R4) and is backed by the clearest external finding: take-up fails on
   process, not desire (E6). Lands with edge cases so "fixed" is a passing check.
3. **RR4 — positive verification handoff, tested.** Small, strengthens the hard rule,
   and is exactly the boundary the state program lead (B2) needs given the live Cal-ITP
   overlap (E4).
4. **RR3 — means-based / Cal-ITP path depth (begin).** The thinnest, churniest,
   highest-complexity corner of the corpus and the one California is actively
   standardizing (E4, E5). Start with MST/SBMTD edge cases.
5. **RR7 — schedule the manual a11y walkthrough.** It needs a human, so book it early;
   it is the gate before any "production-ready" claim (E10).

Bundle the afternoon-sized wins alongside: **RR8** (Title VI one-pager) and **RR10**
(NIST crosswalk), both pure documentation over evidence that already exists.

## Traceability matrix (persona → items)

| Persona | Remediations | Expansions |
|---|---|---|
| R1 Senior | RR1 | — |
| R2 Disabled/SR | RR7 | — |
| R3 Student | RR5, RR9 | — |
| R4 Low-income | RR1, RR3, RR4 | — |
| R5 Spanish/LEP | RR5 | RE1 |
| R6 LEP (Chinese) | RR9 | RE1 |
| R7 Visitor | — | RE2, RE6 |
| O1 CS rep | RR1, RR4, RR5, RR9 | RE3, RE4 |
| O2 Title VI officer | RR8 | RE1, RE7 |
| O3 Navigator | RR1 | RE3 |
| B1 Procurement | RR2, RR6, RR10 | RE5 |
| B2 Cal-ITP lead | RR3, RR4 | — |
| B3 Comms | RR2 | RE7 |
| A1 ADA coordinator | RR7 | — |
| A2 Eval/QA eng | — | RE4 |
| A3 Methodology researcher | RR5, RR6 | RE4 |
| A4 Journalist | RR2, RR6 | — |
| D1 Owner | (all) | (all) |
| D2 Vendor | RR10 | RE1, RE5 |
| D3 Fork engineer | — | RE5 |

## Validate with real users / risks

Each item earns its place only by becoming a checkable case or a real observation —
the same discipline `synthetic-personas-feedback.md` set:

- **Rider-facing items (RR1, RR3, RR9, RE2)** land with at least one new eval case
  carrying the corpus passage it depends on, so "fixed" is a passing check, not a
  vibe. Validate with ride-alongs: seniors (R1), a CalFresh recipient (R4), an LEP
  rider (R5/R6).
- **Faithfulness items (RR5, RR6, RE4)** sit on the LLM judge's decision boundary, so
  they are validated by a live `--judge llm` run and a grown calibration sample, not
  an offline pass (E3). **Risk:** without live-judge credentials these stay prepared,
  not proven; the ROADMAP records that a prior blind prompt edit regressed other cases.
- **Accessibility (RR7)** lands as a recorded walkthrough plus test assertions, not
  prose. **Risk:** no automation substitutes for a person (E10).
- **Compliance/procurement (RR8, RR10, RE5)** should be reviewed by a real agency Title
  VI officer and a real procurement reviewer before being claimed as fit for those
  audiences. **Risk:** a crosswalk that looks plausible can still miss a control a real
  reviewer requires.
- **Means-based depth (RR3)** carries the project's gravest risk: drifting toward
  eligibility determination. Validate that every new means-based answer still passes
  the no-determination and positive-handoff checks (E1, E4). Where a persona asked for
  a ruling or a saved profile, the recorded outcome is a clearer explanation or a
  stronger handoff, never a relaxed guard.

## Honest limits

This roadmap is built on a **synthetic** panel plus external evidence. The evidence
makes the *problems* credible — means-based complexity, take-up barriers, language-access
duty, chatbot liability, the multilingual quality gap, the need for a human a11y pass
are all real and cited. It does **not** establish that these particular riders or
buyers exist in numbers, or that fixing these items wins adoption. Several of the
highest-value items are gated on inputs this environment does not have: a live judge
(RR5, RR6, RE4), a person with a screen reader (RR7), an agency partner (RR8, RE1,
RE5). Treat the priorities as a well-reasoned starting hypothesis to test against real
discovery, not a commitment. The companion panel and its method are in
[`USER-RESEARCH.md`](USER-RESEARCH.md).
