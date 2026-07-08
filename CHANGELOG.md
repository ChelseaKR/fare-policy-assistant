# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
does not yet cut tagged releases (see the "Standards conformance" table in
`README.md` for the release-and-versioning declaration), so entries are dated
rather than tied to a published tag.

## [Unreleased]

### Changed
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
- Corrected the multilingual eval regression flagged in the 2026-06-30 report
  (18/21 vs the committed 20/21 baseline); see
  `docs/audits/eval-regression-2026-06-30.md` for the root-cause writeup and
  current status.
- Reformatted the codebase with `ruff format` and made it a blocking `make
  verify` / CI gate (was check-only).

### Added
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

## [0.1.0] - 2026-06-30

Initial reference-implementation milestone referenced by `CITATION.cff`:
five-agency EN/ES corpus, 118-case eval harness (groundedness, refusal,
edge_cases, multilingual, freshness, conversation), gettext-based i18n
catalogs, SHA-pinned CI with blocking SAST/secret-scan, branch-coverage gate,
and the independent GovChat-Eval audit. See `EVALS.md` and `docs/audits/` for
the measured state at this point, and `git log` for the full history (no
tag was created for this milestone; see the Standards conformance table's
RELEASE-AND-VERSIONING row).
