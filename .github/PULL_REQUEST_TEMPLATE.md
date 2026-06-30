<!-- Keep this short. The eval report and tests are the real evidence. -->

## What this changes

## Why

## Checklist
- [ ] `make test` passes (ruff, mypy, pytest).
- [ ] New deterministic behavior has a unit test; new rider-facing behavior has
      an eval case written against a real corpus passage.
- [ ] The hard rules in `CLAUDE.md` still hold (no eligibility determination,
      every answer cited, no PII collected or logged, corpus dated).

## If this touches prompts, retrieval, or answer behavior
- [ ] Named the eval case(s) this targets.
- [ ] Bumped the prompt version header with a rationale citing those cases.
- [ ] Ran a live `make eval` with a green regression gate — or marked the prompt
      header `NOT YET LIVE-VALIDATED` and flagged that a maintainer must run it.

## Refs
<!-- Closes #... -->
