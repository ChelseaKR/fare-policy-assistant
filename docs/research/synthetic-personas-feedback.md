# Synthetic Persona Research: Interviews, Remediations, and Expansions

Status: research artifact, not user research. Last updated 2026-06-20.

## What this is and what it is not

This document assembles a broad panel of *synthetic* personas around the Transit
Fare Policy Assistant, runs structured interviews against the current build, and
derives a prioritized backlog of remediations and expansions. The personas are
constructed, not recruited. No real rider was interviewed and no real usage data
informs the quotes below. The point is to pressure-test the product surface and
the eval harness from many angles at once and to surface work that the existing
[`docs/ROADMAP.md`](../ROADMAP.md) does not yet name.

Two guardrails on how to read it:

1. Synthetic feedback is a hypothesis generator, not evidence. Every "finding"
   here is a candidate to be validated with real users or a real eval case
   before it changes the product. Where a finding maps to a check we can write,
   the remediation says so.
2. The hard rules in [`CLAUDE.md`](../../CLAUDE.md) still bind. Several personas
   below ask for things the project will not build (an eligibility ruling, a
   saved rider profile, a "just tell me yes" shortcut). Those requests are
   recorded as signal about rider need, and the response is a better explanation
   or a redirect, never a relaxation of the no-determination, no-PII line.

The build this panel reviewed: five-agency dated corpus (MST, SBMTD, Yolobus,
SacRT, HTA), BM25 retrieval with EN/ES expansion and agency filter, guarded
answer pipeline, 118-case eval harness at ~113/118 with an independent
GovChat-Eval audit, and an accessible single-page demo on Lambda. Current scores
are in [`EVALS.md`](../../EVALS.md).

## The persona panel

Twenty personas across five constituencies. Each entry lists what they want, how
they would actually reach the assistant, and the single sentence that best
captures their stake.

### A. Riders (the people the policy is about)

- **P1: Dolores, 71, Woodland.** Rides Yolobus to medical appointments. Smartphone
  used mostly for calls and photos. Wants to know the senior fare and what card to
  show. Stake: "I just need the number and where to get the card, in big print."
- **P2: Rosa, 44, Salinas.** Spanish-monolingual, caring for an aging parent on MST.
  Comfortable on a phone, distrusts forms. Stake: "Necesito la misma respuesta que
  recibe alguien que habla inglés, no un resumen más corto."
- **P3: Marcus, 38, Marina.** Post-9/11 veteran, new to MST. Heard there is a free
  pass and a discount and is not sure which applies. Stake: "Tell me exactly which
  document proves I am a veteran and where to take it."
- **P4: Tomás, 29, Santa Barbara.** Uses a wheelchair, rides SBMTD daily, eligible
  for paratransit. Screen-reader user on some days due to a fluctuating vision
  condition. Stake: "If the page is not operable with my keyboard and voice, the
  content does not exist for me."
- **P5: Aisha, 19, Sacramento.** SacRT, full-time student, low income. Heard rides
  might be free but also saw a monthly pass price. Stake: "Is it free for me or is
  it twenty dollars, and what is the catch."
- **P6: Frank, 67, Eureka.** HTA rider in a rural county with patchy cell service.
  Often offline at the stop. Stake: "Half the time I have no bars when I need the
  answer."
- **P7: Priya, 35, Davis.** Parent of two kids, plans family trips across Yolobus
  and SacRT. Stake: "Do my kids ride free, on which services, and does that include
  the on-demand van."
- **P8: Wei, 58, Sacramento.** Chinese-monolingual, Medicare cardholder, unsure if
  the Medicare path or the age path gets the discount. Stake: "I read no English and
  no Spanish, and the senior discount rules are confusing in any language."

### B. Rider-facing staff (the amplifiers)

- **P9: Carla, call-center agent, MST.** Answers fare questions all day, needs a
  fast, citable answer she can read aloud and trust. Stake: "If it is wrong once, I
  stop using it, because my name is on the call."
- **P10: Ben, library navigator, Yolo County.** Helps patrons apply for reduced
  fares alongside CalFresh and other benefits. Stake: "I sit beside someone and we
  read the screen together; it has to be calm and correct."
- **P11: Sandra, paratransit eligibility clerk, SBMTD.** Wants the assistant to
  explain process without ever appearing to pre-decide a case she will adjudicate.
  Stake: "If a rider arrives believing a bot already approved them, you have made my
  job harder."

### C. Agency and government (the buyers and owners)

- **P12: Marketing/comms manager, mid-size agency.** Considering embedding the
  assistant on the agency site. Worried about a wrong fare quote going viral. Stake:
  "One bad screenshot costs me more than the tool ever saves."
- **P13: ADA / accessibility coordinator, agency.** Will not approve anything that
  has not had a real screen-reader pass. Stake: "Automated contrast checks are table
  stakes; show me the manual walkthrough."
