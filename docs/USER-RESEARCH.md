# User Research — Synthetic Persona Panel & Simulated Interviews

> [!WARNING]
> **These personas and interviews are synthetic.** They were generated as a
> structured brainstorming device, not conducted with real people. No real rider,
> agency staffer, auditor, or buyer said any of this. The panel pressure-tests the
> assistant and its evaluation harness from many angles at once; it is **not**
> evidence of demand and does **not** substitute for real discovery. Treat every
> "quote" as a hypothesis to validate, not a finding. This is consistent with how
> the project labels its other synthetic artifacts (see
> [`research/synthetic-personas-feedback.md`](research/synthetic-personas-feedback.md)
> and the audit's synthetic-data discipline in
> [`audits/methodology.md`](audits/methodology.md)).
>
> **Last assembled: 2026-06-30.** The roadmap derived from this panel is
> [`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md). Where this panel overlaps the
> earlier `research/synthetic-personas-feedback.md`, it does not repeat that work;
> it adds an **external-evidence** layer the earlier doc lacked — the friction
> points below are cross-checked against real fare-policy, language-access, and
> chatbot-evaluation sources, cited in Method.

## Why do this at all

Role-playing the full cast around a fare-policy assistant surfaces gaps a single
author misses and forces the question "who is each feature *for*?" The synthesis
is tagged so it stays honest, not a wishlist:

- **[shipped]** — already exists in the current build.
- **[tracked]** — already named in [`docs/ROADMAP.md`](ROADMAP.md) or the prior
  `research/synthetic-personas-feedback.md` backlog.
- **[blocked]** — needs an external/human input (live judge credentials, a person
  with a screen reader, an agency partner, legal sign-off).
- **[new]** — genuinely surfaced here, often by the external research.

The hard rules in [`CLAUDE.md`](../CLAUDE.md) still bind every line below: no
eligibility determinations, no PII collection or query persistence in the deployed
demo, every answer cited, corpus dated. Several personas ask for things the
project will not build (a ruling, a saved profile, a "just tell me yes"). Those are
recorded as signal about rider need; the response is always a clearer explanation
or a stronger handoff, never a relaxed guard.

## How to read a persona

Each card: **who / goal**, then the simulated interview compressed to five lines —
*Values today (mapped to a real feature) · Gets stuck · Wants next · Adopts if /
Walks if.* "Values today" only ever names something that exists in the repo.

---

## Method

- **Sampling frame.** The real cast around a public fare-policy assistant: the
  riders the policy is about (senior, disabled/paratransit, low-income/means-based,
  youth/student, Spanish-first and other LEP, occasional/tourist); the agency staff
  who serve and own the policy (customer-service reps, navigators, fare-equity /
  Title VI officers); the people who buy, fund, and bound the risk (transit-tech
  procurement, a state program lead, a comms owner); the people who scrutinize it
  (accessibility specialist, eval/QA engineer, eval-methodology researcher,
  journalist/auditor); and the people who build or reuse it (owner/maintainer, a
  chatbot vendor, a fork-it engineer).
- **Protocol.** For each persona: a goal, a walkthrough of the surfaces or suites
  they would actually touch, what worked against the **current** build, where they
  stalled, and an open "what would make this a 10/10" prompt. Frictions become
  remediations; wishes become expansions, triaged in
  [`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md).
