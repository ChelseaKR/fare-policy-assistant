# Roadmap — productionalization and feature-completeness

How to read this: the project is already a working, deployed, evaluated
reference implementation. This roadmap is the path from "credible portfolio
artifact" to "a thing a government team could actually run." Items are grouped
by priority, not by size. Each names the files it touches and what "done" means
so the work is checkable, not aspirational. The phases are ordered; within a
phase, items are roughly independent.

The hard limits in [`CLAUDE.md`](../CLAUDE.md) still bind every item below: no
eligibility determinations, no PII collection or query persistence in the
deployed demo, every answer cited, corpus dated. Nothing here relaxes those.

## Current state (2026-06-16)

Done: 4-agency dated corpus with transposed-table normalization (ADR 0005);
BM25 retrieval with EN/ES query expansion and agency filter; guarded answer
pipeline (PII, scope, injection, determination-language, as-of); 103-case eval
harness across five suites at 97/103 with an LLM judge and a regression gate;
an independent GovChat-Eval audit; an accessible single-page demo on Lambda +
HTTP API with layered cost guards; CI (lint, types, tests, smoke evals);
model card and five ADRs.

Known-incomplete, addressed below: a model card that overstates what the
harness records; six documented eval failures; manual corpus snapshots; thin
observability; single-shot Q&A only.

## P0 — Integrity and correctness

These come first because the project's whole claim is "here is how I test it,
honestly." A published doc that overstates the harness is the worst possible
bug for this repo specifically.

> **Status (2026-06-16):** done. Per-run cost and judge-vs-human calibration
> now render in `EVALS.md` (the two model-card claims are real). `fresh-001` is
> fixed (combined-citation parsing). A prompt attempt at `ground-026` and
> `refuse-018` regressed other cases and was reverted; both stay documented.
> The GovChat-Eval audit runs in CI as an advisory job. Net: 97 → 98/103.

