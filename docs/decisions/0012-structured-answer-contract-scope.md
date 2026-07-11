# ADR 0012: structured answer contract — deterministic-parse scope, not a prompt change

Date: 2026-07-08. Status: accepted.

## Decision

EXP-04 (`docs/ideation/03-expansions.md`) is implemented as a deterministic
post-processing layer, not by changing the answer prompt or the model call.
`src/assistant/contract.py::build_structured_answer` parses the existing
prose `AnswerResult` (the same text the guards and citation extraction
already validated) into the typed shape in
`docs/answer-contract.schema.json` — criterion, prices, proof documents, next
step, decision owner, as-of date, citations — and validates it before
`web/handler.py` attaches it to the `/api/ask` response as `structured`. The
existing `answer` field is unchanged and stays the fallback whenever
`structured` is null.

## Why this scope, not the full EXP-04 shape

The ideation entry's own shape section says the model should emit the JSON
directly and calls this "a major prompt+pipeline change: it must ride FIX-01
provenance, FIX-12 cheap runs, and a full live regression cycle." Neither
FIX-01 nor FIX-12 is merged to `main` yet, and validating a prompt change of
this size honestly requires live model calls against the full eval suite,
which this change does not run. Building the parser as a separate,
non-prompt-touching layer gets the field-checkable contract and the sectioned
UI rendering — the two concrete wins EXP-04 is chasing — without touching the
five prompt-bump-tuned behaviors already locked in by the v4/v6/v7 prompt
history, and without needing a live run to know the smoke suite still passes.

## What is deliberately not done here

- **Field completeness is not gated.** `evals/checks.py::run_checks` gains
  `structured_contract_schema_valid`, which asserts the parse produced a
  well-typed payload (the schema permits empty `prices`/`proof_docs`/
  `next_step`/`decision_owner`, by design). It does *not* assert
  `next_step` is always present, or that a price is always listed when a
  fact row matches — those are EXP-04's own excellence bar, and adding them
  as hard gates without a live regression run risks exactly the false eval
  failures the item's own risk note warns about.
- **The model still emits prose.** `answer.py` and the prompt files are
  untouched. A future pass that has FIX-01/FIX-12 merged and budget for a
  live regression cycle can move to model-emitted JSON per the original
  shape, with `assistant.contract.validate_answer_contract` reused as the
  validation step and prose as the parse-failure fallback, exactly as
  written there.
- **Staff mode (RE3) is not built.** The schema and rendering split make it
  a rendering choice once wanted, but no staff view exists yet.

## Consequence

`structured` is additive on the `/api/ask` response — existing clients that
read only `answer`/`citations`/`kind` are unaffected. The demo UI
(`web/index.html`) renders the sectioned view (`renderStructured`) only when
`kind === "answered"` and `structured` is non-null; every other case renders
the prose `answer` exactly as before.
