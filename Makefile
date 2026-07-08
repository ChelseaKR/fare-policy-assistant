# All targets run through uv; `uv sync` happens implicitly via `uv run`.

# Path to a local govchat-eval clone for the independent audit (make audit).
EVAL_HARNESS ?= ../govchat-eval

.PHONY: fetch index ingest eval smoke report audit a11y offline test lint typecheck check verify cov mutation i18n i18n-compile dep-scan report-regression provenance template

# Package + its in-tree gettext catalogs (INTERNATIONALIZATION-STANDARD §3/§4).
PACKAGE ?= assistant
LOCALES := src/$(PACKAGE)/locales

# Coverage floor for the first-party packages. Honest achieved is ~95%; the gate
# sits a few points below to absorb Python-version / optional-extra drift in CI.
COV_MIN ?= 90

fetch:        ## Snapshot corpus documents listed in corpus/manifest.yaml
	uv run python -m assistant.ingest fetch

index: ingest
ingest:       ## Clean, chunk, and index the fetched snapshots
	uv run python -m assistant.ingest process

smoke:        ## CI smoke suite (25 cases, deterministic checks only unless key present)
	uv run python -m evals.runner --smoke

eval:         ## Full eval run; writes evals/runs/<timestamp>/ and regenerates EVALS.md
	uv run python -m evals.runner --full

report:       ## Regenerate EVALS.md + HTML from the latest run
	uv run python -m evals.report

a11y:         ## Structural accessibility gate on the demo page (WCAG 2.2 AA, static subset)
	uv run python -m web.a11y

offline:      ## Render the offline fare reference (web/offline.html) from the corpus
	uv run python -m web.offline

template:     ## Extract the domain-agnostic skeleton to TARGET (see template/MANIFEST.yaml, docs/ROADMAP.md P3-5)
	@test -n "$(TARGET)" || { echo "usage: make template TARGET=/path/to/new-domain-assistant"; exit 2; }
	uv run python -m scripts.extract_template "$(TARGET)"

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

provenance:   ## ADVISORY: do EVALS.md/baseline.json/golden.jsonl declare the prompt+corpus versions HEAD actually ships? (evals/provenance.py; not yet a blocking gate — see 2026-07-05 execution log for why)
	uv run python -m evals.provenance

i18n:         ## i18n catalog gate: POT current + EN/ES parity + PO compiles + BCP-47
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
	msgfmt --check --check-format --check-domain -o /dev/null \
		$(LOCALES)/en/LC_MESSAGES/messages.po
	msgfmt --check --check-format --check-domain -o /dev/null \
		$(LOCALES)/es/LC_MESSAGES/messages.po
	# G6 EN/ES key-parity + G5 completeness/placeholder parity.
	uv run python tools/check_catalog_parity.py
	# G3 -- BCP 47 / RFC 5646 validity of every authored locale tag.
	uv run python tools/check_bcp47.py
	@echo "i18n: POT current; EN/ES key-parity + completeness; PO compiles; BCP-47 valid."

i18n-compile: ## Compile the committed PO catalogs to MO (run after editing a .po)
	msgfmt -o $(LOCALES)/en/LC_MESSAGES/messages.mo $(LOCALES)/en/LC_MESSAGES/messages.po
	msgfmt -o $(LOCALES)/es/LC_MESSAGES/messages.mo $(LOCALES)/es/LC_MESSAGES/messages.po
	@echo "i18n-compile: refreshed messages.mo for en, es."

verify: check i18n a11y report-regression  ## Full offline gate = the exact CI `checks`+`i18n` gate set: lint + format + typecheck + coverage-gated tests + a11y + i18n + committed-report regression check

report-regression:  ## Committed EVALS.md must not regress vs evals/baseline.json (see docs/audits/eval-regression-2026-06-30.md)
	uv run python -m evals.check_report_regression

mutation:     ## ADVISORY mutation testing on the core scoring logic (offline; never a merge gate)
	# Scoped in [tool.mutmut] to evals/checks.py + evals/judges.py, run against
	# the two fast offline unit suites. Not part of `check`/`verify` and never a
	# per-PR gate; run it deliberately. See docs/mutation-testing.md for the
	# baseline (~75% killed) and how to read survivors.
	uv run --group mutation mutmut run
	uv run --group mutation mutmut results