1. **Close the model-card claims.** `docs/model-card.md` states per-run cost is
   documented and that judge agreement is spot-checked on a 10% human-labeled
   sample and recorded in the report. Neither exists in `evals/`. Either build
   them or soften the prose to match reality — building is better:
   - *Per-run cost:* `models.py` already returns `input_tokens`/`output_tokens`
     on every `Completion`; thread them through `runner.py` into `summary.json`
     and the report, priced from a small per-model table in `config.py`.
     Done = `EVALS.md` shows tokens and an estimated USD cost for the run.
   - *Human agreement:* add an `evals/calibration/` set of hand-labeled
     judgments (≥10% of judged cases), a `judges.py` routine that scores the
     judge against them (agreement and Cohen's κ), and a line in the report.
     Done = the report prints judge-vs-human agreement with its n.
2. **Remediate the fixable eval failures.** Of the six, three are real and
   targeted:
   - `refuse-018` / partial answers omit the "as of" line — adjust the answer
     prompt so partials still disclose the snapshot date. Done = `refuse-018`
     passes its `as_of_disclosure` check.
   - `ground-026` (SacRT student) — the answer leads with the free RydeFreeRT
     program and drops the $20 monthly price the question asked for; a prompt
     rule to state an asked-for figure even when a free option exists. Done =
     `ground-026` reports the $20.
   - `fresh-001` — the guard over-blocks a meta-question about data currency;
     let the assistant answer "based on documents fetched on <date>" without
     tripping the citation guard. Done = `fresh-001` answers instead of
     `answered_guarded`.
   - Leave `ground-024` (a model misread of a well-formed table),
     `edge-002`, and `ml-004` (groundedness-judge strictness) documented; they
     are honest failures, not defects to paper over.
3. **Wire the GovChat-Eval audit into CI as non-blocking.** It runs only by
   hand today. Add a CI job that runs it against the committed dataset and
   uploads the report; keep it advisory until the deterministic-judge floor is
   understood, then gate. Done = every PR shows an audit artifact.

## P1 — Production hardening

What a real operator needs before trusting the thing unattended.

> **Status (2026-07-02):** items 1, 2, and 5 done. Weekly corpus-freshness
> automation opens a PR on drift (`.github/workflows/corpus-freshness.yml`) and
> the UI shows how long ago the cited policies were fetched; a per-container
> answer cache fronts the model call in the deployed handler; the CI badge is
> in the README. The freshness loop is now closed end to end (FIX-09): the
> workflow keys the change decision on `corpus_version` rather than a raw
> `git diff -- corpus/`, `tools/corpus_refresh_report.py` writes the changelog
> entry and doc-level diff into the PR body and lints the eval suites for stale
> facts, and `/version` reports `staleness_days` against a budget. Remaining:
> observability/alarms (item 3) and a true cross-container rate limit (item 4).

1. **Corpus-freshness automation.** ✅ **Done.** Snapshots were taken by hand
   (`make fetch`) and the UI's "as of" date was drifting. A scheduled GitHub
   Actions job now re-fetches the manifest URLs, keys the change decision on the
   processed-chunk `corpus_version` (furniture-only churn opens nothing), and on
   a real change opens a PR whose body carries the doc-level diff, a new
   `corpus/CHANGELOG.md` entry, and a staleness lint of `evals/suites/` (a case
   whose `required_facts` the refreshed corpus dropped, or whose
   `forbidden_content` it now contains, is flagged with the same matcher as the
   eval gate). The staleness budget (`FPA_STALENESS_BUDGET_DAYS`, default 90) is
   surfaced on `/version` and the UI. A corpus change becomes a reviewable PR
   with eval deltas, no human polling.
2. **Answer caching in the deployed path.** Every question re-pays Bedrock. Add
   a content-keyed cache (normalized question + corpus hash → answer) in front
   of the model call in `answer.py`, backed by an in-memory LRU per container
   and optionally a short-TTL store. Done = repeated questions skip the model
   call; cost-per-demo-session drops measurably.
3. **Observability and cost backstop.** The handler logs counts and timings but
   nothing watches them. Add CloudWatch metric filters and alarms (error rate,
   p99 latency, Bedrock throttles), and an AWS Budget alarm as a hard backstop
   beneath the app-level guards. Done = a spend or error spike pages someone;
   the demo's per-day cost is visible on a dashboard.
4. **A true rate limit.** The current 8/min budget is per-container and resets
   on cold starts, so it is a soft guard, not a real limit. Add gateway-level
   throttling tuned with load, or a token-bucket keyed on a coarse, non-PII
   signal. Done = a documented, tested request ceiling that holds across
   containers without persisting anything identifying.
5. **CI live-evals on protected branches.** `ci.yml` already reads
   `AWS_OIDC_ROLE_ARN`; set the repo variable and an IAM role so the nightly
   full suite and smoke judges run for real, and add the CI badge to the
   README. Done = the badge is green from a live nightly run.

## P2 — Feature completeness

The product surface CLAUDE.md scopes but the current build only partly covers.

> **Status (2026-06-16):** item 1 (multi-turn) done, with eval coverage. The
> client holds the conversation and sends the last few turns; the server stores
> nothing. Follow-ups that name no agency resolve against the prior turn in both
> retrieval and the prompt, and the output guard still polices every answer. A
> six-case `conversation` suite formalizes this at 5/6 — including the safety
> case (a "just tell me I qualify" follow-up still refuses to determine); the
> one miss is the same Spanish-veteran groundedness-judge strictness as ml-004,
> not a conversation bug. Item 2 (streaming) is deferred with a recorded
> rationale (ADR 0006): the API Gateway HTTP API the org policy forced cannot
> stream, and the hard output guard limits any streaming to a cosmetic
> post-guard replay. Item 4 (dense-retrieval decision) is settled with evidence
> (ADR 0007): a retrieval-recall ablation shows the dense hybrid is worse
> everywhere and −11.8 points on Spanish — the case it was meant to help — so it
> stays off, behind its flag. Item 3 (feedback) is open. Item 5 (a11y wiring)
> is code-complete — GovChat-Eval's a11y suite now audits every recorded
> transcript — with only the manual screen-reader/keyboard walkthrough left,
> which needs a human at a real assistive-tech session, not automation.

1. **Multi-turn within a session.** The UI is a chat but the pipeline is
   single-shot; "what about my spouse?" loses context. Add stateless
   conversation: the page passes prior turns back, the API threads them into
   retrieval and the prompt, still persisting nothing server-side. Done = a
   follow-up question resolves pronouns against the previous answer; a new eval
   sub-suite covers two-turn cases.
2. **Streaming responses.** ADR 0004 rejected streaming because the output
   guard needs the whole answer first; revisit by streaming *after* the guard
   passes (guard the full text, then replay it as SSE) so perceived latency on
   the ~5s Bedrock call improves without weakening the guarantee. Done = the
   demo renders progressively; the guard still sees complete text.
3. **Privacy-safe feedback.** No "was this helpful?" signal today. Add a
   thumbs-up/down that logs only the verdict, the response kind, and the
   corpus version — never the question or answer. Done = aggregate helpfulness
   is queryable without storing any rider content.
4. **Settle dense retrieval with an ADR.** `FPA_DENSE` is implemented but off;
   the multilingual suite (at 95%) is meant to decide whether it earns its
   place, especially for Spanish questions over English-only docs. Run the
   suite both ways, record the delta, and either enable it or document why not
   (extends ADR 0001). Done = the choice is evidence-backed, not deferred.
5. **Wire the a11y audit, not just structure.** *(Automation done; one manual
   step outstanding.)* The page targets WCAG 2.2 AA, a pure-Python structural
   gate (`web/a11y.py`) runs in CI and as `make a11y`, and an advisory
   pa11y/axe pass cross-checks contrast and ARIA. Every recorded turn also
   carries an accessible HTML transcript
   (`evals.govchat_export.render_transcript` → `transcript_html` in
   `evals/govchat/golden.jsonl`), so GovChat-Eval's structural checker audits
   a11y too — see `docs/audits/methodology.md` ("a11y now runs"). Remaining:
   do and record the manual screen-reader and keyboard-only walkthrough.
   Checklist and result log live in `docs/audits/a11y-walkthrough.md`; the
   result table is still unfilled (model card notes it pending), and the demo
   should not be presented as accessibility-reviewed beyond automation until a
   person completes it.

## P3 — Breadth and scale

Higher cost, lower urgency; do when the core is solid.

1. **More agencies.** CLAUDE.md targeted 4-6; Unitrans was dropped (WAF 403)
   and LA Metro is a stated stretch. Add one or two, each with manifest entry,
   snapshot, edge-case cases built from its real policy, and a parity check.
   Done = the new agency has the same eval coverage as the existing four.
2. **PDF/OCR ingest.** Open question #3 in CLAUDE.md is unresolved: some
   agencies publish policy as PDFs. Add a PDF path to `ingest.py` (text
   extraction, OCR fallback) gated behind a flag, with an ADR on the tradeoffs.
   Done = a PDF-only policy is citable like an HTML page.
3. **Stretch languages.** Only Spanish is at parity. Add one more high-demand CA
   language (Chinese, Vietnamese, Tagalog, or Korean) as a clearly-tagged
   stretch suite, honest about cross-lingual retrieval limits. Done = the new
   language has a mirrored suite and the report shows its parity gap.
   - *(FIX-11 — done.)* **Robust, extensible language identification.** The
     two-regex EN/ES word-count heuristic in `guards.detect_language` is replaced
     by a dependency-free, deterministic character-trigram classifier
     (`src/assistant/langid.py`) with per-language profiles committed as
     `src/assistant/lang_profiles.json` and regenerated from committed sample
     texts by `tools/build_lang_profiles.py` — so **adding a language is a data
     file**. It supports en/es/**tl** (Tagalog), returns a confidence from the
     top-two margin, and gives an honest `unsure` verdict for short / ambiguous /
     code-switched input. `detect_language` keeps its `str` signature for
     existing callers (`answer.py`, `retrieve.py`, `evals/checks.py`);
     `detect_language_confident` exposes `(lang, conf, unsure)`. An uncertain
     detection **never blocks** an answer — `check_input` proceeds in English and
     attaches a translated "I wasn't sure of your language" note (new gettext
     msgid, EN/ES catalogs). Tagalog is *detected* but has no fixed-string
     catalog yet, so its refusals fall back to English via
     `gettext` `fallback=True` (documented in `i18n.py`). Remaining for full
     parity: a mirrored `tl` eval suite and a `locales/tl` catalog.
4. **Reranker, only if earned.** Per CLAUDE.md, add one only if the evals show
   retrieval is the bottleneck, and justify it in an ADR with the deltas. Today
   retrieval is not the bottleneck (the failures are generation and judge
   strictness), so this stays unbuilt until a failure says otherwise.
5. **Generalize the harness.** `docs/adapting.md` and the civic-AI family
   (GovChat-Eval, civic-rag-starter-kit) are the start of "adapt this to your
   domain." Fold the lessons from this project back into that template so the
   next domain assistant starts from the audited skeleton rather than rebuilding
   it. Done = a second domain can stand up the same gates without forking this
   repo.

## Sequencing

P0 first and soon: the model-card claims should not sit overstated on a public
repo. P1 items 1 and 3 (freshness automation, observability) are the difference
between "deployed" and "operable" and are worth doing before promoting the demo
widely. P2 multi-turn is the highest-visible feature gain. P3 is opportunistic.

## Out of scope (and staying that way)

User accounts; persistence of rider queries in the deployed demo; any path that
requires PII to function; a hosted multi-tenant SaaS; and — the one that defines
the project — automated eligibility determinations. The assistant explains
published criteria and never rules on a person.
