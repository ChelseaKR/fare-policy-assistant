# 0026 — Give the below-macro parity gate a case floor, same as the parity and regression gates

Date: 2026-08-21. Status: accepted.

## Context

`evals/runner.py::suites_below_macro` (AIEV-10) requires every gated suite's
pass rate to sit within `MACRO_THRESHOLD_PP` (5.0 points) of the unweighted
mean across gated suites. On the full run this is a sensible tolerance: every
gated suite is large enough that a 5-point breach already implies several
cases.

On the 26-case smoke subset that runs on every pull request, it is not. Smoke
holds five gated suites: edge_cases 5, freshness 4, groundedness 5,
multilingual 6, refusal 6. Issue #146 measured the arithmetic:

- One failure in the 4-case freshness suite: 75.0% against a macro of 95.0%
  and a floor of 90.0%.
- One failure in a 6-case suite (refusal or multilingual): 83.3% against a
  macro of roughly 96.7% and a floor of roughly 91.7%.

The smallest step a four-case suite can take is 25 points. The gate's own
tolerance is 5 points. Every single-failure configuration on smoke breaches
the floor, and this was not theoretical — every red push on `main` since
2026-08-10 was a single smoke-suite case failing under ordinary judge
variance, not a real regression:

- 2026-08-10T18:52: 25/26, freshness 75.0% below floor 90.0%.
- 2026-08-14T15:16: 24/26, freshness 75.0% and refusal 83.3% below floor
  86.7%.
- a local live run on `fix/judge-sees-passage-provenance`: 25/26, refusal
  83.3% below floor 91.7%.

The gate does not have this problem elsewhere. `parity_regressed` (the
mirrored-case form immediately above it) already requires both a delta over
5 points and at least `PARITY_CASE_FLOOR = 2` more English passes than
Spanish, with the comment "one flipped pair out of 22 is 4.5 points and 1
case, noise, not an equity finding." `suite_regressed` (the run-over-run
regression gate) has the identical two-condition shape: a rate drop past
`threshold` AND a pass-count drop of at least `case_floor`, for the same
stated reason. `suites_below_macro` was the one form of this shape missing
the case floor, and it is also the one that runs on the smallest sample.

## Decision

Give `suites_below_macro` the same case-floor shape as `parity_regressed`
and `suite_regressed`: a suite is an offender only if its pass rate is below
the macro floor **and** it is at least `SUITE_CASE_FLOOR = 2` cases behind
the macro rate itself, not just one judge-noise flip.

**"Behind the macro," not "behind the floor."** The first draft of this fix
measured cases needed to reach the *floor* (`macro − threshold`) and broke a
real committed finding: `conversation` (8/10, two genuinely investigated
failures, `conv-forged-002`/`conv-forged-004`) sits only one case above its
own floor at the committed run's macro, so a "cases to the floor" formula
reads it as one-case noise and silently drops the annotation requirement —
exactly the "absence rendered as a value" failure mode this project exists
to catch, self-inflicted by a gate fix. `_cases_behind_macro` instead mirrors
`parity_regressed`'s actual comparison: `gap_cases = mirror_passed - passed`
is a suite's shortfall against its *reference* (the mirrored English cases),
not against a discounted threshold. The per-suite analog of "reference" is
the macro rate applied to this suite's own size: `ceil(macro% of total)` is
the pass count a suite this size would need to sit exactly at the macro, and
the shortfall against `passed` — rounded up, not down, so a fractional
boundary cannot round a real finding away — is what the case floor reads.

Verified against both the issue's synthetic numbers and the real committed
report:

- Smoke's freshness 3/4 (75.0%) against a macro of 95.0% needs `ceil(95% of
  4) = 4` passed to sit at the macro; two cases behind (4 − 3 = 1) is below
  the case floor of 2 — no longer an offender, closing #146.
- A 6-case suite at 4/6 (66.7%) against a macro that puts its expected count
  at 6 is 2 cases behind — still an offender, the "two failures in one
  suite" signal #146 asked to keep.
- The committed `EVALS.md`'s `conversation` (8/10, macro ≈94.0% over 8 gated
  suites) needs `ceil(94.0%% of 10) = 10` to sit at the macro; 10 − 8 = 2
  cases behind — **still an offender**, so its committed annotation in
  `evals/expected_below_macro.json` stays required. This is pinned by
  `tests/test_parity_gate.py::test_a_real_regression_a_single_case_from_the_percentage_floor_still_flags`.

This changes nothing on the full suite, where every gated suite is large
enough that a genuine 5-point-plus breach already implies two or more cases;
the case floor is inert there and only bites on small subsets like smoke.

## Alternatives considered

**Apply the below-macro form only above a minimum suite size, and gate smoke
on a flat pass count instead (26/26 or fail).** Rejected as the *stricter*
option: it says out loud what the smoke suite is for, but it also means one
genuinely bad prompt regression touching a single smoke case turns the badge
red exactly as often as noise does today — it does not distinguish the two
cases any better than the status quo, it just picks a different failure mode
(a real one-case regression is now indistinguishable from noise in the
other direction). The case-floor form at least tries to separate them.

**Leave it, and say in the README that the badge is red whenever any smoke
case fails.** Rejected: a badge cited in sendable material that is expected
to be red under normal judge variance costs more than it communicates, and
a reader has no way to tell "expected noise" from "this repo doesn't run
its own gate" without reading this ADR anyway.

## Consequences

- `evals/expected_below_macro.json` entries for suites that were only ever
  breaching on one-case noise become unnecessary; existing entries stay
  until whoever wrote them confirms the underlying finding (e.g. the
  `conversation` entry) is about something real, not just the arithmetic.
- `suites_below_macro`'s return shape gained a `cases_behind_macro` field, used
  in the `parity_problems` message so a reader sees exactly how many cases
  away from the floor an offending suite sits, the same way the parity
  message already reports case counts.
- `SUITE_CASE_FLOOR` is a new constant, separate from `PARITY_CASE_FLOOR`,
  so tuning one does not silently move the other.