- **Research basis (why these frictions are plausible).** High-stakes friction
  points are grounded in real evidence, cross-checked against ≥2 reputable sources
  where the claim carries weight. Access date 2026-06-30.
  - *Means-based fare complexity is the live frontier.* California's
    [Cal-ITP Benefits](https://www.calitp.org/press/cal-itp-benefits-launch) verifies
    [CalFresh as a low-income proxy](https://docs.calitp.org/benefits/enrollment-pathways/low-income/)
    and [Medicare](https://docs.calitp.org/benefits/explanation/enrollment-pathways/medicare-cardholders/)
    for reduced fares; it became a permanent state service in 2025 with 50+ agencies
    onboarding contactless. Regional means-based programs add their own rules:
    [Clipper START](https://mtc.ca.gov/planning/transportation/access-equity-mobility/clipperr-startsm)
    (≤200% federal poverty level; launched at a
    [20% discount in 2020](https://www.bart.gov/news/articles/2020/news20200715),
    now 50%) and
    [LA Metro LIFE](https://www.metro.net/about/l-a-metros-low-income-fare-is-easy-life-program-hits-over-250000-enrollments/)
    (250k+ enrollments, income self-certification). MST and SBMTD in this corpus are
    live on Cal-ITP, so the boundary between "explain policy" and "verify eligibility"
    is real, not hypothetical.
  - *Reduced-fare take-up is gated by process, not desire.*
    [NADTC](https://www.nadtc.org/news/blog/understanding-half-farereduced-fare-requirements/)
    and [RPA](https://rpa.org/work/reports/reduced-fares) document in-person
    application, clinician documentation, and missing online paths as the barriers
    seniors and disabled riders hit — exactly the "last physical step" gap riders
    report below.
  - *Language access is a legal duty, not a nicety.* FTA
    [Title VI Circular 4702.1B](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf)
    and [DOT LEP guidance](https://www.transportation.gov/civil-rights/civil-rights-awareness-enforcement/dots-lep-guidance)
    require meaningful access for limited-English-proficient riders (safe harbor at
    5% or 1,000 persons). California's
    [threshold languages](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/APL%202025/Threshold-and-Concentration-Languages-for-All-Counties.pdf)
    are Spanish, Chinese, Tagalog, Vietnamese, Korean — the assistant covers two.
    LLM quality is uneven across languages: training corpora skew heavily English,
    and models score materially lower in low-resource languages
    ([survey](https://pmc.ncbi.nlm.nih.gov/articles/PMC11783891/),
    [MuBench](https://arxiv.org/pdf/2506.19468)), which is why the project measures
    Spanish parity rather than assuming it.
  - *Chatbot accuracy in this domain carries liability.* An airline was held liable
    for its chatbot's wrong fare advice
    ([CBC](https://www.cbc.ca/news/canada/british-columbia/air-canada-chatbot-lawsuit-1.7116416),
    [ABA](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/));
    NYC's MyCity bot gave businesses illegal advice and stayed live
    ([THE CITY](https://www.thecity.nyc/2024/04/02/malfunctioning-nyc-ai-chatbot-still-active-false-information/),
    [SHRM](https://www.shrm.org/topics-tools/employment-law-compliance/nyc-ai-chatbot-faulty-legal-advice)).
    RAG lowers but does not remove hallucination, and faithfulness is the metric
    that matters
    ([Patronus](https://www.patronus.ai/llm-testing/rag-evaluation-metrics),
    [K2view](https://www.k2view.com/blog/rag-hallucination/)).
  - *Accessibility for a chat surface needs a human pass.* Conversational a11y
    guidance is specific — polite live regions, `role=log`, focus discipline — and
    explicitly requires manual screen-reader testing on top of automation
    ([W3C ARIA23](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA23),
    [Orange chatbot guidelines](https://a11y-guidelines.orange.com/en/articles/chatbot/)).
- **Synthesis.** Frictions → **R**emediations; wishes → **E**xpansions, each carried
  to [`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md) with personas, priority, effort,
  and the evidence above. Effort scale: S ≈ an afternoon · M ≈ a day or two ·
  L ≈ a week+.

---

## Persona roster

| # | Persona | Group | Primary goal | Top friction |
|---|---|---|---|---|
| R1 | **Dolores, 71** — senior, Yolobus | Ride & Ask | The senior fare and which card to show | Gets the criterion, not the next physical step |
| R2 | **Tomás, 29** — wheelchair + intermittent screen reader, SBMTD | Ride & Ask | Use the page operably; know the paratransit fare | A11y is asserted by automation, not witnessed |
| R3 | **Aisha, 19** — low-income student, SacRT | Ride & Ask | Free or twenty dollars, and the catch | Answer buries the paid fallback behind the free program |
| R4 | **Luis, 47** — low-income worker, CalFresh, MST | Ride & Ask | Whether his CalFresh card gets a discount | Means-based / Cal-ITP path is thin in the corpus |
| R5 | **Rosa, 44** — Spanish-first / LEP, MST | Ride & Ask | The same answer an English speaker gets | Spanish multi-turn over-attributes a contact |
| R6 | **Wei, 58** — Chinese-monolingual, Medicare, SacRT | Ride & Ask | Medicare path vs age path, in his language | No Chinese; two-ways-to-qualify unclear translated |
| R7 | **Mark, 33** — visitor / occasional rider, MST + SBMTD | Ride & Ask | A fast fare answer on a trip, no program | Tools assume you know which discount to ask for |
| O1 | **Carla** — call-center agent, MST | Operate & Serve | A citable answer she can read aloud and trust | One wrong line and she stops trusting it |
| O2 | **Marisol** — fare-equity / Title VI + LEP officer | Operate & Serve | Defensible language access + no discrimination | No LEP/parity evidence packaged for compliance |
| O3 | **Ben** — county library navigator | Operate & Serve | Help a patron apply, side by side | Citations and copy still read engineer-flavored |
| B1 | **Devon** — transit-tech procurement officer | Buy & Comply | Evaluate the AI without evaluating the model | The strong audit story is buried below the fold |
| B2 | **Hector** — Cal-ITP / state program lead | Buy & Comply | The bot never enters the verification lane | Boundary is enforced but not stated positively |
| B3 | **Dana** — agency comms / marketing manager | Buy & Comply | Embed it without a viral wrong-fare screenshot | Liability of one bad number; needs a visible frame |
| A1 | **Priya** — ADA / accessibility coordinator | Assure & Audit | A real screen-reader pass, not a badge | Manual walkthrough is still pending |
| A2 | **Sam** — eval/QA engineer running the harness | Assure & Audit | Wire evals into CI; keep them boring and stable | Run-to-run band; a couple of judge-boundary cases |
| A3 | **Dr. Okonkwo** — eval-methodology researcher | Assure & Audit | Trust the measurement; defend κ and parity | κ rests on n=16, pass-skewed; lexical audit floor |
| A4 | **Nadia** — journalist / watchdog | Assure & Audit | Independently assess and publish a checkable claim | The unframed 0.04 line is a headline waiting to happen |
| D1 | **Chelsea** — owner / maintainer | Build | A credible, honest, reusable artifact | Several wins are live-judge- or human-gated |
| D2 | **Renaud** — vendor selling transit chatbots | Build | Attach a neutral audit to an RFP response | No stamped, citable audit profile to point a buyer at |
| D3 | **Aki** — eval engineer at another civic team | Build | Fork the harness for a housing-voucher assistant | Wants the transit seam drawn cleanly |

20 personas across 5 groups.

---

## Group 1 — Ride & Ask (the riders the policy is about)

### R1 — Dolores, 71, senior on Yolobus
- **Goal:** the senior fare and where to get the card, in big print.
- **Values today:** a clear number with a real citation, and the
  [reader text-size A / A+ / A++ and high-contrast controls](../web/index.html)
  shipped for older eyes; the Sources list resolves to the actual agency page, not
  a `[doc:id]`.
- **Gets stuck:** the answer states "62 and older" but rarely closes the loop on the
  *next physical step* — where to apply for the reduced-fare ID, the fee, the hours —
  even though `yolobus-reduced-fare-id` is in the corpus. This is the exact take-up
  barrier the [NADTC](https://www.nadtc.org/news/blog/understanding-half-farereduced-fare-requirements/)
  and [RPA](https://rpa.org/work/reports/reduced-fares) research names.
- **Wants next:** the criterion *plus* the application location, cost, and hours, in
  one answer; a one-tap larger default.
- **Adopts if:** it tells her where to go next. **Walks if:** it leaves her to work
  out that she qualifies and where to take her photo.

### R2 — Tomás, 29, wheelchair user, intermittent screen reader, SBMTD
- **Goal:** use the page operably and learn the paratransit/disability fare.
- **Values today:** the [WCAG 2.2 AA structural gate](../web/a11y.py) (heading order,
  labeled controls, 24px targets, zoom not disabled) and the polite `aria-live`
  region with focus moved to each new answer turn (`web/index.html`).
- **Gets stuck:** structure passes, but nobody has *listened* to it. The manual
  screen-reader walkthrough is still [pending](audits/a11y-walkthrough.md); the
  citation block and the "as of" line risk reading as a wall. Conversational a11y
  guidance ([W3C](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA23),
  [Orange](https://a11y-guidelines.orange.com/en/articles/chatbot/)) is explicit that
  automation is not the sign-off.
- **Wants next:** a recorded NVDA/VoiceOver pass, reflow at 400% evidence, and an
  accessible reading treatment for citations.
- **Adopts if:** a person confirms it is operable. **Walks if:** it green-lights a
  page that is fine structurally but unusable by voice.

### R3 — Aisha, 19, low-income student, SacRT
- **Goal:** is it free for her or twenty dollars, and what is the catch.
- **Values today:** every answer is cited and dated; the
  [output guard](../src/assistant/guards.py) keeps it from inventing an enrollment rule.
- **Gets stuck:** this is the documented `ground-026` miss — the answer leads with
  "RydeFreeRT, it's free" and drops the $20 monthly pass she asked about, so she never
  learns the paid fallback or the enrollment condition that separates them.
- **Wants next:** both the free program *and* the paid fare, with the one condition
  that decides which applies.
- **Adopts if:** it states both paths plainly. **Walks if:** it hides the answer she
  asked for behind the cheaper one.

### R4 — Luis, 47, low-income worker, CalFresh, MST
- **Goal:** whether his CalFresh card gets him a discount, and how.
- **Values today:** the corpus already overlaps the real benefits domain — MST's
  `mst-fares-benefits` page describes the [Cal-ITP](https://docs.calitp.org/benefits/enrollment-pathways/low-income/)
  contactless path, and the assistant cites it.
- **Gets stuck:** means-based eligibility is the thinnest, churniest part of the
  corpus. The low-income pathway (CalFresh proxy, Login.gov, a contactless card) and
  regional programs like
  [Clipper START](https://mtc.ca.gov/planning/transportation/access-equity-mobility/clipperr-startsm)
  or [LA Metro LIFE](https://www.metro.net/about/l-a-metros-low-income-fare-is-easy-life-program-hits-over-250000-enrollments/)
  involve income thresholds, self-certification, and steps the assistant can describe
  but should never adjudicate. Today it can under-serve "am I low-income enough?"
  questions.
- **Wants next:** a clear "here is the published income criterion and where
  verification happens (Cal-ITP / the agency), and it is their decision, not mine."
- **Adopts if:** it explains the means-based path and hands off cleanly. **Walks if:**
  it gets vague and tells him to call without the criterion.

### R5 — Rosa, 44, Spanish-first / LEP, MST
- **Goal:** the same answer in Spanish that an English speaker gets.
- **Values today:** real EN/ES answers (not machine sludge) over MST's Spanish fares
  page, with the [Spanish-parity table in EVALS.md](../EVALS.md) making the gap a
  measured number rather than a hope.
- **Gets stuck:** the documented `conv-005` failure — a Spanish multi-turn answer
  attached an in-person location to the veteran courtesy-card path that the passages
  only support for a different program. Contact details over-attribute, and it is
  worse in Spanish multi-turn. This matches the
  [known multilingual LLM quality gap](https://pmc.ncbi.nlm.nih.gov/articles/PMC11783891/).
- **Wants next:** Spanish answers held to the same faithfulness as English, with
  contacts attributed only to the program a passage ties them to.
- **Adopts if:** Spanish parity is real on multi-turn, not just single-shot.
  **Walks if:** the careful English answer is the only careful one.

### R6 — Wei, 58, Chinese-monolingual, Medicare cardholder, SacRT
- **Goal:** Medicare path vs. age path for the senior discount, in his language.
- **Values today:** nothing he can read yet — and that is the honest finding. Only
  EN/ES exist.
- **Gets stuck:** there is no Chinese, though Chinese is one of California's
  [top-five threshold languages](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/APL%202025/Threshold-and-Concentration-Languages-for-All-Counties.pdf)
  and FTA [Title VI / LEP](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf)
  expects meaningful access. Even translated, "two ways to qualify for the same
  discount" (Medicare card vs. 65+) is not laid out as a simple either/or.
- **Wants next:** at least one more threshold language at clearly-tagged non-parity,
  honest about cross-lingual retrieval limits; a reusable two-ways-to-qualify pattern.
- **Adopts if:** his language is served, even imperfectly and labeled so. **Walks if:**
  "multilingual" means English-plus-Spanish only.

### R7 — Mark, 33, visiting Monterey then Santa Barbara, occasional rider
- **Goal:** a fast, correct fare answer on a trip; he has no program and does not
  know the discount names.
- **Values today:** the [`/offline` printable per-agency fare page](../web/offline.py)
  (no model call, no signal needed) and the demo's plain "what it will not do" frame
  so he is not misled.
- **Gets stuck:** the surface assumes you know which discount to ask for; a visitor
  asks "how much is the bus" and wants the base adult fare, transfer rule, and payment
  options without a discount detour. Cross-agency trips ("Monterey then Santa Barbara")
  have no first-class handling.
- **Wants next:** a clean base-fare answer and a payment/transfer summary; eventually
  a cross-agency comparison.
- **Adopts if:** it answers the simple question simply. **Walks if:** it over-explains
  programs he cannot use.

---

## Group 2 — Operate & Serve (agency staff who serve and own the policy)

### O1 — Carla, call-center agent, MST
- **Goal:** a fast, citable answer she can read aloud and trust on a recorded call.
- **Values today:** citation-on-every-answer, the
  [graded retrieval-confidence signal](../src/assistant/answer.py) (low/medium/high)
  on the API payload, and the dated "as of" line she can repeat to a caller.
- **Gets stuck:** the documented `conv-004` false negative — the bot said veteran
  documents were "not specified" when the page lists DD-214 and the rest — is exactly
  the kind of miss that burns her, because her name is on the call. She wants the
  uncertainty to be *loud* when it is real.
- **Wants next:** a denser, citation-first staff mode and an explicit "I am not
  certain, verify here" when confidence is low, distinct from a hard refusal.
- **Adopts if:** every line is trustworthy and it flags its own doubt. **Walks if:**
  it over- or under-claims once.

### O2 — Marisol, fare-equity / Title VI + LEP officer
- **Goal:** defensible language access and no whiff of discrimination, for the
  agency's three-year Title VI program.
- **Values today:** the [Spanish-parity table](../EVALS.md), the EN/ES output guards,
  and the dated, public corpus — evidence she can attach to a
  [four-factor LEP analysis](https://www.transportation.gov/civil-rights/civil-rights-awareness-enforcement/dots-lep-guidance).
- **Gets stuck:** parity is measured but not *packaged* for compliance, and only two
  of California's five
  [threshold languages](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/APL%202025/Threshold-and-Concentration-Languages-for-All-Counties.pdf)
  are covered. She also needs the no-determination guarantee written in civil-rights
  language, not just engineering language.
- **Wants next:** a Title VI / LEP evidence one-pager (parity numbers, language scope,
  the no-determination rule) and a roadmap to the remaining threshold languages.
- **Adopts if:** it produces audit-ready language-access evidence. **Walks if:**
  "bilingual" cannot be defended in a Title VI review.

### O3 — Ben, county library navigator (helps patrons apply for CalFresh + reduced fares)
- **Goal:** sit beside a patron and read the screen together, calmly and correctly.
- **Values today:** the readable Sources list (agency, title, resolvable URL — the UI
  already strips inline `[doc:id]`), and the calm, no-hype copy.
- **Gets stuck:** answers sometimes still read engineer-flavored, and the
  reduced-fare-plus-CalFresh overlap (the real
  [enrollment-barrier](https://rpa.org/work/reports/reduced-fares) terrain he works in)
  is under-explained.
- **Wants next:** plain-language default copy, clickable agency links he can hand to a
  patron, and the "where and how to apply" step joined to the fare answer.
- **Adopts if:** he can use it shoulder-to-shoulder with a patron. **Walks if:** it
  needs an engineer to interpret.

---

## Group 3 — Buy & Comply (decide, fund, bound the risk)

### B1 — Devon, transit-tech procurement officer
- **Goal:** evaluate the AI without being able to evaluate the model itself.
- **Values today:** the [procurement brief](procurement-brief.md), the
  [model card](model-card.md), the two-layer eval story (white-box harness +
  independent [GovChat-Eval audit](audits/methodology.md)), and the refusal/data
  provenance posture — the
  [Govern/Map/Measure/Manage](https://www.nist.gov/itl/ai-risk-management-framework)
  evidence a NIST-aligned buyer asks for.
- **Gets stuck:** the independent-audit story is the thing he would point his director
  to, and it sits below the fold; the two failing GovChat lines (groundedness 0.04,
  multilingual 0.667) need a procurement-readable explainer so a skeptic does not
  misread them.
- **Wants next:** the audit story above the fold; a short NIST-AI-RMF crosswalk;
  boilerplate "what to require of an AI vendor" language.
- **Adopts if:** it makes a bid evaluable on a test report and a refusal policy.
  **Walks if:** the audit reads as a weakness because it is unframed.

### B2 — Hector, Cal-ITP / state benefits program lead
- **Goal:** the assistant explains policy and never drifts into the verification lane
  that [Benefits](https://www.calitp.org/press/cal-itp-benefits-launch) owns.
- **Values today:** the ruthless no-determination guard, enforced in EN/ES code and
  twice in the suites; the corpus overlaps the real Cal-ITP domain (MST, SBMTD).
- **Gets stuck:** the boundary is enforced but stated *negatively*. The moment a rider
  believes the bot verified them, the public-trust model Cal-ITP runs breaks, and the
  positive handoff ("verification happens at Benefits / the agency; here is how to
  start") is inconsistent across answers.
- **Wants next:** an explicit, tested "this is policy explanation; verification is at
  Cal-ITP / the agency" framing on every eligibility-adjacent answer.
- **Adopts if:** the handoff to verification is as reliable as the refusal. **Walks
  if:** a rider could leave thinking the bot approved them.

### B3 — Dana, agency comms / marketing manager
- **Goal:** embed the assistant on the agency fare page without a viral wrong-fare
  screenshot.
- **Values today:** the [`/embed` iframe widget](../web/embed.py) that carries the
  reference-implementation notice and the will-not-do line, the
  [`/version` corpus pin](../corpus/CHANGELOG.md) so she can lock a date she approved,
  and the "fetched N days ago" staleness note.
- **Gets stuck:** her real fear is liability for one wrong number. An airline was held
  liable for its chatbot's fare advice
  ([CBC](https://www.cbc.ca/news/canada/british-columbia/air-canada-chatbot-lawsuit-1.7116416));
  NYC's bot stayed live while giving illegal advice
  ([THE CITY](https://www.thecity.nyc/2024/04/02/malfunctioning-nyc-ai-chatbot-still-active-false-information/)).
  She wants the "confirm with the agency" frame and the staleness indicator to be
  unmissable inside the embed.
- **Wants next:** a louder confirm-with-agency frame, optional agency theming, and the
  candid failure cases in the report (candor is what sells her).
- **Adopts if:** the disclaimers travel with the embed and the corpus is pinnable.
  **Walks if:** one bad screenshot could go viral unframed.

---

## Group 4 — Assure & Audit (independent scrutiny)

### A1 — Priya, ADA / accessibility coordinator
- **Goal:** a real screen-reader and keyboard pass before any agency demo.
- **Values today:** the automated structural gate and the committed
  [walkthrough checklist](audits/a11y-walkthrough.md) ready for a human, plus the
  text-size/contrast controls.
- **Gets stuck:** automation is table stakes; the result log is still `pending`, and
  per conversational-a11y guidance the
  [manual pass](https://a11y-guidelines.orange.com/en/articles/chatbot/) is the sign-off
  that does not exist yet (focus order on open/close, polite announcement of streamed
  status, citations that do not read as brackets).
- **Wants next:** a recorded NVDA + VoiceOver pass, reflow-at-400% and target-size
  evidence, committed as an artifact.
- **Adopts if:** a person signs the walkthrough. **Walks if:** the README implies a
  sign-off the log does not show.

### A2 — Sam, eval/QA engineer running the harness
- **Goal:** wire the evals into the same pipeline as unit tests; keep them fast,
  deterministic, and boring.
- **Values today:** [118 cases across six suites](../EVALS.md), deterministic checks
  separate from the LLM judge, the 25-case CI smoke suite, the regression gate that
  trips on a 2-point drop, and `--offline` runs with no key.
- **Gets stuck:** the headline is a band (~113/118) because a few cases sit on the
  judge's decision boundary and the answer model is not perfectly deterministic on
  Bedrock; he wants the variance legible and the live judge gated in CI without
  flaking the build.
- **Wants next:** a calibrated LLM judge gated in CI behind credentials, per-suite
  variance surfaced, and the GovChat audit job promoted from advisory to gating once
  the lexical floor is understood.
- **Adopts if:** it is as reliable as pytest. **Walks if:** a non-deterministic case
  reds the build for no real regression.

### A3 — Dr. Okonkwo, eval-methodology researcher
- **Goal:** trust the measurement and defend it to skeptical reviewers.
- **Values today:** judge model ≠ answer model, versioned judge prompts, unparseable
  judge output counted as error not pass, the
  [κ + per-run cost in EVALS.md](../EVALS.md), and the honest-limits framing in the
  model card.
- **Gets stuck:** κ rests on n=16 and is pass-skewed; the committed GovChat audit uses
  a lexical judge that floors groundedness near zero. Both are real limits, and the
  literature backs his worry: LLM-judges carry length and self-preference
  [biases](https://www.patronus.ai/llm-testing/rag-evaluation-metrics) and need
  human-verified calibration, while lexical proxies cannot tell paraphrase from
  fabrication.
- **Wants next:** a grown calibration sample that deliberately spans failures, a
  committed `--judge llm` audit run beside the lexical one, and bootstrapped CIs per
  suite.
- **Adopts if:** the numbers survive peer scrutiny. **Walks if:** they look rigorous
  but are lexical underneath.

### A4 — Nadia, journalist / watchdog
- **Goal:** independently assess the deployed bot and publish a checkable claim.
- **Values today:** the candid "representative failures with full traces" in the
  report, the reproducible committed audit, and the project's stated thesis of showing
  its own misses.
- **Gets stuck:** "Civic AI tool's own audit scores 0.04 on groundedness" is a
  headline one screenshot away. She knows from the footnote it is the lexical judge,
  but the explanation has to lead, not trail — the
  [NYC MyCity](https://www.thecity.nyc/2024/04/02/malfunctioning-nyc-ai-chatbot-still-active-false-information/)
  pattern is exactly what she would compare it to.
- **Wants next:** above-the-fold framing of the lexical-judge floor and a committed
  real-judge run so the true signal sits next to the proxy.
- **Adopts if:** she can publish an independently verifiable claim. **Walks if:** the
  unframed number invites a dishonesty read.

---

## Group 5 — Build (build it or reuse it)

### D1 — Chelsea, owner / maintainer
- **Goal:** a credible, honest, reusable artifact whose thesis is "here is how I test
  it."
- **Values today:** the whole pipeline — dated corpus, guarded answer path, six-suite
  harness with a separate judge, the independent audit, the
  [`DomainProfile`](../src/assistant/domain.py) seam, CI, and the ADR trail.
- **Gets stuck:** the highest-value remaining wins are gated — faithfulness fixes need
  a live judge (`make eval`), the a11y sign-off needs a person, and the means-based /
  multilingual breadth needs new corpus and cases. Offline passes prove no plumbing
  breakage but cannot validate the judge-boundary cases.
- **Wants next:** the live-judge run that confirms the prepared prompt v6/v3 fixes; the
  recorded a11y walkthrough; the research-backed roadmap that says what to build next
  and why.
- **Adopts if:** each claim stays falsifiable against the repo. **Walks if:** a doc
  ever overstates what the harness records.

### D2 — Renaud, vendor selling transit chatbots
- **Goal:** attach a neutral, reproducible audit to an RFP response to win bids.
- **Values today:** the model-neutral adapter (Bedrock or Anthropic), the
  reproducible record-then-replay audit, and the procurement brief a buyer can read
  without the code.
- **Gets stuck:** he wants a *versioned, stamped* audit profile he can cite, run
  against his own hosted API, and that procurement will recognize — exactly the gap
  the [transit-chatbot market](https://publicsector.google/ai/chicago-transit-authority-launches-a-multi-lingual-chatbot-for-more-a-more-seamless-commute/)
  has, where deployed bots (e.g., the
  [buggy MTA OMNY assistant](https://www.thecityreporter.nyc/2025/05/28/mta-omny-ai-chat-support-customer-complaints/))
  ship without a public accuracy claim.
- **Wants next:** an HTTP adapter with auth, a stamped audit profile, and a
  NIST-AI-RMF crosswalk a buyer trusts.
- **Adopts if:** the audit differentiates his bid. **Walks if:** procurement does not
  recognize the output.

### D3 — Aki, eval engineer at another civic team (housing vouchers)
- **Goal:** fork the harness for a housing-voucher assistant in an afternoon.
- **Values today:** the [`adapting.md`](adapting.md) guide and the `DomainProfile`
  that isolates scopes, aliases, redirect topics, and fallback contact, with the
  PII/injection/determination detectors kept out of the profile as cross-domain
  safety.
- **Gets stuck:** he can see the runner, judges, deterministic checks, and YAML format
  are reusable, but wants the transit seam (doc-id checks, agency filter) drawn even
  more explicitly, and a scaffold so a new domain starts from the audited skeleton.
- **Wants next:** a "new domain" scaffold command and a worked housing example beyond
  the existing test.
- **Adopts if:** a fork reuses the gates without editing the pipeline. **Walks if:**
  transit assumptions leak into his domain.

---

## Cross-cutting themes (what the cast agrees on)

1. **The last step is missing, and that is the rider frontier.** Seniors (R1),
   low-income (R4), students (R3), and LEP riders (R6) all get a criterion but not the
   *action* — where to apply, the cost, which of two paths applies, where verification
   happens. The corpus often holds this; retrieval and prompting do not reliably join
   "what is the fare" to "how do I get the card." External research says this is the
   real-world barrier to take-up, not desire
   ([NADTC](https://www.nadtc.org/news/blog/understanding-half-farereduced-fare-requirements/),
   [RPA](https://rpa.org/work/reports/reduced-fares)).
2. **Means-based eligibility is the high-value, high-churn gap.** The corpus is strong
   on categorical discounts (age, disability, veteran) and thin on the income-based
   path that California is actively standardizing through
   [Cal-ITP](https://docs.calitp.org/benefits/enrollment-pathways/low-income/) and
   regional programs ([Clipper START](https://mtc.ca.gov/planning/transportation/access-equity-mobility/clipperr-startsm),
   [LA Metro LIFE](https://www.metro.net/about/l-a-metros-low-income-fare-is-easy-life-program-hits-over-250000-enrollments/)).
   This is where eligibility is most complex and where the no-determination boundary
   matters most (R4, B2).
3. **Faithfulness drift beats guard bypass.** Riders (R3, R5), staff (O1), and the
   methodology researcher (A3) hit the same class of problem: the guards hold, but the
   *generated answer* over-claims a contact or under-claims a documented fact
   (`conv-004`, `conv-005`, `ground-026`). RAG lowers but does not remove this
   ([Patronus](https://www.patronus.ai/llm-testing/rag-evaluation-metrics),
   [K2view](https://www.k2view.com/blog/rag-hallucination/)); it is a generation-and-eval
   problem, not a guard problem.
4. **Language access is a legal duty the build half-meets.** Spanish parity is real and
   measured (R5, O2), but three of California's five
   [threshold languages](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/APL%202025/Threshold-and-Concentration-Languages-for-All-Counties.pdf)
   are unserved (R6), and FTA
   [Title VI / LEP](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf)
   frames this as meaningful-access compliance, not a feature.
5. **Accessibility is asserted, not witnessed.** Automation passes; the lived
   screen-reader walkthrough does not exist (R2, A1). For a phone-first civic audience
   this is a credibility gate, and chatbot a11y guidance is explicit that the manual
   pass is required ([W3C](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA23)).
6. **The audit story is a strength that currently reads as a weakness.** Buyers (B1)
   and the comms owner (B3) want exactly the independent second layer, but the unframed
   0.04 lexical-judge line is a journalist's (A4) gift. The fix is framing and a
   committed real-judge run, not new engineering — and the stakes are real, given the
   [Air Canada](https://www.cbc.ca/news/canada/british-columbia/air-canada-chatbot-lawsuit-1.7116416)
   and [NYC MyCity](https://www.thecity.nyc/2024/04/02/malfunctioning-nyc-ai-chatbot-still-active-false-information/)
   liability precedents.

## Honest limits of this exercise

This is simulated. It can generate plausible needs and obvious gaps, but it cannot
tell you *which* are real, how many riders or buyers exist, or what they would pay. It
over-represents the author's mental model and misses what only real users surprise you
with. The external sources make the *frictions* more credible than pure invention, but
they cannot establish demand. Do not prioritize a roadmap off this alone. Use it to
design the questions for, and lower the cost of, real discovery: ride-alongs with
seniors and LEP riders, a session with an agency Title VI officer, a procurement
read-through with a real reviewer, and the human screen-reader walkthrough that
[`audits/a11y-walkthrough.md`](audits/a11y-walkthrough.md) is waiting for.

The triaged backlog, with priority, effort, evidence, and traceability, is in
[`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md). It complements
[`docs/ROADMAP.md`](ROADMAP.md) (productionalization) and the earlier
[`research/synthetic-personas-feedback.md`](research/synthetic-personas-feedback.md)
backlog; it does not replace either.
