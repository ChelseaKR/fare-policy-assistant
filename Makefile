# All targets run through uv; `uv sync` happens implicitly via `uv run`.

# Path to a local govchat-eval clone for the independent audit (make audit).
EVAL_HARNESS ?= ../govchat-eval

.PHONY: fetch index ingest eval smoke report audit a11y offline guide history test lint typecheck check verify cov mutation eval-selftest coverage robustness i18n i18n-compile dep-scan deploy-reqs report-regression provenance template gtfs-fetch gtfs-check fares relabel

# The committed relabeling worksheet `make relabel` opens by default.
WORKSHEET ?= evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl

# Package + its in-tree gettext catalogs (INTERNATIONALIZATION-STANDARD §3/§4).
PACKAGE ?= assistant
LOCALES := src/$(PACKAGE)/locales
SUPPORTED_LOCALES := en es tl

# Coverage floor for the first-party packages. Honest achieved is ~95%; the gate
# sits a few points below to absorb Python-version / optional-extra drift in CI.
COV_MIN ?= 90

fetch:        ## Snapshot corpus documents listed in corpus/manifest.yaml
	uv run python -m assistant.ingest fetch

index: ingest
ingest:       ## Clean, chunk, and index the fetched snapshots
	uv run python -m assistant.ingest process

gtfs-fetch:   ## Transactionally capture exact GTFS(-Fares) feeds (ADRs 0011, 0024)
	uv run python -m assistant.gtfs fetch

gtfs-check:   ## Cross-check snapshotted GTFS fares against the prose corpus (EXP-06)
	uv run python -m assistant.gtfs check

fares:        ## Print an agency's authoritative fares from its GTFS-Fares feed (ADR 0017): make fares AGENCY=SBMTD
	uv run python -m assistant.fare_table $(AGENCY)

smoke:        ## CI smoke suite (26 cases, deterministic checks only unless key present)
	uv run python -m evals.runner --smoke

eval:         ## Full eval run; writes evals/runs/<timestamp>/ and regenerates EVALS.md
	uv run python -m evals.runner --full

report:       ## Regenerate EVALS.md + HTML from the latest run, and the eval-history trend page
	uv run python -m evals.report
	uv run python -m evals.history

a11y:         ## Structural accessibility gate on every public page (WCAG 2.2 AA, static subset)
	uv run python -m web.a11y

offline:      ## Render the offline fare reference (web/offline.html) from the corpus
	uv run python -m web.offline

guide:        ## Render the guided fare finder (web/guide.html): zero-model-call, no input fields
	uv run python -m web.guide

template:     ## Extract the domain-agnostic skeleton to TARGET (see template/MANIFEST.yaml, docs/ROADMAP.md P3-5)
	@test -n "$(TARGET)" || { echo "usage: make template TARGET=/path/to/new-domain-assistant"; exit 2; }
	uv run python -m scripts.extract_template "$(TARGET)"

history:      ## Regenerate corpus/version_history.json (git-backed changelog for the operator console, EXP-09)
	uv run python -m assistant.corpus history > corpus/version_history.json

audit:        ## Independent GovChat-Eval audit: record answers, then run the external harness
	@test -d "$(EVAL_HARNESS)" || { echo "govchat-eval not found at $(EVAL_HARNESS); set EVAL_HARNESS=<path>"; exit 2; }
	uv run python -m evals.govchat_export
	cd "$(EVAL_HARNESS)" && uv run govchat-eval validate --dataset "$(CURDIR)/evals/govchat/golden.jsonl"
	cd "$(EVAL_HARNESS)" && uv run govchat-eval run \
		--config "$(CURDIR)/evals/govchat/govchat-eval.toml" \
		--dataset "$(CURDIR)/evals/govchat/golden.jsonl" \
		--baseline "$(CURDIR)/evals/govchat/baseline.json" \
		--out "$(CURDIR)/docs/audits"

test:         ## Run the unit suite with the branch-coverage gate (offline; no paid calls)
	uv run pytest -q --cov=assistant --cov=web --cov=evals --cov-branch \
		--cov-report=term-missing --cov-fail-under=$(COV_MIN)

cov: test     ## Alias for the coverage-gated test run

lint:
	uv run ruff check src tests evals web scripts
	uv run ruff format --check src tests evals web scripts

typecheck:
	uv run mypy src web scripts

check: lint typecheck test

