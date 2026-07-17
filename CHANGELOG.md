# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
does not yet cut tagged releases (see the "Standards conformance" table in
`README.md` for the release-and-versioning declaration), so entries are dated
rather than tied to a published tag.

## [Unreleased]

### Changed
- Hosted completions now expose the SDK's actual served model while retaining
  the requested model for pricing, and eval cache keys use collision-proof
  canonical JSON framing even when prompts contain U+0000.
- Replaced the absolute BM25 `min_confidence` decline threshold with
  normalized, corpus-size-independent retrieval signals
  (`assistant.retrieve.ConfidenceSignals`: a z-score against the full-corpus
  score distribution and query-term coverage), calibrated by the new
  `evals/decline_calibration.py` against a labeled should-answer/
  should-decline question set. See `docs/decisions/0013` (FIX-07).
- Roadmap P1 item 4, "a true rate limit": `infra/deploy.sh` now derives the
  API Gateway stage throttle's rate and burst from the same
  `RESERVED_CONCURRENCY` value used for the Lambda concurrency ceiling, so
  the gateway's cross-container rate limit is documented, tuned, and cannot
  silently drift out of sync with concurrency. `web/handler.py`'s comments
  and docstrings now correctly describe the gateway throttle, not the
  per-container in-memory budget, as the guard that holds across containers.
  New test: `tests/test_deploy_rate_limit.py`. See the 2026-07-08 amendment
  in `docs/decisions/0004-demo-deploy.md`.

### Fixed
- Hosted-model usage and eval cost accounting now follow the reviewed
  portfolio GenAI telemetry contract. Anthropic and Bedrock cache-write/read
  buckets are normalized into canonical input totals once, priced at their
  distinct rates, propagated through answer/judge traces, cache records, run
  summaries, and reports, and exposed as PII-free structured fields. Invalid
  provider counts fail closed; unknown models remain visibly unpriced; an eval
  cache hit now correctly spends zero provider tokens for the current run.
- Restored the `checks`, i18n, and advisory browser-accessibility jobs on pull
  requests after a CI-minutes optimization accidentally made them push-only.
  The committed-report regression check runs on every pull request again.
- Reformatted the codebase with `ruff format` and made it a blocking `make
  verify` / CI gate (was check-only).
- `docs/ROADMAP.md` P2 item 5 (a11y wiring) was stale: it still listed feeding
  transcripts to GovChat-Eval's a11y suite (`transcript_html`) as remaining
  work, but that landed in the same session two commits later
  (`evals.govchat_export.render_transcript`, documented in
  `docs/audits/methodology.md` as "a11y now runs"). Corrected the roadmap to
  reflect that only the manual screen-reader/keyboard walkthrough
  (`docs/audits/a11y-walkthrough.md`, still an unfilled result table) remains
  — that step needs a human at a real assistive-tech session and is not
  something this pass fabricates.

### Added
- NIST AI RMF crosswalk in `docs/procurement-brief.md` (roadmap F-12 /
  research item RR10, 2026-07-17): maps the existing artifacts (guards, eval
  suites, calibration, audits, model card, risk register, freshness loop)
  onto Govern/Map/Measure/Manage with file pointers. Explicitly a
  self-assessment, not a certification; the pending manual accessibility
  walkthrough stays flagged as not covered.
