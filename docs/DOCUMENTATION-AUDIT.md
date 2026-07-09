# Documentation Audit

Last reviewed: 2026-07-08. Base branch: `main`.

This audit records the documentation sweep and remediation loop for this repository. It checks the docs as a system: entry points, root-level process and legal files, project scope, setup and validation notes, safety and privacy posture, architecture and planning docs, local links, and the places where code, tests, workflows, and docs meet.

## Audit Results

| Area | Result | Evidence |
| --- | --- | --- |
| Entry docs | pass | `README.md` present |
| Security/process docs | pass | CONTRIBUTING.md, SECURITY.md, CHANGELOG.md |
| Architecture/planning docs | pass | 0 architecture/interface docs; 2 planning/research docs |
| Safety/privacy/audit docs | pass | 6 safety/privacy/accessibility/audit docs |
| Validation surface | pass | 25 test files; 5 workflow files |
| Local doc links | pass | 63 authored-doc links checked; 0 unresolved |

## Root-Level Documentation Audit

This section covers hand-authored documentation at the repository root and root-adjacent GitHub templates. It is separate from the `docs/` inventory so README, process, legal, release, and project-specific root files do not get hidden inside the larger docs tree.

| Surface | Result | Evidence |
| --- | --- | --- |
| Root README | pass | Present: `README.md` |
| Root process docs | pass | Present: `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` |
| Root legal, citation, and conduct docs | pass | Present: `LICENSE`, `NOTICE`, `CITATION.cff`, `CODE_OF_CONDUCT.md` |
| Other root project docs | info | `CLAUDE.md`, `EVALS.md` |
| Root-adjacent GitHub templates | pass | `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/bug-or-feature.md`, `.github/ISSUE_TEMPLATE/eval-failure.md` |
| Root/template doc links | pass | 18 root-level/template links checked; 0 unresolved |

Root-level files checked:

- `CHANGELOG.md`
- `CITATION.cff`
- `CLAUDE.md`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `EVALS.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`

Root-adjacent template files checked:

- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/ISSUE_TEMPLATE/bug-or-feature.md`
- `.github/ISSUE_TEMPLATE/eval-failure.md`

## Remediation In This PR

- Added missing root-level remediation docs found by the audit loop, including legal, conduct, contribution, or security files where absent.
- Added `docs/PROJECT-SCOPE.md` as the plain-language project and boundary map.
- Added this audit record so future doc changes have a dated baseline.
- Added or refreshed the docs index so scope, audit, and primary docs are easy to find.
- Fixed or added root/doc remediation files: `CODE_OF_CONDUCT.md`, `NOTICE`, `docs/I18N.md`.

## Repo Surfaces Checked

Package and workspace metadata:

- Python package `fare-policy-assistant` (>=3.12).

Source and operations surfaces seen at the repo root:

- `evals/`
- `infra/`
- `Makefile`
- `pyproject.toml`
- `src/`
- `tests/`
- `tools/`
- `uv.lock`
- `web/`

Workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/corpus-freshness.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/security.yml`
- `.github/workflows/standards.yml`

## Documentation Inventory

| Category | Count | Representative files |
| --- | ---: | --- |
| architecture and interfaces | 0 |  |
| entry points and repo process | 11 | `.github/ISSUE_TEMPLATE/bug-or-feature.md`, `.github/ISSUE_TEMPLATE/eval-failure.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `CHANGELOG.md`, `CITATION.cff`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, plus 3 more |
| operations and release | 1 | `docs/decisions/0004-demo-deploy.md` |
| other docs | 34 | `CLAUDE.md`, `CODEOWNERS`, `EVALS.md`, `corpus/CHANGELOG.md`, `corpus/processed/hta-fares.md`, `corpus/processed/mst-fares-benefits.md`, `corpus/processed/mst-fares-es.md`, `corpus/processed/mst-fares.md`, plus 26 more |
| planning and research | 2 | `docs/ROADMAP.md`, `docs/research/synthetic-personas-feedback.md` |
| safety, privacy, accessibility, and audits | 6 | `docs/DOCUMENTATION-AUDIT.md`, `docs/audits/a11y-walkthrough.md`, `docs/audits/eval-regression-2026-06-30.md`, `docs/audits/eval-report.md`, `docs/audits/methodology.md`, `docs/model-card.md` |
| grouped generated/source content | 11 | `corpus/raw/` counted as a content group, not listed file by file |

Full hand-authored doc inventory checked by this pass:

- `.github/ISSUE_TEMPLATE/bug-or-feature.md`
- `.github/ISSUE_TEMPLATE/eval-failure.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `CHANGELOG.md`
- `CITATION.cff`
- `CLAUDE.md`
- `CODEOWNERS`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `EVALS.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`
- `corpus/CHANGELOG.md`
- `corpus/processed/hta-fares.md`
- `corpus/processed/mst-fares-benefits.md`
- `corpus/processed/mst-fares-es.md`
- `corpus/processed/mst-fares.md`
- `corpus/processed/mst-veterans-resource.md`
- `corpus/processed/sacrt-fares.md`
- `corpus/processed/sbmtd-farechange.md`
- `corpus/processed/sbmtd-fares-passes.md`
- `corpus/processed/yolobus-fares.md`
- `corpus/processed/yolobus-purchasing.md`
- `corpus/processed/yolobus-reduced-fare-id.md`
- `docs/DOCUMENTATION-AUDIT.md`
- `docs/I18N.md`
- `docs/PROJECT-SCOPE.md`
- `docs/README.md`
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

Grouped content counts:

- `corpus/raw/`: 11 files

## Link Check

- Checked 63 local links in authored Markdown and MDX docs.
- Unresolved authored-doc links after remediation: 0.
- Root-level/template unresolved links after remediation: 0.

Audit scope notes:

- Generated sites, deployed app routes, raw third-party HTML captures, and golden fixture websites were inventoried as product or data surfaces but excluded from authored-doc link failure counts.
- Grouped content directories are counted so they stay visible without making the audit readable without hiding them.

## Validation Notes

- The audit was generated from a clean worktree based on `origin/main` for this PR branch.
- Ran a local relative-link check over hand-authored Markdown and MDX docs.
- Ran an explicit root-level documentation presence and link check for README, process, legal, project, and template docs.
- Ran `git diff --check` across the PR worktrees after remediation.
- Product test suites remain the authority for runtime behavior; this PR changes documentation only.
