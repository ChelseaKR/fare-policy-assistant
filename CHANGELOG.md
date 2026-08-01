# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
does not yet cut tagged releases (see the "Standards conformance" table in
`README.md` for the release-and-versioning declaration), so entries are dated
rather than tied to a published tag.

## [Unreleased]

### Security
- Update the locked Pillow dependency from 12.2.0 to 12.3.0, clearing the
  image-decoder advisory cluster reported against PDF/OCR support.
- Update the locked pypdf dependency from 6.13.3 to 6.14.2, clearing four
  malformed-document denial-of-service advisories in optional PDF ingestion.
- Guard the current question and client-held prior questions before history
  parsing or cache access; cache only successful answers under process-local
  keyed digests, never plaintext rider text or refused/guarded payloads.
- Recognize compact, spaced, and hyphenated SSN, phone, and Medicare identifier
  formats before retrieval/model use. Only successful supported answers enter
  browser follow-up history, and signed turns are bound to the corpus version
  and disabled-source state so stale policy context cannot be replayed.
- Fail closed when a citation tag is malformed or names a document outside the
  exact retrieved evidence set; single and combined citation tags now share one
  grammar through enforcement, structured extraction, and public rendering.
  Production contains the expired `yolobus-fares` snapshot through an
  operator-visible source kill switch.

### Changed
- CI persists the content-keyed answer/judge cache between runs, so a pull
  request that cannot change an answer no longer re-buys the smoke suite's
  model calls, and a merge to `main` no longer re-scores the tree its own pull
  request scored minutes earlier. Six of seven nightlies are served from that
  cache; the seventh runs cold under the new `--refresh-cache` flag, which
  re-measures the provider and rewrites the stored answers so the next cached
  night cannot republish results the cold run contradicted. The generated
  report names how many calls a run reused, so a near-zero cost line reads as a
  reused result rather than a broken meter. Suite composition, the regression
  gate, the parity gate, and the deterministic checks are unchanged; what is
  traded is provider-drift detection on six nights out of seven. See
  `docs/decisions/0022-persisted-eval-cache-and-weekly-cold-run.md`.

### Added
- Layered corpus identity and source-complete archives: a full
  `content_version` now covers every behavior-relevant stored chunk field and
  order while excluding observation date; `snapshot_version` adds verified
  URL/status/format/date/raw-digest evidence. Schema-2 archives retain exact
  raw bytes and receipts, validate every artifact in a hidden stage, and publish
  atomically before the live chunk index can change. Processing and archival
  share one validated in-memory source capture, and legacy compatibility
  archives now use the same staged, validated, immutable publication discipline.
  Git attributes preserve the exact evidence bytes across add/checkout instead
  of applying line-ending conversion. The existing `corpus_version` and
  deployment pin remain compatible during the additive rollout.
- Production smoke coverage for the separate evidence and assistant origins,
  every public GET route, security headers, PII refusal, corpus pin/source
  containment (including active Yolobus refusal and static-page removal), and a
  paid-path dated/cited answer.
- A phased improvement and expansion plan, including a fail-soft advisory
  integration contract for independently verified GTFS Scorecard artifacts.
- Privacy-safe production observability with Lambda-owned request correlation,
  canonical GenAI model/token/duration fields, token-derived estimated cost,
  explicit unpriced-call alarms, real request/model latency metrics, and an
  updated CloudWatch dashboard. A paid cache-bypassing check now captures the
  numbered candidate's actual JSON log tail, rejects content/request metadata,
  and proves the installed metric-filter grammar before `live` can move.
- Bilingual parity gate (2026-07-17, roadmap M-1; audit P1-1; AIEV-10/11,
  I18N-22). A live run now fails when the Spanish-vs-mirrored-English pass
  delta exceeds 5 points on 2 or more cases (`evals/runner.py::check_parity`),
  and `evals/check_report_regression.py` re-applies the same gate to the
  committed `EVALS.md` on every PR, reading the machine-readable `parity`
  payload or, for reports generated before this change, the rendered Spanish
  parity table. The general per-suite form (no gated suite more than 5 points
  below the macro pass rate) gates alongside it, with one loud escape hatch:
  `evals/expected_below_macro.json`, a committed suite-to-rationale map whose
  annotations render in the report (the `conversation` suite carries the first
  entry, citing its two documented forged-history failures). Stretch-language
  suites stay outside the gate per the existing P3-3 promise. `EVALS.md` will
  render the parity delta line from the next live run onward; the committed
  report is unchanged here because reports are only ever regenerated from real
  runs.

### Changed
- Public surfaces now distinguish the evaluation evidence hub from the AWS
  assistant, describe dated snapshots and bounded transient processing
  precisely, render prose rather than experimental structured cards, and give
  programmatically focused answer regions a visible focus treatment.
- Iterative deploys now fail closed when existing Lambda configuration cannot be
  read, preserve unrelated operator variables and the history-signing key,
  validate disabled source IDs, capture the current code/configuration as a
  private rollback artifact, apply and verify containment before code, and count
  guarded Bedrock calls in the spend alarm.
- Nightly evaluation evidence uploads even when the evaluator fails, preserving
  the partial report and traces needed to diagnose a red release gate.
- Release authorization now runs from reviewed `main` through the immutable
  portfolio authorizer, verifies and builds the exact selected commit, and
  hands only distributions, SBOM, and notes to a checkout-free publisher that
  rechecks the tag object.
- Hash-pinned rider deploy bundle (roadmap M-7 / audit P1-6, 2026-07-17):
  `infra/deploy.sh` now installs only from `infra/requirements-deploy.txt`
  (a `uv export` of the locked runtime set) with `--require-hashes`, so the
  deployed artifact carries exactly the dependency versions the test suite
  ran against. The loose ranges it used before really did drift: the locked
  numpy publishes no manylinux2014 wheels, so the old install silently
  deployed an older numpy; the bundle now targets `aarch64-manylinux_2_28`,
  which the python3.12 Lambda runtime (Amazon Linux 2023, glibc 2.34)
  supports. Regenerate with `make deploy-reqs`;
  `tests/test_deploy_requirements.py` holds the pin file, the script, and
  `uv.lock` in lockstep. The operator console bundle
  (`infra/deploy-console.sh`) is not covered and still installs from loose
  ranges.
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
- Make the rider Lambda ZIP byte-reproducible across rebuilds by sorting archive
  paths and normalizing timestamps, file modes, and ZIP metadata. An unchanged
  reviewed revision now reuses its exact numbered Lambda version instead of
  publishing a duplicate because dependency-install mtimes changed. Unused
  dependency console entry points are omitted because their generated shebangs
  embed the builder's absolute virtual-environment path.
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
