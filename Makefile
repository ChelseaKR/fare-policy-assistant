# All targets run through uv; `uv sync` happens implicitly via `uv run`.

# Path to a local govchat-eval clone for the independent audit (make audit).
EVAL_HARNESS ?= ../govchat-eval

.PHONY: fetch index ingest eval smoke report audit test lint typecheck check

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

audit:        ## Independent GovChat-Eval audit: record answers, then run the external harness
	@test -d "$(EVAL_HARNESS)" || { echo "govchat-eval not found at $(EVAL_HARNESS); set EVAL_HARNESS=<path>"; exit 2; }
	uv run python -m evals.govchat_export
	cd "$(EVAL_HARNESS)" && uv run govchat-eval validate --dataset "$(CURDIR)/evals/govchat/golden.jsonl"
	cd "$(EVAL_HARNESS)" && uv run govchat-eval run \
		--config "$(CURDIR)/evals/govchat/govchat-eval.toml" \
		--dataset "$(CURDIR)/evals/govchat/golden.jsonl" \
		--baseline "$(CURDIR)/evals/govchat/baseline.json" \
		--out "$(CURDIR)/docs/audits"

test:
	uv run pytest -q

lint:
	uv run ruff check src tests evals web

typecheck:
	uv run mypy src web

check: lint typecheck test
