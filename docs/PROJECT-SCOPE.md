# Project Scope

Last reviewed: 2026-07-08. Base branch: `main`.

This file is a plain-language map of the project as it exists on `main`. It does not replace the README, roadmap, audit docs, or source comments. It points to them so a reviewer can see the whole shape without reading every file first.

## What This Project Is

This repo is a fare-policy assistant for transit riders and agencies. It answers questions from a controlled fare corpus, cites the policy text it used, and carries evaluation material for refusals, language behavior, and source freshness.

Package metadata checked in this pass:

- Python package `fare-policy-assistant` for Python `>=3.12`.

## Who It Serves

- Transit agencies exploring cited fare help for riders.
- Riders and service staff who need plain answers tied back to official policy.
- Engineers comparing deterministic retrieval, model-backed answers, and evaluation gates.

## What It Covers

- Fare corpus files, raw source snapshots, manifests, and processed markdown.
- Retrieval, answer, evaluation, and corpus freshness workflows.
- Docs for adapting the assistant, methodology, audits, and decisions.
- Eval reports, accessibility notes, and i18n coverage.
- A CLI and service-oriented Python package.

## How It Is Put Together

- corpus/ holds raw and processed fare material.
- docs/decisions/ explains retrieval, model adapter, deployment, and ingest choices.
- docs/audits/ holds eval, accessibility, and methodology records.
- The package source contains the assistant, retrieval, provider, and eval code.
- Makefile and workflows run the local gate set.

Observed source and operations surfaces:

- `Makefile`
- `corpus/`
- `evals/`
- `infra/`
- `prompts/`
- `pyproject.toml`
- `src/`
- `tools/`
- `web/`

GitHub workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/corpus-freshness.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/security.yml`
- `.github/workflows/standards.yml`

## Trust Boundaries

- Answers must stay tied to fare sources and should decline when the corpus does not support a claim.
- Fare policies change, so corpus freshness and source dates are part of the product.
- Legal and Title VI language should be reviewed by the agency using it.

## Outside This Scope

- It is not an official fare ruling unless an agency adopts and maintains it.
- It cannot answer outside the loaded corpus with the same confidence.
- Live model and judge runs depend on configured credentials.

## Docs And Evidence Checked

This pass checked 37 hand-authored doc or metadata files, 26 test files, and 5 workflow files on `main`. The count excludes vendored provider licenses, dependency folders, generated cache files, and large generated artifact history.

Large content groups were counted rather than listed file by file:

- `corpus/processed/`: 11 files

Primary docs checked:

- `.github/ISSUE_TEMPLATE/bug-or-feature.md`
- `.github/ISSUE_TEMPLATE/eval-failure.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `CHANGELOG.md`
- `CITATION.cff`
- `CLAUDE.md`
- `CONTRIBUTING.md`
- `EVALS.md`
- `LICENSE`
- `README.md`
- `SECURITY.md`
- `corpus/CHANGELOG.md`
- `docs/I18N.md`
- `docs/ROADMAP.md`
- `docs/adapting.md`
- `docs/audits/a11y-walkthrough.md`
- `docs/audits/eval-regression-2026-06-30.md`
- `docs/audits/eval-report.md`
- `docs/audits/methodology.md`
- `docs/decisions/0001-retrieval-keep-it-simple.md`
- `docs/decisions/0002-corpus-pilot-agencies.md`
- `docs/decisions/0003-model-adapter.md`
- `docs/decisions/0004-demo-deploy.md`
- `docs/decisions/0005-transposed-table-normalization.md`
- `docs/decisions/0006-streaming-deferred.md`
- `docs/decisions/0007-dense-retrieval-stays-off.md`
- `docs/decisions/0008-pdf-ingest.md`
- `docs/model-card.md`
- `docs/mutation-testing.md`
- `docs/procurement-brief.md`
- `docs/research/synthetic-personas-feedback.md`
- `infra/README.md`
- `prompts/answer_user.txt`
- `prompts/judge_groundedness.txt`
- `prompts/judge_helpfulness.txt`
- `prompts/system.txt`
- `web/README.md`

Representative test files checked:

- `tests/conftest.py`
- `tests/test_a11y.py`
- `tests/test_a11y_extra.py`
- `tests/test_answer.py`
- `tests/test_calibration.py`
- `tests/test_check_report_regression.py`
- `tests/test_checks.py`
- `tests/test_cli.py`
- `tests/test_corpus.py`
- `tests/test_domain.py`
- `tests/test_govchat_export.py`
- `tests/test_guards.py`
- `tests/test_i18n.py`
- `tests/test_ingest.py`
- `tests/test_ingest_pipeline.py`
- `tests/test_judges.py`
- `tests/test_models.py`
- `tests/test_pdf_ingest.py`
- `tests/test_provenance.py`
- `tests/test_regression_gate.py`
- `tests/test_report.py`
- `tests/test_report_extra.py`
- `tests/test_retrieval_ablation.py`
- `tests/test_retrieve.py`
- `tests/test_runner.py`
- `tests/test_web.py`

## Validation Notes

For this docs PR, validation means the scope file was generated from the clean `origin/main` worktree, reviewed against repo metadata and docs inventory, and checked with `git diff --check`. Project test suites are still the authority for code behavior, because this PR changes documentation only.
