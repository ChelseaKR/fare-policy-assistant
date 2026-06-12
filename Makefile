# All targets run through uv; `uv sync` happens implicitly via `uv run`.

.PHONY: fetch index ingest eval smoke report test lint typecheck check

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

test:
	uv run pytest -q

lint:
	uv run ruff check src tests evals

typecheck:
	uv run mypy src

check: lint typecheck test