- Tag-triggered release workflow (`.github/workflows/release.yml`, STANDARDS
  conformance REL-14): on a `v*` tag it checks the tag matches
  `pyproject.toml`'s version, re-runs `make verify` at the tagged commit,
  builds sdist+wheel, generates a CycloneDX SBOM, attests SLSA build
  provenance, and creates a GitHub Release with the matching CHANGELOG
  section as notes. No tag has been pushed yet, so the note above ("does not
  yet cut tagged releases") still holds until the first one is.
- **Provenance gate promoted to blocking** (FIX-01/M-2, 2026-07-09): `make
  verify` and CI's `checks` job now run `evals/provenance.py`; the three
  published artifacts declare their **true** generation-time prompt/corpus
  versions (baseline: v5/v2 from the 2026-06-17 run; golden dataset: v4/v2
  from commit `3901855`) and their staleness vs HEAD is acknowledged loudly in
  `evals/stale_acknowledged.json` with written reasons — declared, not
  stamped current. `evals/govchat_export.py` now emits a dataset-level
  `# provenance:` header on every regeneration.
- Root-caused and fixed the multilingual eval regression flagged in the
  2026-06-30 report (18/21 vs the committed 20/21 baseline). The 2026-07-11
  full live run recovered to 20/21 without changing the baseline or gate.
  See `docs/audits/eval-regression-2026-06-30.md`.
- System prompt **v7, live-validated 2026-07-11**: never
  state a fee/payment consequence beyond what the passage supports — targets
  the `ml-015` cross-lingual assertiveness gap. A full live run passed
  `ml-015` and restored multilingual to 20/21; no baseline or threshold was
  changed.
- Documented the **MST Spanish-content parity ceiling** behind `ml-012`
  (2026-07-09): `mst.org/es/fares/benefits/` exists but is an untranslated
  English shell, verified once via the polite fetch pipeline and deliberately
  not ingested. See `docs/I18N.md` and the addendum in
  `docs/audits/eval-regression-2026-06-30.md`.
- Re-recorded the 122-item independent GovChat-Eval dataset under the validated
  v7 prompt and regenerated its report. The advisory deterministic audit still
  reports its known groundedness and multilingual gaps; they remain visible.
- `src/assistant/gtfs.py`: GTFS(-Fares) cross-validation channel (EXP-06).
  `make gtfs-fetch` snapshots MST's and SBMTD's live GTFS static feeds
  (surveyed and confirmed 2026-07-08 — the other three pilot agencies did
  not resolve to a discoverable feed this pass) and `make gtfs-check`
  cross-checks feed fares against the prose corpus, flagging disagreement as
  `feed_agrees: yes|no|no_feed` in `corpus/processed/gtfs_cross_check.json`.
   Never overrides an answer; see `docs/decisions/0011-gtfs-cross-validation.md`
   for the design, the live survey, and the real coverage gap the first run
   found (SBMTD's Downtown-Waterfront Shuttle fare has no citable prose page).
- Structured fare-fact layer (EXP-01, `docs/ideation/03-expansions.md`):
  `src/assistant/facts.py` extracts a typed `FareFact` row (agency, program,
  rider_class, price, age_min/max, source chunk) per price/age figure found
  at ingest, committed as `corpus/processed/facts.jsonl`; a new
  `fare_facts_consistent` deterministic check in `evals/checks.py` verifies
  every `$`-amount and age claim in an answer against a fact row scoped to
  the cited document, instead of relying only on the LLM judge for
  groundedness of numbers.
- Standards conformance declaration table in `README.md`.
- Blocking dependency-vulnerability scan (`pip-audit`) in `security.yml`.
- `CODEOWNERS`, `.python-version`, `.standards-version`, this `CHANGELOG.md`.
- Judge-label staleness binding (`answer_sha256`) in `evals/calibration.py` so
  a prompt bump can't silently score the judge against a stale human label.
- `evals/provenance.py`: a provenance-drift check comparing the prompt/corpus
  versions declared in `EVALS.md`, `evals/baseline.json`, and
  `evals/govchat/golden.jsonl` against `HEAD` (offline tool; not yet wired as
  a blocking CI gate — see the execution log in
  `../audit-2026-07-05/fare-assistant-REMEDIATION.md`).
- `evals/check_report_regression.py`: a merge-blocking check that the
  committed `EVALS.md` scoreboard has not regressed against the committed
  `evals/baseline.json` (closes the gap where a locally-regenerated, gate-failing
  report could be committed without CI ever seeing the failure).
- Agency operator console (`web/console.py`, EXP-09): a small, separately
  authenticated surface (fails closed without `FPA_CONSOLE_TOKEN`) where an
  agency owner can pin a corpus version, review the git-backed changelog/diff
  (`assistant.corpus.version_history`, `make history`), configure the embed
  widget's allowed origins, and read the latest eval report — actions that
  previously meant editing the rider Lambda's environment variables by hand.
  Deployed separately from the rider demo via `infra/deploy-console.sh`, with
  an IAM role scoped to only that one rider function's configuration.

## [0.1.0] - 2026-06-30

Initial reference-implementation milestone referenced by `CITATION.cff`:
five-agency EN/ES corpus, 118-case eval harness (groundedness, refusal,
edge_cases, multilingual, freshness, conversation), gettext-based i18n
catalogs, SHA-pinned CI with blocking SAST/secret-scan, branch-coverage gate,
and the independent GovChat-Eval audit. See `EVALS.md` and `docs/audits/` for
the measured state at this point, and `git log` for the full history (no
tag was created for this milestone; see the Standards conformance table's
RELEASE-AND-VERSIONING row).