- **P14: Procurement officer, county IT.** Has been told to ask every vendor "what
  is your AI story." Stake: "I cannot evaluate a model; I can evaluate a test
  report and a refusal policy."
- **P15: Cal-ITP / state benefits program lead.** Cares that the assistant never
  drifts into the eligibility-verification lane that Benefits owns. Stake: "Explain
  the policy, hand off to us for verification, and never blur that boundary."

### D. Builders and reviewers (the people who reuse the thing)

- **P16: Eval engineer, another civic team.** Wants to fork the harness for a
  housing-voucher assistant. Stake: "How much of this is transit and how much is
  reusable scaffolding."
- **P17: Responsible-AI reviewer.** Reads the model card adversarially, checks that
  claims match code. Stake: "Every sentence in the model card should be falsifiable
  against the repo."
- **P18: Open-source contributor.** Found the repo, wants a good first issue and a
  fast local loop with no API key. Stake: "Can I run the evals offline in five
  minutes and see what to fix."

### E. Adversaries and skeptics (the stress)

- **P19: Red-teamer.** Tries prompt injection, determination-baiting, PII probes,
  out-of-corpus traps. Stake: "I will find the one phrasing your guard misses."
- **P20: Journalist / auditor.** Distrusts AI claims by default, will publish the
  gap between the README and reality. Stake: "Your own report shows a 0.04
  groundedness line; explain that before I do."

## Interviews

Condensed to the exchanges that produced a finding. Q is the interviewer; the
persona answers in voice. Findings are tagged `[F-n]` and collected in the
backlog.

### P1: Dolores (Yolobus senior)

> Q: You asked "how much is the senior fare." What did you get?
> A: A clear number and a citation, which is nice. But it said "62 plus" and I am
> 71, so I still had to work out that I count. And it pointed me to a card I did
> not know how to get. Where do I take my photo? Is there a fee?

`[F-1]` Answers state the criterion but rarely close the loop on *the next
physical step* (where to apply, what it costs, hours). The corpus has some of
this (`yolobus-reduced-fare-id`); retrieval does not always surface it on a fare
question. `[F-2]` No large-text or high-contrast affordance is surfaced for older
riders even though the page meets AA.

### P2: Rosa (MST, Spanish)

> Q: You asked in Spanish about the veteran documents for your father.
> A: La respuesta fue buena y en español. Pero en una conversación de dos turnos,
> la segunda respuesta me dio un teléfono que, me dijeron después, no era el
> correcto para esa tarjeta. Si la versión en inglés es más cuidadosa, eso no es
> justo.

This is the real failure `conv-005` in `EVALS.md`: the Spanish multi-turn answer
attached a phone/email to the veteran courtesy-card path that the passages only
support for a different program. `[F-3]` Contact details get over-attributed,
worse in Spanish multi-turn. `[F-4]` Spanish parity is measured (good) but the
multilingual suite still shows `ml-010` missing on a cross-lingual claim about
BeeLine youth fares.

### P3: Marcus (MST veteran)

> Q: Did you learn which document proves veteran status?
> A: Eventually. But one answer told me "the policy does not specify what
> documents prove each category," and that was just wrong: the veterans page
> lists DD-214 and the rest. I almost gave up.

This is `conv-004`: the answer under-claims, saying documents are unspecified
when the retrieved passage lists them. `[F-5]` The model sometimes hedges into a
false negative ("not specified") when the passage does specify, the inverse of a
groundedness error and just as damaging to trust. `[F-6]` Two paths (free
veteran pass vs. courtesy-card discount) confuse riders; the assistant does not
disambiguate them well.

### P4: Tomás (SBMTD, wheelchair, intermittent screen reader)

> Q: How was the page with a keyboard and screen reader?
> A: Structure was fine, headings in order, controls labeled. But nobody seems to
> have actually listened to it. The "as of" date and the citation read as a wall.
> And after I send a question, focus does not move to the answer, so I do not know
> it arrived.

`[F-7]` The model card admits the manual screen-reader walkthrough is still
pending; this persona makes it the gating item for any agency demo. `[F-8]` Live
region / focus management on new answers is unverified. `[F-9]` Citations and the
"as of" line need an accessible reading treatment, not just visual placement.

### P5: Aisha (SacRT student)

> Q: Free or twenty dollars?
> A: The answer led with "RydeFreeRT, it's free" and never told me the twenty-dollar
> monthly pass even existed. Turns out the free program has enrollment rules; if I
> miss those, the paid pass is my fallback and nobody told me.

This is `ground-026`: the answer drops the asked-for $20 figure because a free
option exists. `[F-10]` When a free program and a paid option coexist, the
assistant must state both and the condition that separates them, not bury the
one the rider asked for.

