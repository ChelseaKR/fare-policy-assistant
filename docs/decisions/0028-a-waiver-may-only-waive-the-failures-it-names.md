# 0028 — A below-macro waiver may only waive the failures it names

Date: 2026-08-26. Status: accepted.

## Context

`evals/expected_below_macro.json` is the one escape hatch in the M-1 parity
gate. A gated suite listed there may sit more than 5 points below the run's
macro pass rate without failing the build, on the strength of a written
rationale in the diff. The file's own first paragraph says "delete the entry
the moment the suite recovers", and ADR-era practice added one enforcement of
that in 2026-08-05: `runner.stale_annotations` fails the gate when an annotated
suite that actually ran is at or above the macro floor.

That check is real, it fires, and it has been firing. From the 2026-08-26
nightly (`CI` run 32953294135, `full-evals-nightly` job):

```
PARITY GATE (M-1):
  Spanish parity: 29/40 vs mirrored English 33/40 — gap 10.0 pp exceeds 5 pp on 2+ cases
  cross_agency: 47.6% is below the macro floor 72.6% (macro 77.6% − 5 pp) on 7+ cases, with no written annotation in evals/expected_below_macro.json
  multilingual: 72.5% is below the macro floor 72.6% (macro 77.6% − 5 pp) on 3+ cases, with no written annotation in evals/expected_below_macro.json
  conversation: annotated in evals/expected_below_macro.json but is at 80.0%, at or above the macro floor — the annotation no longer describes anything and must be deleted, or it will silently waive the next real regression
```

The last line had been printing for five consecutive nights and the entry was
still there. Not because nobody read it: because the instruction it gives
cannot be followed. Deleting the entry turns the *other* gate red. The same
file is read by `evals/check_report_regression.py`, which applies the below-macro
form to the committed `EVALS.md`, and the committed `EVALS.md` is the
2026-07-12 promoted run, where `conversation` is 8/10 against a macro floor of
89.0% and the annotation is doing genuine work. Measured, with the entry
removed:

```
COMMITTED-REPORT PARITY (M-1) — EVALS.md trips the bilingual parity gate:
  conversation: 80.0% in the committed EVALS.md is below the macro floor 89.0% (macro 94.0%) with no written annotation in evals/expected_below_macro.json
```

So one waiver was simultaneously stale for the nightly and load-bearing for
every pull request, and no edit to the file could satisfy both. That deadlock
is why it sat.

Underneath the deadlock is the substantive defect. The entry read, in full,
"the two failures were conv-forged-002 ... and conv-forged-004 ...". By
2026-08-26 that was no longer true. The 2026-08-22 full live run
(`evals/runs/20260822T131246Z`, `judges_ran: true`) and the 2026-08-26 nightly
both show `conversation` at 8/10 with `conv-003` and `conv-forged-002` failing
and `conv-forged-004` passing. The waiver named a fixed case as its evidence,
and it never named `conv-003`, the failure the suite actually has. The entry's
own 2026-08-16 update had recorded half of this and changed nothing, because
prose is not a gate input.

That is the shape of the hazard the entry's text warns about, and the coarse
check cannot see it. `stale_annotations` asks only "is this suite above the
floor". The moment the suite dips below the floor again for any reason at all,
the question answers itself "no", the check goes quiet, and the old rationale
covers a regression nobody has looked at. Silence, in the exact place the
project claims not to be silent.

## Decision

A waiver may only waive the failures it names.

1. An entry in `evals/expected_below_macro.json` is now an object with two
   keys: `rationale`, the prose that has to survive review and that the report
   prints, and `cases`, the failing case ids the entry claims to cover. A bare
   string still parses, as a waiver that names nothing, and is reported as
   exactly that.
2. `runner.stale_annotation_cases` fails the gate when a named case is no
   longer a case in `evals/suites/`, when a named case passed in the run being
   gated, when a named case turned not-applicable (which is neither a pass nor
   a failure, so it is not a failure the waiver is holding open), or when an
   entry names no case at all. A named case that simply did not run is out of
   view rather than stale, the same rule `stale_annotations` uses for a
   `--suite` subset.
3. `runner.unnamed_failures_under_annotation` fails the gate when a waived
   suite that is *still* below the macro floor is failing cases the entry never
   named. This is the half that shuts the door: a new regression inside an
   annotated suite can no longer land inside an exemption written about
   different cases.
4. `stale_annotations` is unchanged. Nothing here relaxes an existing finding;
   (2) and (3) are additional.

The deadlock itself is left standing, deliberately, and is now written into the
entry rather than left for the next reader to rediscover. The `conversation`
entry stays, anchored to `conv-forged-002` alone, which still fails in both
recorded live runs. It is retained solely because the committed 2026-07-12
`EVALS.md` still needs it, and it is marked for deletion in the same change
that promotes a fresher report. Until then `stale_annotations` will keep
reporting it on the nightly and will keep being right to.

## Consequences

- The offline half of the new check — every waiver names cases, and every case
  it names still exists — runs on every pull request through
  `check_report_regression`, which is in `make verify`. That matters because
  the signal that went unread for five nights was nightly-only.
- The pass/fail half needs per-case results, which a committed scoreboard does
  not carry, so it runs where the records are: `runner.parity_problems`, and
  therefore `check_parity`, and therefore the nightly and every promotion gate.
  `check_parity_committed` deliberately does not guess at it.
- Writing a waiver is now more work: the author has to say which cases, and
  every one of them has to still be failing. That is the point. An entry that
  cannot name a live failure is not an annotation of a known gap, it is an
  exemption.
- `expected_below_macro()` keeps its `dict[str, str]` shape, so `report.py`'s
  scoreboard annotation and every existing caller are untouched;
  `annotation_cases()` is the new sibling that returns the anchors.

## Alternatives considered

**Delete the `conversation` entry, as the nightly instructs.** Rejected on
evidence: it turns `make verify` and the PR gate red over a six-week-old
artifact whose regeneration is blocked on a live run that is red for unrelated,
real reasons (#138, #165). Reds that a contributor cannot clear teach people to
ignore reds.

**Scope each waiver to the `run_id` it was written against, so it applies only
to that report.** This resolves the deadlock cleanly and was tempting. Rejected
because it leaves the nightly with no usable escape hatch at all — you cannot
annotate a run before it exists — which would trade a stale waiver for a
permanently red gate and no way to record an honest, investigated gap.

**Have `stale_annotations` consider the committed report as well, so it stops
reporting an entry that is still load-bearing somewhere.** Rejected: it silences
a true finding to make an output tidy, and this project's whole claim is that it
does not do that. The nightly line is correct. The right fix is to promote a
fresher report, not to stop saying so.
