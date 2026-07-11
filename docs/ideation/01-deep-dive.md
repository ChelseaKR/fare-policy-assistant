# Deep dive — current state as read on 2026-07-01

Everything below is from reading the repository at HEAD of `main`
(`9cc479a`, the gettext migration) plus the unmerged branches. Nothing was
executed except `git log` / `git status`; no tests, builds, or network calls.

## Architecture summary

The system is a deliberately small RAG pipeline wrapped in a much larger
measurement apparatus. The pipeline:

- **Ingest** (`src/assistant/ingest.py`): manifest-driven polite fetch
  (`corpus/manifest.yaml`, 12 documents, 5 agencies), HTML cleaning with
  boilerplate stripping, section-preserving chunking, plus a PDF text-layer
  path with an OCR hook (ADR 0008). Snapshots and processed chunks are
  committed (`corpus/raw/`, `corpus/processed/chunks.jsonl`).
- **Retrieval** (`src/assistant/retrieve.py`): BM25 (`rank_bm25`) with
  hand-built ES→EN and TL→EN query-expansion lexicons, EN synonym folding,
  language boost, agency filtering with per-agency quotas for comparison
  questions. Dense retrieval exists behind `FPA_DENSE` and stays off with
  evidence (ADR 0007).
- **Guards** (`src/assistant/guards.py`): input PII/scope/injection regexes,
  a hedge-aware determination-language detector with sentence-level
  redaction, citation and as-of regexes. Cross-domain safety stays here;
  domain content lives in `src/assistant/domain.py` (`DomainProfile`).
- **Answer** (`src/assistant/answer.py`): guards → retrieve → versioned
  prompts (`prompts/system.txt` v6, `prompts/answer_user.txt` v3 on main) →
  model → output guard with redaction fallback → citation extraction. The
  `AnswerResult` carries the full trace, token counts, retrieval confidence
  band, and the raw blocked answer for eval forensics.
- **Models** (`src/assistant/models.py`): a three-backend Protocol adapter
  (Bedrock default, direct Anthropic, deterministic mock), pinned in
  `src/assistant/config.py` with a per-model price table for cost reporting.
- **Serving** (`web/handler.py`): one Lambda, routes `/`, `/offline`,
  `/embed`, `/version`, `/api/ask`, `/api/feedback`. Layered cost guards
  (body cap, question cap, per-container 8/min budget, LRU answer cache),
  content-free structured logs, strict security headers. Client-held
  multi-turn history, server stores nothing.
- **Evals** (`evals/`): 118 YAML cases in seven suite files
  (`evals/suites/`, including the not-yet-gated `cross_agency.yaml`),
  deterministic checks (`evals/checks.py`), LLM judges on a different model
  (`evals/judges.py`), judge-vs-human calibration (`evals/calibration.py`,
  n=16), a report generator, a retrieval ablation harness, and a GovChat-Eval
  export (`evals/govchat_export.py`) for the independent black-box audit.
  31 run directories are committed under `evals/runs/`.
- **CI** (`.github/workflows/`): lint/type/coverage(90%)/a11y gates, a
  gettext i18n gate (`make i18n`), OIDC-federated smoke evals with fork
  gating, nightly full evals, advisory pa11y, advisory GovChat audit, weekly
  corpus-freshness PR automation, SAST/secret-scan, standards fetch, and
  advisory mutation testing scoped to `evals/checks.py`/`evals/judges.py`.

## What is genuinely strong

- **Enforcement, not just measurement.** The output guard blocks and
  substitutes (`answer.py` returns `answered_guarded` with the raw text
  preserved for forensics); the same rules are re-asserted by
  `evals/checks.py` so a regression fails twice. This double-binding is the
  repo's most defensible design decision.
- **Honesty infrastructure is real, not performative.** The committed 0.040
  lexical groundedness floor in the README, the "band, not a number" framing
  in `docs/model-card.md`, the reverted-prompt-attempt note in
  `docs/ROADMAP.md` P0, and the "dropped after checking the evidence"
  section of the research log are unusual and load-bearing.
- **Reproducibility discipline.** Prompt versions in file headers surfaced
  into every run summary; exact token counts and an estimated USD cost per
  run; a content-hashed corpus version with pin checking (`/version`,
  `assistant/corpus.py`); a sha256 sidecar on the audit dataset.