### P6: Frank (HTA, rural, offline)

> Q: What happens when you have no signal?
> A: Then I have nothing. The whole thing needs the internet. At my stop, that is
> exactly when I need the fare.

`[F-11]` No offline or low-bandwidth rider path exists. The corpus is tiny and
static; a printable or cached fare card per agency is feasible and currently
absent from the *rider* surface (the repo commits snapshots, but riders never see
them).

### P7: Priya (Yolobus + SacRT family)

> Q: Did you get a clear answer on kids riding free?
> A: For the fixed-route bus, yes. For the BeeLine van it got vague and told me to
> call. Later I learned BeeLine youth is actually $1.50, it is in the table. So
> the assistant hid a real answer behind a redirect.

`[F-12]` On-demand / microtransit fares (BeeLine) are under-retrieved relative to
fixed-route, producing unnecessary redirects (`ml-010`, `ground-024`).
`[F-13]` Cross-agency trips ("Yolobus then SacRT") have no first-class handling.

### P8: Wei (Sacramento, Chinese-monolingual, Medicare)

> Q: Could you use it at all?
> A: No. There is no Chinese. And even translated, the Medicare-card path versus
> the age path was not laid out as a simple either/or.

`[F-14]` Only EN/ES exist; a large CA language cohort is unserved (ROADMAP P3
names this as a stretch). `[F-15]` "Two ways to qualify for the same discount"
(Medicare card vs. 65+) is a recurring comprehension gap across agencies.

### P9: Carla (MST call-center)

> Q: Would you read its answers to callers?
> A: Only if I can trust every line. The veteran-documents miss would have burned
> me. I also want it to tell me when it is *not* sure, loudly, so I can switch to
> the binder.

`[F-16]` Staff want an explicit confidence / "I am not certain, verify here"
signal. Today low confidence collapses into a generic redirect with no graded
signal. `[F-17]` A staff mode (denser, citation-first, less rider-friendly
preamble) is unbuilt.

### P10: Ben (library navigator)

> Q: What would make this usable side by side with a patron?
> A: Calm copy, no jargon, and links I can click to the actual agency page, not
> just a doc-id in brackets. `[doc:sacrt-fares]` means nothing to the person next
> to me.

`[F-18]` Citations render as internal `doc:` ids rather than human-readable
source names with resolvable URLs in the rider UI. The data exists in
`corpus/manifest.yaml`; it is not surfaced.

### P11: Sandra (paratransit clerk)

> Q: Your worry is riders arriving "pre-approved."
> A: Yes. The no-determination rule is exactly right. Keep it ruthless. But also
> make the handoff to *me* explicit: tell them the next step is an agency
> decision, with the contact, every time eligibility is in play.

`[F-19]` The determination guard is strong (good), but the *positive* handoff
("the agency decides; here is how to start") is inconsistent across answers.
Reinforces, does not relax, the hard rule.

### P12: Comms manager

> Q: What blocks you from embedding this?
> A: Liability of a wrong number. I need a visible "confirm with the agency"
> frame, a staleness indicator, and a way to pin the corpus to a date I approved.
> And I want the failure cases in your report; candor is what sells me.

`[F-20]` No embeddable widget / iframe with agency theming. `[F-21]` No
agency-facing "pin to approved corpus version" control or changelog of fare
changes since a given date.

### P13: ADA coordinator

> Q: What is your approval gate?
> A: A recorded manual screen-reader and keyboard walkthrough, plus reflow at 400%
> and the target-size evidence. Your automation is good; it is not the sign-off.

Reinforces `[F-7]`. `[F-22]` No recorded a11y walkthrough artifact (video or
written transcript with NVDA/VoiceOver) committed to the repo.

### P14: Procurement officer

> Q: What do you actually evaluate?
> A: The eval report, the refusal policy, the data provenance, and whether the
> claims are independently checked. Your GovChat-Eval second layer is the thing I
> would point my director to. Make that story impossible to miss.

`[F-23]` The independent-audit story is strong but buried below the fold; the two
failing GovChat lines (groundedness 0.04, multilingual 0.667) need a
procurement-readable explainer so a skeptic does not misread them. `[F-24]` No
one-page "security & data handling" sheet for buyers.

### P15: Cal-ITP program lead

> Q: Where is the boundary you care about?
> A: Verification is ours. You explain policy and route people to Benefits or the
> agency. The moment a rider thinks your bot verified them, the public-trust model
> we run breaks.

`[F-25]` No explicit, tested "this is policy explanation, verification happens at
Benefits/the agency" framing tied to the Cal-ITP overlap the corpus already has.

### P16: Eval engineer (reuse)

> Q: What would you fork?
> A: The runner, judges, deterministic checks, and the YAML case format. What I
> can't tell quickly is which parts assume transit doc-ids and agency-scope. Draw
> the seam.