dep-scan:     ## Dependency-vulnerability scan over the locked deps (pip-audit; needs network — not part of `verify`)
	uv sync --frozen --all-groups
	uv export --frozen --no-emit-project --all-groups --format requirements-txt -o /tmp/requirements-audit.txt
	uv run --with pip-audit pip-audit --strict --desc -r /tmp/requirements-audit.txt

deploy-reqs:  ## Regenerate infra/requirements-deploy.txt (hash-pinned rider deploy bundle, M-7/P1-6) from uv.lock
	uv export --frozen --no-dev --no-emit-project --format requirements-txt -o infra/requirements-deploy.txt

provenance:   ## BLOCKING: EVALS.md/baseline.json/golden.jsonl must declare the prompt+corpus versions HEAD ships, or carry a documented waiver in evals/stale_acknowledged.json (promoted from advisory 2026-07-09, FIX-01/M-2 — a baseline may legitimately lag HEAD, but only loudly)
	uv run python -m evals.provenance

i18n:         ## i18n catalog gate: POT current + supported-language parity + PO compiles + BCP-47
	# G2-lite -- regenerate the extraction template and fail if it drifts from the
	# committed one (a new/changed rider-facing string without a re-extract is a
	# merge-blocker). The normalizer freezes volatile header/flag noise so this is
	# a meaningful diff, not a flaky timestamp check. Local == CI.
	uv run python -m babel.messages.frontend extract -F babel.cfg --no-location \
		--sort-output --project=fare-policy-assistant --version=0.1.0 \
		-o $(LOCALES)/messages.pot src/
	uv run python tools/i18n_normalize_pot.py $(LOCALES)/messages.pot
	git diff --exit-code -- $(LOCALES)/messages.pot
	# G7 -- every PO compiles cleanly (format + domain checks), no msgfmt errors.
	@for locale in $(SUPPORTED_LOCALES); do \
		msgfmt --check --check-format --check-domain -o /dev/null \
			$(LOCALES)/$$locale/LC_MESSAGES/messages.po || exit 1; \
	done
	# G6 supported-language key-parity + G5 completeness/placeholder parity.
	uv run python tools/check_catalog_parity.py
	# G3 -- BCP 47 / RFC 5646 validity of every authored locale tag.
	uv run python tools/check_bcp47.py
	@echo "i18n: POT current; supported-language parity + completeness; PO compiles; BCP-47 valid."

i18n-compile: ## Compile the committed PO catalogs to MO (run after editing a .po)
	@for locale in $(SUPPORTED_LOCALES); do \
		msgfmt -o $(LOCALES)/$$locale/LC_MESSAGES/messages.mo \
			$(LOCALES)/$$locale/LC_MESSAGES/messages.po || exit 1; \
	done
	@echo "i18n-compile: refreshed messages.mo for $(SUPPORTED_LOCALES)."

verify: check i18n a11y report-regression provenance  ## Full offline gate = the exact CI `checks`+`i18n` gate set: lint + format + typecheck + coverage-gated tests + a11y + i18n + committed-report regression + provenance gate

report-regression:  ## Committed EVALS.md must not regress vs evals/baseline.json (see docs/audits/eval-regression-2026-06-30.md)
	uv run python -m evals.check_report_regression

mutation:     ## ADVISORY mutation testing on the core scoring logic (offline; never a merge gate)
	# Scoped in [tool.mutmut] to evals/checks.py + evals/judges.py, run against
	# the two fast offline unit suites. Not part of `check`/`verify` and never a
	# per-PR gate; run it deliberately. See docs/mutation-testing.md for the
	# baseline (~75% killed) and how to read survivors.
	uv run --group mutation mutmut run
	uv run --group mutation mutmut results

eval-selftest:  ## Plant known defects into clean answers and prove the deterministic gate catches each (offline; also enforced by tests/test_selftest.py in CI)
	uv run python -m evals.selftest

relabel:      ## Label the judge-calibration worksheet one row at a time (offline; shows each row's evidence, never proposes a verdict): make relabel [WORKSHEET=<path>]
	uv run python -m evals.calibration --review "$(WORKSHEET)"

coverage:     ## Agency x program coverage matrix + corpus blind spots; --write regenerates docs/eval-coverage.md
	uv run python -m evals.coverage --write

robustness:   ## Pass-rate 95% CIs + leave-one-suite-out jackknife over the latest run; --write regenerates docs/eval-robustness.md
	uv run python -m evals.robustness --write
