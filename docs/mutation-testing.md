# Mutation testing (advisory)

Coverage tells you a line ran. It does not tell you a test would notice if that
line were wrong. Mutation testing checks the second thing: it makes small edits
to the code (a `>` becomes `>=`, an `and` becomes `or`, a flag is dropped) and
runs the tests. If the tests still pass, that mutant "survived" and marks a gap
in the assertions, not just the coverage.

This repo runs mutation testing on the two modules where a silent bug would
corrupt an eval score or let a bad answer pass:

- `evals/checks.py` — the deterministic per-case gate (citation resolvable,
  correct agency, no-determination language, required facts, refusal + redirect,
  language match). Every check's boolean decides pass or fail for that case.
- `evals/judges.py` — the LLM-as-judge verdict parsing. The load-bearing
  property is that an unparseable or malformed judge output is recorded as
  `passed=None` (errored/skipped), never a silent `True`.

The cross-domain safety guards in `src/assistant/guards.py` (PII, scope,
injection, determination language) are exercised transitively here but are not
the mutation target; they have their own dedicated suites.

## It is advisory, never a merge gate

Mutation score is a diagnostic, not a threshold to enforce per PR. Enforcing it
would make an unrelated change fail because it added a branch, and would push
people toward assertion-padding instead of meaningful tests. So:

- It is not part of `make check` / `make verify`.
- It never runs on `pull_request` or `push`.
- The `.github/workflows/mutation.yml` job runs weekly and on demand only, and
  its mutmut step is `continue-on-error`. The per-PR merge gate stays the
  coverage-gated suite in `ci.yml`.

## Running it

```
make mutation
```

This runs mutmut (installed via the isolated `mutation` dependency group, so the
default dev install stays lean) against the two fast, offline unit suites named
in `[tool.mutmut]` — no network, no model calls, no coverage gate. The scoped
run finishes in well under a minute.

Useful follow-ups:

```
uv run --group mutation mutmut results          # list survivors
uv run --group mutation mutmut show <mutant-id>  # see the exact surviving edit
uv run --group mutation mutmut browse            # interactive TUI
```

mutmut copies the sources into a `mutants/` sandbox and caches results in
`.mutmut-cache`; both are regenerated each run and are gitignored.

## Baseline (2026-06-30)

Scoped to `evals/checks.py` + `evals/judges.py`, run against
`tests/test_checks.py` + `tests/test_judges.py`:

| Metric | Value |
| --- | --- |
| Mutants generated | 334 |
| Killed | 249 |
| Survived | 85 (checks.py 33, judges.py 52) |
| Timeout / suspicious | 0 |
| Mutation score | ~75% (249/334) |

The survivors cluster in two low-risk categories, which is the useful finding:

1. **Diagnostic-string mutations.** Most survivors edit the `detail` message or
   the check/verdict `name` label (for example `"redirect_present"` →
   `"REDIRECT_PRESENT"`, or a `detail` string replaced with `None`). These
   fields are for humans reading a failed trace; they do not change any pass or
   fail decision, so the tests correctly do not assert them. Pinning every one
   would be assertion-padding.
2. **Judge prompt text invisible to a scripted judge.** The judge tests inject a
   canned-response model, so mutations to the exact wording sent to the judge
   (separators, default reasoning strings) are not observable in the verdict.
   The safety-critical behavior — a parse failure becomes `passed=None`, and the
   verdict tracks `bool(data["grounded"])` — is covered, and those mutants die.

Four notable survivors from the first pass were real assertion gaps and now have
tests (they are killed as of this baseline):

- `evals/checks.py`: dropping `re.I` on the **forbidden-content** match made it
  case-sensitive, so a banned phrase could slip past by capitalizing it.
- `evals/checks.py`: dropping `re.I` on the **required-facts** match would fail a
  correct answer that stated the fact in different casing.
- `evals/checks.py`: `scope and result.kind == "answered"` → `scope or ...`
  emitted a `correct_agency_cited` check even when the case named no agency,
  failing answers that never claimed one.
- `evals/judges.py`: setting the groundedness judge's `user` prompt to `None`
  meant the judge scored without seeing the passages or the answer — a silently
  corrupt measurement.

## Reading a new survivor

Run `mutmut results`, then `mutmut show <id>` on anything in `checks.py` or the
verdict logic of `judges.py`. Ask: would this edit change a pass/fail decision or
a recorded verdict? If yes, add the smallest test that asserts the real
behavior. If it only changes a diagnostic string or unobservable prompt text,
leave it — a surviving mutant is a prompt to think, not a number to force to 100.

## Companion: the defect-injection self-test (a merge gate)

Mutation testing asks "would a *unit test* notice if the check code were wrong?"
The self-test (`evals/selftest.py`, `make eval-selftest`) asks the question a
skeptic actually cares about: **"given a deliberately wrong *answer*, does the
gate fail the right case?"**

It takes an otherwise-clean, grounded answer and plants one known defect, then
asserts that the specific check meant to catch it flips from pass to fail:

| Planted defect | Check that must catch it |
|---|---|
| a fare that contradicts the corpus fact table | `fare_facts_consistent` |
| a determination phrase ("you qualify") | `no_determination_language` |
| a dropped / unresolvable citation | `citation_present_and_resolvable` |
| a missing "as of" date | `as_of_disclosure` |
| the wrong agency cited | `correct_agency_cited` |
| an asserted forbidden over-claim | `forbidden_content_absent` |
| a missing required fact | `required_facts_present` |

The clean answer must also pass each check, so a scenario cannot pass by being
broken in two directions at once. Unlike mutation testing, this **is** a merge
gate — it makes no model calls, and `tests/test_selftest.py` runs it in CI. A
defect that survives (the mutated answer still passes its check) is a hole in
the harness, and it turns the run red.

This is the "we test our tests" evidence the evaluation story leans on: the
scoreboard says the assistant passes; the self-test says the scoreboard would
have caught it if it hadn't.