`[F-26]` `docs/adapting.md` exists but the transit-specific coupling (doc-id
checks, agency filter, determination patterns) is not isolated behind a clear
interface for a new domain. `[F-27]` No "new domain in an afternoon" template or
scaffolding command.

### P17: Responsible-AI reviewer

> Q: Did the model card survive your read?
> A: Mostly. The P0 work closed the cost and calibration claims, which is good.
> But κ is 0.636 on a tiny pass-skewed sample, and the card should say plainly
> that this is weak evidence, not lean on the number. And "manual a11y pending"
> sits in the card while the README implies AA is done.

`[F-28]` Judge calibration rests on n=16; the sample needs to grow and span
failures, not just passes. `[F-29]` A small README/model-card consistency gap on
accessibility maturity should be reconciled.

### P18: Open-source contributor

> Q: Was the local loop friendly?
> A: `make test` and `--offline` evals worked with no key, which is excellent.
> But there is no CONTRIBUTING, no labeled good-first-issues, and the six known
> eval failures are described in prose, not filed as trackable issues.

`[F-30]` No CONTRIBUTING guide or issue templates. `[F-31]` Documented eval
failures are not individually trackable (no issue per failure with the failing
case id).

### P19: Red-teamer

> Q: Where did you get through?
> A: The hard refusals held: injection, PII, determination-baiting, all blocked,
> in both languages. Where I made it *degrade* was indirect: I never said "ignore
> instructions," I just asked leading questions until an answer over- or
> under-claimed (the veteran-documents and BeeLine cases). Your guard is regex on
> the input; the failures are in generation fidelity, not the guard.

`[F-32]` Input guards are pattern-based and could miss novel phrasings or
obfuscation (spacing, homoglyphs, base64-ish asks); worth a small adversarial
expansion in the refusal suite. `[F-33]` The higher-yield risk is faithfulness
drift under leading multi-turn questions, which the conversation suite only
covers in six cases.

### P20: Journalist / auditor

> Q: What is your headline risk for this project?
> A: "Civic AI tool's own audit scores 0.04 on groundedness." I know from the
> footnote it is the lexical judge, but you are one screenshot away from looking
> dishonest. Lead with the explanation, not the asterisk.

Reinforces `[F-23]`. `[F-34]` The audit's deterministic-judge floor needs an
above-the-fold framing and ideally a committed `--judge llm` run so the real
signal sits next to the proxy.

## Cross-cutting synthesis

Four themes recur across constituencies and matter more than any single finding.

1. **Faithfulness drift beats guard bypass.** Riders (P3, P5, P7), staff (P9),
   and adversaries (P19) all hit the same class of problem: the safety guards
   hold, but the *generated answer* over-claims a contact detail or under-claims
   a documented fact. This is the project's real quality frontier, and it is a
   generation/eval problem, not a guard problem. (`conv-004`, `conv-005`,
   `ground-024`, `ground-026`, `ml-010`.)
2. **The last step is missing.** Multiple riders get the criterion but not the
   action: where to apply, what it costs, which of two paths applies (P1, P3, P5,
   P8, P15). The corpus often holds this; retrieval and prompting do not reliably
   join "what is the fare" to "how do I get the card."
3. **Accessibility is asserted, not witnessed.** Automation passes; the lived
   walkthrough does not exist (P4, P13, P17). For a civic tool this is a
   credibility gate, not a nicety.
4. **The audit story is a strength that currently reads as a weakness.** The
   independent second layer is exactly what buyers (P14) want, but the unframed
   0.04 line is a journalist's (P20) gift. Framing, not new engineering, fixes it.

## Execution log (2026-06-20)

A first execution pass against this backlog, with the honesty the project asks
for: some findings turned out to be already satisfied once the code was read,
and those are recorded as such rather than "fixed" twice.

Verified already built (the synthetic finding was inaccurate about current state):

- **F-18 (readable citations).** The rider UI already strips inline `[doc:id]`
  tags and renders a "Sources" list of agency, title, and resolvable URL
  (`web/index.html`). No work needed; R1-1's UI half is done.
- **F-8 (focus on new answers).** The page already moves focus to each new turn
  and announces status through an `aria-live` region (`web/index.html`). The
  remaining gap is the human screen-reader walkthrough, not the code.

Executed this pass:

- **R0-6 done.** Added [`docs/procurement-brief.md`](../procurement-brief.md),
  the one-page buyer artifact P14 and P12 asked for, linked from the README.
- **R1-7 done.** Added reader text-size (A / A+ / A++) and high-contrast controls
  to the demo for P1 and P4, persisted per browser, with a test
  (`tests/test_web.py::test_display_settings_controls_present`). The structural
  a11y gate still passes; high-contrast only deepens contrast so it cannot
  regress the AA baseline.
