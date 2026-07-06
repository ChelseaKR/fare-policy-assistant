# Contributing

Thanks for looking at this project. It is a reference implementation of a
narrow, evaluated civic assistant, so contributions are judged less by "does it
add a feature" and more by "does it keep the thing honest and tested." Read this
once before opening a pull request.

## The hard rules never bend

Everything in [`CLAUDE.md`](CLAUDE.md) binds every change:

- The assistant never determines anyone's eligibility. It explains published
  criteria and routes the decision to the agency.
- Every answer cites a dated corpus passage. An uncited answer is a bug.
- No personal information is collected, echoed, or logged.
- No medical, legal, or immigration advice.
- The corpus is versioned and dated; answers disclose the snapshot date.

These are enforced in `src/assistant/guards.py` and asserted by the eval suites.
A change that relaxes any of them will not be merged.

## Local setup

Requires [uv](https://docs.astral.sh/uv/). The corpus snapshots are committed, so
the offline path needs no API key and no network:

```sh
make verify                                # the full offline gate: lint + format + typecheck + coverage-gated test + i18n
uv run python -m evals.runner --offline    # full eval, deterministic checks only
uv run python -m assistant.cli --offline "What proof do I need for the veteran fare on MST?"
```

`make verify` is exactly the AUTO-GATE set CI runs (see the portfolio-wide
[CI/CD standard](https://github.com/ChelseaKR/portfolio-standards/blob/main/CI-CD-STANDARD.md)'s
`make verify` parity requirement) — if it is green locally, the mechanical part
of CI will be too. `make test` alone (ruff + mypy + pytest, no i18n) is a
faster inner loop while iterating, but is not the full gate.

A live run (real answer and judge models) needs AWS Bedrock or an Anthropic key;
see the README. You do not need a live run for most changes, but you do for any
change to prompts, retrieval, or answer behavior (see below).

This repo's cross-cutting rigor (coverage floors, SAST/secret-scan gates,
accessibility, i18n, AI-eval calibration, and the rest) is defined once in
[`ChelseaKR/portfolio-standards`](https://github.com/ChelseaKR/portfolio-standards)
and referenced, not repeated, here (`standards.yml` pins and freshness-checks
the exact version this repo was last measured against). This repo's own
conformance declaration is the "Standards conformance" table in
[`README.md`](README.md#standards-conformance).

## What "done" means

- `make verify` passes: ruff clean, ruff format clean, mypy clean, pytest green
  (branch-coverage gate), i18n catalog gate green.
- New deterministic behavior has a unit test. New rider-facing behavior has an
  eval case in `evals/suites/*.yaml` written against a real corpus passage.
- The offline eval still runs (the regression gate is skipped offline; that is
  expected).

## Prompt, retrieval, and answer changes need a live eval

This is the project's whole thesis, so it is strict: do not tune a prompt or
retrieval by intuition. A change to `prompts/`, `src/assistant/retrieve.py`, or
the answer pipeline must:

1. Name the eval case(s) it targets in the PR description.
2. Bump the prompt version header (first line of the prompt file) with a one-line
   rationale citing those cases, as the existing headers do.
3. Be validated with a live `make eval` and a green regression gate. The harness
   trips on a drop of two or more cases in any suite. A prior prompt attempt that
   improved one case and regressed others was reverted; that is the bar.

If you cannot run a live eval, say so in the PR and mark the prompt header
`NOT YET LIVE-VALIDATED`; a maintainer with credentials will run it before merge.

## Good first issues

The open eval-failure issues (`[eval]` prefix) are the best starting points: each
names a case id, the failing check, and a proposed remediation. The generation
and judge-strictness failures need a live eval to validate; the documentation and
UI items do not.

## Style

Prose in the README, model card, UI copy, and report follows the writing notes in
`CLAUDE.md`: plain and concrete, almost no em dashes, no hype, no rule-of-three.
Code matches the surrounding file: type hints, small functions, comments that
explain why rather than what.