- **The generalization seam exists.** `DomainProfile` (`domain.py`) plus
  `docs/adapting.md` is a credible "fork one file" story, tested with a
  housing-voucher profile.

## Structural debt and gaps actually observed

1. **Branch divergence is the repo's live integrity risk.** The
   `research-panel-and-roadmap` branch (commit `553ace1`) carries
   `docs/RESEARCH-ROADMAP.md`, `docs/USER-RESEARCH.md`, prompts **v7/v4**
   (RR1 close-the-loop, RR4 positive handoff), new guard/check code, and new
   edge/multilingual cases — but it forked before main's i18n migration,
   mutation testing, and CITATION work, so it cannot fast-forward. Main's
   `EVALS.md` (2026-06-30, live, v6/v3, 113/118, $1.70) is current *for
   main*, but the moment the branch lands, `EVALS.md`, `evals/baseline.json`
   (still dated 2026-06-17), and the GovChat dataset (recorded 2026-06-16
   per `ADDED` in `evals/govchat_export.py`) all go stale simultaneously,
   and nothing in CI would notice. The repo has no mechanism that ties
   published claims to the code version they were measured on (FIX-01).
2. **Judges are blind to conversation context.** `judge_helpfulness` in
   `evals/judges.py` sends only the final-turn question; the committed
   conv-004 failure trace in `EVALS.md` shows the judge inventing an
   explanation for the missing context ("suggests there was likely prior
   context the assistant should have used"). At least one of the five
   headline failures is partly a harness artifact, not a model defect
   (FIX-02).
3. **Calibration labels are unbound to the answers they judged.**
   `evals/calibration.py` matches labels by `case_id` only; when a prompt
   bump changes an answer, an old human label silently grades a new answer
   (FIX-03). RR6 already covers growing the n=16 sample; this is a different,
   structural defect in how labels bind.
4. **The domain profile is frozen at import time.**
   `guards.py:36`, `retrieve.py:22`, and `config.py:29` bind
   `domain.get_profile()` results to module-level constants, so the
   documented `FPA_DOMAIN` switch only works if set before first import —
   an easy silent no-op that undercuts the `adapting.md` promise (FIX-06).
5. **Guard multilingual parity is asserted for outputs but not inputs.** The
   determination detector is EN+ES, but the PII `dob` pattern matches only
   English phrasings ("born on|date of birth|birthday is|dob"), and the
   legal-advice scope pattern is English-only, while the product answers in
   Spanish as a first-class language (FIX-05).
6. **Client-supplied history is prompt-injected as the assistant's own
   words.** `_history_block` in `answer.py` renders `"You answered: …"` from
   whatever the client posts; `web/handler.py` explicitly treats history as
   "context, not a trust boundary." The output guard still polices the new
   answer, but faithfulness under forged history is untested (FIX-08).
7. **The freshness loop is half-built.** `corpus-freshness.yml` diffs the
   whole `corpus/` tree (raw HTML included, so page furniture can trigger
   PRs) and never calls the `diff_corpus` machinery or writes
   `corpus/CHANGELOG.md`, which was seeded expressly for it (FIX-09).
8. **Known-open roadmap items observed still open on main:** no CloudWatch
   alarms/dashboards (P1-3), per-container-only rate limit (P1-4), manual
   a11y walkthrough log empty (`docs/audits/a11y-walkthrough.md`), Title VI
   one-pager (RR8) not present on main and in any case needing counsel
   review, and the GovChat `--judge llm` audit run still uncommitted (RR6).
   These are referenced, not re-proposed, in this folder.

## Strategic position in the portfolio

This repo is the *origin artifact* of the civic-AI family: it was built end
to end first, and GovChat-Eval (the shared audit engine) and
civic-rag-starter-kit are its generalizations
(`docs/audits/methodology.md`). It is also the portfolio's most complete
instantiation of the STANDARDS AI-EVALUATION posture: two-layer evaluation,
judge calibration, cost accounting, corpus versioning. Its highest-leverage
role going forward is therefore not "more transit features" but (a) keeping
its own honesty machinery airtight — the provenance and judge-fidelity fixes
below — and (b) converting what it learned into structures the rest of the
family can inherit (structured fare facts, conformance profiles,
eval-history rendering). Its biggest external tailwind is real: transit
chatbots are deploying with no public accuracy claims (RESEARCH-ROADMAP E11),
and this repo's differentiator is precisely the stamped, inspectable test
story.