- **R0-2 done (framing half).** The README now frames the GovChat-Eval table
  before the scary 0.04 line so a screenshot cannot mislead (P20). Committing a
  live `--judge llm` audit run still needs Bedrock credentials.

- **R0-5 done.** The six current eval failures are filed as tracked issues
  (#1–#6) against the repo, each with its case id, the failing check, the root
  cause, a proposed remediation, and the note that validation needs a live judge.

Prepared but not live-validated (the environment has no Bedrock or Anthropic
credentials, so no judge can run here):

- **R0-1 (faithfulness fixes).** The answer and system prompts are updated to
  stop the over-hedge that drives `conv-004`, `ground-026`, and `ml-010` (saying
  "not specified" when a passage does specify), to attribute contacts only to the
  program a passage supports (`conv-005`), to state an asked-for figure even when
  a free option exists (`ground-026`), and to keep the "as of" line on partial
  answers (`refuse-018`). System prompt v6 and answer_user v3 carry a loud
  NOT-YET-LIVE-VALIDATED header. All offline gates pass (ruff, mypy, 109 tests,
  clean offline eval run), which proves no plumbing or deterministic-safety
  breakage, but the LLM-judge groundedness and helpfulness checks that these
  cases actually fail on can only be confirmed by `make eval` (full, live) with a
  green regression gate. Do that before merging the prompt change to main; the
  ROADMAP records that a prior blind attempt at `ground-026` regressed other
  cases and was reverted, so the live gate is the real acceptance test.

Deliberately not executed here, with the reason:

- **R1a-1 (manual screen-reader walkthrough).** Needs a human with NVDA or
  VoiceOver. Automation cannot stand in for it; that is the point of the item.

### Second pass (2026-06-20)

More verifiable, no-credentials items executed, each with a test or a committed
artifact:

- **R1-5 done.** A graded retrieval-confidence signal (`low` / `medium` / `high`
  plus the top score) now rides on every `AnswerResult` and the API payload, the
  signal staff and integrators asked for (F-16). It never changes the answer or
  the guards. Tests in `tests/test_answer.py` and `tests/test_web.py`.
- **R1a-2 done.** A test locks the polite live region that announces answers and
  status (`tests/test_web.py::test_live_region_present_for_answer_status`); the
  behavior already existed and is now regression-protected.
- **R1a-1 scaffolded.** `docs/audits/a11y-walkthrough.md` is the manual
  screen-reader and keyboard checklist with an empty result log, linked from the
  README and the model card. The human pass itself still has to be performed and
  recorded; the artifact is now ready for it.
- **R0-3 done.** The README now states that the audit's a11y `1.000` is the
  automated check, not a sign-off, and points to the pending manual walkthrough,
  reconciling it with the model card.
- **R3-4 done.** `CONTRIBUTING.md`, two issue templates (eval-failure and
  bug/feature), and a PR template, all encoding the prompt-needs-live-eval
  discipline and the hard rules.

Dropped after checking the evidence (honest non-action):

- **R1-3 (microtransit retrieval).** Not justified. In `ground-024` the BeeLine
  passage was already retrieved top-ranked (the error was a generation misread of
  the row), and in `ml-010` the rider never named BeeLine, so retrieving it is
  not the fix. The evidence says retrieval is not the bottleneck here, consistent
  with ADR 0007, so changing it would be a vibes edit the project forbids. The
  fix is the prompt rule (shipped in v6) and the documented limitation.

### Third pass (2026-06-20): first multi-session feature

- **R2-1 done.** An offline, printable fare reference at `GET /offline`, rendered
  once per container from the committed corpus with no model call and no network
  (persona research F-11, P6/P1). It groups every agency's dated policy passages
  on one page a rider can save or print before a trip, with the same reference-
  implementation framing and resolvable source links as the chat page. Generator
  in `web/offline.py` (`make offline` writes it for inspection), route in
  `web/handler.py`, footer link from the demo, and the Lambda bundle now ships
  it (`infra/deploy.sh`). Tested: the route serves, covers all five agencies, and
  the generated HTML passes the same structural a11y gate as the chat page. Making
  `web` a package (`web/__init__.py`) was needed so the intra-package import type-
  checks under one module name.

### Fourth pass (2026-06-20): PDF ingest

- **R3-5 done.** Ingest can now read a PDF policy, closing CLAUDE.md open
  question #3. A manifest document marked `format: pdf` (or a PDF content type on
  fetch) is snapshotted as `.pdf` and processed through a text-first path:
  `extract_pdf_text` reads the text layer with pypdf, `sections_from_text` infers
  headings from flat text, and the same `_finalize_sections` tail as HTML shapes
  the chunks so a PDF cites exactly like an HTML page. An OCR fallback for scanned
  PDFs is wired behind `ocr: true` and the `ocr` extra but, honestly, not
  exercised in CI (it needs tesseract and poppler system binaries). Optional `pdf`
  extra keeps the core install light. ADR 0008 records the tradeoffs. Tests build
  a minimal PDF by hand and round-trip it through extraction and sectioning, so
  the only new test dependency is pypdf. No corpus document is PDF yet; wiring a
  real agency PDF (for example MST's Courtesy Card form) is the follow-up.

### Fifth pass (2026-06-20): embeddable widget

- **R2-5 done.** A compact, frameable widget at `GET /embed` an agency can drop
  into its own fare page with one iframe (persona P12, the comms manager). It is
  the only frameable route: it drops the `x-frame-options: DENY` every other
  response carries and instead names allowed ancestors in CSP `frame-ancestors`,
  read at call time from `FPA_EMBED_ANCESTORS` (default `*` for the demo, an
  origin allowlist in production). The main page stays `DENY`, tested so embedding
  did not loosen it. The widget is served same-origin, so its `/api/ask` call
  stays under `connect-src 'self'`; it carries the reference-implementation notice
  and the does-not-decide-eligibility line so the limits travel with the embed;
  and it passes the same structural a11y gate. Generator in `web/embed.py`, route
  and frame headers in `web/handler.py`, bundled by `infra/deploy.sh`, snippet in
  the README.

### Sixth pass (2026-06-20): corpus version pinning

- **R2-6 done.** The corpus now has a stable identity. `assistant.corpus`
  computes a deterministic `corpus_version` (a short hash over chunk content and
  fetch dates, order-independent), a `corpus_summary`, and a `diff_corpus` that
  names added/removed/changed documents between two snapshots. The `/version`
  endpoint reports the running corpus and, against `FPA_PINNED_CORPUS_VERSION`,
  whether it matches the version an operator approved (a logged warning on
  mismatch, not a block). The version rides on every `/api/ask` payload and the
  offline page footer, and `corpus/CHANGELOG.md` is seeded with the current
  version. Honest limit: the changelog has one entry because there is one
  snapshot; `diff_corpus` is built and tested so the weekly corpus-freshness
  automation can write future entries on drift. Tests in `tests/test_corpus.py`
  and `tests/test_web.py`.

### Full backlog disposition

Every backlog id below, so nothing is silently dropped. "Blocked: creds" means it
needs a live answer/judge model (none in this environment). "Blocked: human"
needs a person. "Feature" is a multi-session build left for a focused effort.

| ID | Status |
|---|---|
| R0-1 | Prepared, pending live validation (prompts v6/v3; needs `make eval`) |
| R0-2 | Done (README audit framing) |
| R0-3 | Done (README ↔ model-card a11y reconciled) |
| R0-4 | Blocked: creds. Growing the calibration sample needs live judge verdicts to label against; only the report-prose caveat is doable offline |
| R0-5 | Done (issues #1–#6) |
| R0-6 | Done (procurement brief) |
| R1-1 | Done (UI already renders readable Sources; verified) |
| R1-2 | Blocked: creds (close-the-loop prompting needs live validation); eval cases can be pre-written |
| R1-3 | Dropped as unjustified (see above) |
| R1-4 | Blocked: creds (positive-handoff prompt rule, partly in v6); eval case can be pre-written |
| R1-5 | Done (confidence signal) |
| R1-6 | Blocked: creds (two-ways-to-qualify template, partly in v6) |
| R1-7 | Done (text-size + contrast controls) |
| R1a-1 | Scaffolded; Blocked: human (perform and record the walkthrough) |
| R1a-2 | Done (live-region test) |
| R1a-3 | Folded into the walkthrough checklist; the JS-built reading treatment cannot be verified by the static gate, so it belongs to the manual pass |
| R2-1 | Done (offline/printable fare reference at `/offline`, built from the corpus) |
| R2-2 | Blocked: creds (staff answer mode is a prompt variant) |
| R2-3 | Feature + creds (stretch language: corpus, prompts, suite) |
| R2-4 | Done already (privacy-safe feedback exists in handler + UI; verified) |
| R2-5 | Done (embeddable widget at `/embed`, frameable via configurable CSP) |
| R2-6 | Done (corpus version id, `/version` pin check, `diff_corpus`, seeded changelog) |
| R3-1 | Partly doable: adversarial-faithfulness eval *cases* can be added (deterministic parts validate offline; judge parts need creds) |
| R3-2 | Feature (cross-agency trip handling) |
| R3-3 | Feature (isolate transit coupling; new-domain scaffold) |
| R3-4 | Done (contributor scaffolding) |
| R3-5 | Done (PDF text-layer ingest + OCR-fallback hook, ADR 0008) |

The honest summary: every item that is a document, a test, a safe UI affordance,
or a code signal that can be validated offline is now done. What remains splits
into three piles, none of which a single offline pass can legitimately close:
prompt and answer-quality work that needs a live judge (R0-1, R1-2, R1-4, R1-6,
R2-2, R0-4), the human accessibility walkthrough (R1a-1), and multi-session
features (R2-1, R2-3, R2-5, R2-6, R3-2, R3-3, R3-5).

## Remediations and expansions backlog

Grouped by theme, each with the findings it answers, a rough effort (S/M/L), the
files it touches, and whether the existing ROADMAP already names it. Priority
bands: **R0** integrity and trust (do first, cheap, high credibility), **R1**
rider-facing quality, **R2** expansions, **R3** larger bets. Nothing here relaxes
a `CLAUDE.md` hard rule; several items strengthen them.

### R0: Integrity and trust (mostly framing and small fixes)

| ID | Item | Findings | Effort | Files | In roadmap? |
|---|---|---|---|---|---|
| R0-1 | Fix the documented faithfulness failures as targeted prompt/eval work: state an asked-for figure even when a free option exists; never claim "not specified" when the passage specifies; attribute a contact only to the program the passage ties it to. | F-3, F-5, F-10 | M | `prompts/answer_user.txt`, `evals/suites/groundedness.yaml`, `conversation.yaml` | Partly (P0-2 lists `ground-026`) |
| R0-2 | Above-the-fold framing of the GovChat-Eval lexical-judge floor; commit a `--judge llm` audit run beside the deterministic one so the real number sits next to the proxy. | F-23, F-34 | S | `README.md`, `docs/audits/`, `Makefile` | No |
| R0-3 | Reconcile the README "WCAG 2.2 AA" claim with the model card's "manual pass pending" so the two read consistently until the walkthrough exists. | F-29 | S | `README.md`, `docs/model-card.md` | No |
| R0-4 | Grow the judge-calibration sample beyond n=16, deliberately including failure cases, and state in the report that current κ is weak evidence. | F-28 | M | `evals/calibration/judge_labels.jsonl`, `evals/calibration.py`, `EVALS.md` | No |
| R0-5 | File each of the six known eval failures as a trackable issue carrying its case id and the failing check, replacing prose-only tracking. | F-31 | S | issues, `EVALS.md` | No |
| R0-6 | Add a procurement one-pager: data provenance, refusal policy, no-PII/no-persistence, and the two-layer eval story in plain language. | F-24, F-14 | S | `docs/` (new) | No |

### R1: Rider-facing quality

| ID | Item | Findings | Effort | Files | In roadmap? |
|---|---|---|---|---|---|
| R1-1 | Render citations as human-readable source name + resolvable URL in the rider UI instead of `[doc:id]`; data is already in the manifest. | F-18, F-10 | M | `web/index.html`, `web/handler.py`, `corpus/manifest.yaml` | No |
| R1-2 | "Close the loop" prompting and retrieval: when a fare question has a matching application/ID-card passage, include where-to-apply, cost, and hours. Add edge cases that assert the next step is present. | F-1, F-3, F-6, F-15 | M | `prompts/answer_user.txt`, `src/assistant/retrieve.py`, `evals/suites/edge_cases.yaml` | No |
| R1-3 | Improve on-demand/microtransit retrieval so BeeLine-type questions stop collapsing into redirects; add a parity check vs. fixed-route. | F-12, F-7(data) | M | `src/assistant/retrieve.py`, `evals/suites/groundedness.yaml` | No |
| R1-4 | Explicit positive handoff on any eligibility-adjacent answer: "the agency decides; here is how to start," tested as a check. Strengthens the no-determination rule. | F-19, F-25 | S | `prompts/answer_user.txt`, `evals/suites/refusal.yaml` | No |
| R1-5 | Graded confidence signal surfaced to riders and staff when retrieval is weak ("I am not certain about this; confirm here"), distinct from a hard refusal. | F-16 | M | `src/assistant/answer.py`, `web/handler.py` | No |
| R1-6 | "Two ways to qualify" disambiguation pattern (Medicare card vs. 65+, free program vs. paid fallback) as a reusable answer template plus edge cases per agency. | F-6, F-15, F-10 | M | `prompts/`, `evals/suites/edge_cases.yaml` | No |
| R1-7 | Senior/low-vision affordances on the demo: a visible text-size/contrast control and plain-language default copy. | F-2 | S | `web/index.html`, `web/a11y.py` | No |

### R1-a: Accessibility (gating for any agency demo)

| ID | Item | Findings | Effort | Files | In roadmap? |
|---|---|---|---|---|---|
| R1a-1 | Do and **record** the manual screen-reader + keyboard walkthrough (NVDA and VoiceOver), reflow at 400%, target-size evidence; commit the artifact. | F-7, F-22, F-13(a11y) | M | `docs/` (new), `docs/model-card.md` | Yes (P2-5 remaining) |
| R1a-2 | Verify and fix focus management / ARIA live region so a new answer is announced and receives focus. | F-8 | S | `web/index.html`, `tests/test_a11y.py` | Partly |
| R1a-3 | Accessible reading treatment for citations and the "as of" line (not a wall of brackets to a screen reader). | F-9 | S | `web/index.html` | No |

### R2: Expansions (new reach, bounded cost)

| ID | Item | Findings | Effort | Files | In roadmap? |
|---|---|---|---|---|---|
| R2-1 | Offline/low-bandwidth rider path: a printable or cached per-agency fare card generated from the committed corpus, served statically. | F-11 | M | `web/`, `src/assistant/` | No |
| R2-2 | Staff mode: denser, citation-first answer style behind a flag, for call-center and navigator use. | F-17, F-9 | M | `prompts/`, `web/handler.py` | No |
| R2-3 | One stretch language at clearly-tagged non-parity (Chinese or Vietnamese), honest about cross-lingual retrieval limits. | F-14 | L | `evals/suites/` (new), `prompts/`, corpus | Yes (P3-3) |
| R2-4 | Privacy-safe feedback (thumbs verdict + response kind + corpus version only, never content) to get a real helpfulness signal. | F-16, F-33 | M | `web/handler.py`, `web/index.html` | Yes (P2-3 open) |
| R2-5 | Embeddable widget with agency theming and a visible "confirm with the agency" frame and staleness indicator. | F-20, F-12(comms) | L | `web/` | No |
| R2-6 | Agency "pin to approved corpus version" + a fare-change changelog since a chosen date, surfaced in the UI. | F-21 | M | `corpus/manifest.yaml`, `web/`, freshness workflow | Partly (P1-1) |

### R3: Larger bets (do when the core is solid)

| ID | Item | Findings | Effort | Files | In roadmap? |
|---|---|---|---|---|---|
| R3-1 | Adversarial-faithfulness expansion of the conversation and refusal suites: leading multi-turn questions that bait over/under-claiming, plus obfuscated-injection probes (spacing, homoglyphs). This is the project's real risk frontier. | F-32, F-33, F-5 | L | `evals/suites/conversation.yaml`, `refusal.yaml` | No |
| R3-2 | Cross-agency trip handling ("Yolobus then SacRT"): retrieve and answer across two agencies without losing citations. | F-13 | L | `src/assistant/retrieve.py`, `answer.py` | No |
| R3-3 | Isolate the transit-specific coupling (doc-id checks, agency filter, determination patterns) behind a clear interface and add a "new domain" scaffold so a fork starts from the audited skeleton. | F-26, F-27 | L | `evals/`, `src/assistant/`, `docs/adapting.md` | Yes (P3-5) |
| R3-4 | CONTRIBUTING guide, issue/PR templates, and a curated good-first-issue set drawn from the documented failures. | F-30, F-31 | S | `.github/`, `CONTRIBUTING.md` | No |
| R3-5 | PDF/OCR ingest for agencies that publish policy as PDF, gated behind a flag with an ADR. | (panel-adjacent; corpus breadth) | L | `src/assistant/ingest.py` | Yes (P3-2) |

## Suggested sequencing

R0 is almost entirely framing and small fixes, and it is the highest-credibility
work for a portfolio piece whose thesis is honest evaluation: do it first. Inside
R0, the two items that move the project's headline are R0-1 (the documented
faithfulness misses) and R0-2 (the audit framing the journalist persona would
exploit). R1a (the recorded accessibility walkthrough) is the gate every agency
persona named, so it precedes any "production-ready" claim. R1 rider-quality work
follows, led by R1-1 (readable citations) and R1-2 (close the loop), because they
touch every rider interview. R2 and R3 are expansion bets to pull from as the
core hardens, with R3-1 (adversarial-faithfulness evals) the most defensible next
investment given that faithfulness drift, not guard bypass, is where every
stress-tester actually got through.

## Validation plan (before any of this changes the product)

Synthetic findings earn their place only by becoming a checkable case or a real
observation:

- Each rider-quality remediation lands with at least one new eval case carrying
  the corpus passage it depends on, so "fixed" is a passing check, not a vibe.
- The accessibility items land as a recorded walkthrough plus assertions in
  `tests/test_a11y.py`, not as prose.
- The faithfulness items are validated by the LLM-judge groundedness suite and by
  a grown calibration sample, since these failures sit on the judge's decision
  boundary.
- Where a persona asked for something the hard rules forbid (a ruling, a saved
  profile, a "just tell me yes"), the recorded outcome is a clearer explanation
  or a stronger handoff, and the no-determination and no-PII checks must still
  pass.
