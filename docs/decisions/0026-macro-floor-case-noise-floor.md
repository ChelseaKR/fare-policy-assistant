# 0026 — A noise floor for the below-macro gate

Date: 2026-08-15. Status: accepted. Amends the below-macro form of the
bilingual parity gate (M-1 / AIEV-10) introduced alongside ADR 0023's promotion
evidence.

## Context

The parity gate has two forms in `evals/runner.py`. The bilingual form compares
Spanish cases against their mirrored English cases. The below-macro form is its
generalisation: every gated suite's pass rate must sit within 5 points of the
unweighted mean over gated suites.

The two forms were not written with the same guard. `parity_regressed` fails
only when the gap exceeds 5 points **and** at least two mirrored cases diverge,
and it says why in its own docstring: "one flipped pair out of 22 is 4.5 points
and 1 case — noise, not an equity finding." `suites_below_macro` had the
percentage condition alone.

On the full 385-case suite that difference is invisible, because a suite holds
21 to 124 cases and one case moves it by one to five points. On the 26-case CI
smoke subset it is the whole gate. The smoke subset's five gated suites hold 4
to 6 cases each:

| suite | smoke cases | one failure costs |
| --- | --- | --- |
| freshness | 4 | 25.0 points |
| edge_cases | 5 | 20.0 points |
| groundedness | 5 | 20.0 points |
| multilingual | 6 | 16.7 points |
| refusal | 6 | 16.7 points |

Take the best case: every smoke case passes except one freshness case. The
macro is 95%, the floor is 90%, freshness is at 75%, and the build fails. There
is no arrangement of 25 passes and 1 failure that clears a 5-point floor at
these suite sizes, so the smoke gate demanded a perfect run — from an
LLM-judged evaluation whose own docstrings elsewhere describe single-case flips
as noise. A gate nobody can satisfy is not a standard; it is an outage waiting
to be waived.

## Decision

Give the below-macro form the same two-condition shape its sibling has always
had. A suite is an offender when it is below the floor **and** at least
`MACRO_CASE_FLOOR` (2) cases short of reaching it, where the shortfall is
computed on the suite's own denominator:

    cases_short = ceil(floor% * total / 100) - passed

The floor therefore scales with what one case is worth in that suite, rather
than assuming a suite size. One failed case in a four-case suite is absorbed;
two are not. The gate message now names the shortfall in cases, so the reader
can see which of the two conditions carried it.

## Consequences

The finding on a genuinely broken suite is unchanged. On the 2026-08-15 nightly
`cross_agency` sat at 12/21 against a 73.8% floor — four cases short — and
`freshness` at 19/30, also four short. Both remain offenders, and
`tests/test_parity_gate.py` pins that at their real sizes so a future
adjustment to the floor cannot quietly rescue them.

The `conversation` annotation in `evals/expected_below_macro.json` is deleted.
It covered 8/10 against an 89.0% floor: one case short, which is now inside the
noise floor and needs no written waiver. The two adversarial forged-history
failures it described are unchanged and still published in EVALS.md's
representative-failures section; what has gone is a standing exemption over a
suite that only ever needed one case's worth of tolerance. A `conversation`
suite that falls two or more cases short will fail the gate with nothing in the
way — which is what `stale_annotations` was added to guarantee.

## Alternatives rejected

**Widen the tolerance.** Raising the 5-point threshold to cover a 25-point
single-case swing on a four-case suite would mean a 25-point threshold, which
on the full suite would waive a suite failing a quarter of its cases. The
problem was never the percentage; it was measuring a small suite in
percentages.

**Exempt the smoke mode.** The subset is the only pre-merge signal there is, and
a gate that switches itself off in the mode that runs on every pull request is
a gate in name only.

**Grow the smoke subset until percentages behave.** Each smoke case is a paid
model call on every pull request, and the subset was sized against that cost
(ADR 0022). Buying more cases to make a gate's arithmetic work is paying to
avoid fixing the arithmetic.
