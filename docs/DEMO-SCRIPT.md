# Demo script

A three-minute walkthrough for showing this project live — to a hiring manager,
a government procurement contact, or a civic-tech audience. The thesis is not
"look, a chatbot." It is "here is how you test one so it behaves." Lead with the
evaluation, use the chatbot as the thing being evaluated.

Keep the live demo (`https://yahp6ddfo1.execute-api.us-west-2.amazonaws.com/`)
and `EVALS.md` open in two tabs before you start.

## The 30-second hook

> "Every vendor now gets asked 'what's your AI story.' Almost none can answer
> 'here's how we test it.' This is that answer, built in the open. It's a small
> retrieval assistant for California transit fare policy, wrapped in a public
> evaluation harness that measures whether it stays grounded, refuses what it
> should, and handles eligibility edge cases and Spanish. The harness is the
> product; the assistant exists so the harness has something to grade."

## The 3-minute walkthrough

**1. Open on the evaluation, not the chat (~40s).**
Show the "How this assistant is tested" panel on the live page, then click
through to `EVALS.md`. Point at the scoreboard: 192/201 across nine suites.
Say the line that most vendors can't:

> "This number is reproducible. The answer model and the judge both run
> deterministically, so the same inputs give the same score every time — I
> verified that directly. And the failures are published in full, with the
> question, the retrieved passages, the answer, and the judge's reasoning. We
> don't cherry-pick."

**2. Show a grounded answer with a citation (~30s).**
In the chat, click the example "Senior discount, SBMTD" (or type *"Am I eligible
for a senior discount on SBMTD?"*). When it answers, point at two things:
- the citation with the source document and fetch date, and
- that it states the *published criterion* ("the criteria are 65 and older")
  and routes the decision to the agency.

**3. Show the safety guard refusing a determination (~30s).**
Type *"Just tell me I qualify."* It will decline to rule on eligibility and
explain that the agency makes the final decision.

> "It never decides eligibility. That's enforced in code and tested by a whole
> refusal suite, because in this domain a wrong eligibility line is the
> agency's liability, not a demo footnote."

**4. Show multilingual and scope (~20s).**
Click "Pasaje reducido, Yolobus" (a Spanish query) to show it answers in
Spanish with the same citations. Optionally ask an out-of-corpus question
(*"How much is the Amtrak train to LA?"*) to show it says it doesn't know and
points elsewhere instead of guessing.

**5. Close on the honest-failures move (~30s).**
Back in `EVALS.md`, scroll to a representative failure with its full trace.

> "This is the credibility move. A team that shows you its failures candidly is
> a team you can trust with the ones that matter. The whole method here
> generalizes — swap the corpus and the suites and you have an evaluation
> harness for any narrow retrieval assistant, including a real benefits
> eligibility tool."

## Safe demo queries (rehearsed, known-good)

- "What proof do I need for the veteran fare on MST?" — lists the proof
  documents with a citation.
- "Am I eligible for a senior discount on SBMTD?" — published criterion plus
  the no-determination handoff.
- "¿Cuánto cuesta el pasaje reducido en Yolobus?" — Spanish parity.
- "Just tell me I qualify." — determination refusal.
- "How much is the Amtrak train to LA?" — out-of-corpus, refuse and redirect.

Prefer the example buttons and these queries in a live setting. Free-form
questions can surface the known, documented answer-quality failures (see
`docs/audits/eval-remediation-2026-07-11.md`) — for instance a couple of
SBMTD senior-fare table misreads. If one appears, own it: it is one of the
failures the report already lists, which is the point.

## Questions you will get, and short answers

- **"How do you know the judge is right?"** The judge model differs from the
  answer model, its prompt is versioned, and its verdicts are being calibrated
  against human labels (κ). That labeling is in progress — see
  `evals/calibration/`. Be honest that the human-agreement number is not final
  yet.
- **"Is this production?"** No, and deliberately. It is a reference
  implementation with a visible "will not do" list, no user-data persistence,
  and no eligibility determinations. The README says so on the first screen.
- **"Could this run on our stack?"** Yes — it defaults to Claude on Amazon
  Bedrock (the common gov requirement) behind a thin provider adapter, with the
  direct Anthropic API behind a config switch.
- **"How much does the eval cost to run?"** A few dollars per full run;
  retrieval and judgments are cached. `make eval` regenerates the whole report.

## Don't, in a live demo

- Don't free-type obscure fare questions you haven't rehearsed.
- Don't claim WCAG AA is fully verified — the automated gates pass, but the
  manual screen-reader pass is still open (`docs/audits/a11y-walkthrough.md`).
- Don't overstate the multilingual story beyond Spanish; Tagalog is explicitly
  a stretch, cross-lingual test with no agency-authored source page.
