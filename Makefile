# All targets run through uv; `uv sync` happens implicitly via `uv run`.

# Path to a local govchat-eval clone for the independent audit (make audit).
EVAL_HARNESS ?= ../govchat-eval

.PHONY: fetch index ingest eval smoke report audit a11y offline test lint typecheck check verify cov

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
	uv run ruff check src tests evals web

typecheck:
	uv run mypy src web

check: lint typecheck test

verify: check  ## Full offline gate: lint + typecheck + coverage-gated tests
