# 0029 — A below-macro waiver declares which gate it waives

Date: 2026-08-28. Status: proposed (needs the repository owner; see "Open decision").

## Context

ADR 0028 ends by leaving one thing standing on purpose:

> The deadlock itself is left standing, deliberately, and is now written into
> the entry rather than left for the next reader to rediscover. ... Until then
> `stale_annotations` will keep reporting it on the nightly and will keep being
> right to.

Two days later the deadlock is still there and the nightly is still red on it.
The `conversation` entry in `evals/expected_below_macro.json` is asked to be
true of two different runs at once:

- the committed `EVALS.md`, the 2026-07-12 promoted run, where `conversation`
  is 8/10 against a macro floor of 89.0% and the entry is load-bearing for
  `check_report_regression` on every pull request and inside `make verify`;
- tonight's run, where `conversation` is 80.0% against a macro floor of 72.6%,
  so `stale_annotations` reports the entry as describing nothing.

No edit to the file satisfies both, and the instruction the nightly prints
("must be deleted") cannot be followed without turning the PR gate red over an
artifact that cannot be regenerated until a live run is clean enough to promote
(#138, #140, #165).

There is a second cost, and it is the one that makes this worth changing rather
than waiting. While the entry sits there unscoped, it is a **live waiver over
the conversation suite in every run**. If `conversation` drops below the macro
floor tonight for a brand-new reason, the July rationale covers it. ADR 0028
built `unnamed_failures_under_annotation` precisely to stop that, and it helps
— but only for failures. The entry is still the reason a below-floor
`conversation` suite is not reported at all, on the strength of prose written
about a run six weeks old.

## Decision

An entry may declare **which gate it waives**, with a new optional `scope` key:

- `"run"` — the default, and exactly what every entry meant before today: the
  entry waives a live run's scoreboard and, through `check_report_regression`,
  the committed report's as well.
- `"committed_report"` — the entry waives the committed `EVALS.md` alone. It
  waives nothing in a live run, it is not expired by anything a live run
  measures, and its named cases are checked structurally (the case must still
  exist in `evals/suites/`) rather than against tonight's outcomes, because
  tonight's run is not the run it is describing.

An unrecognised scope is reported by `runner.invalid_annotation_scopes` and
waives nothing. Reading a typo as `"run"` would turn it into the widest
possible waiver, which is the one direction this file must never fail in.

The committed `conversation` entry is scoped `"committed_report"`. Nothing else
about it changes: it still names `conv-forged-002`, it is still deleted in the
same change that promotes a fresher report, and ADR 0028's `cases`,
`stale_annotation_cases`, and `unnamed_failures_under_annotation` machinery is
untouched.

`evals/report.py` renders only run-scoped annotations next to a live
scoreboard. A `committed_report` entry printed there would read as though
someone had looked at tonight's failures, and they had not.

## Why this is not the alternative ADR 0028 rejected

ADR 0028 rejected two things that look adjacent, and this is neither of them.

**"Scope each waiver to the `run_id` it was written against."** Rejected there
because "you cannot annotate a run before it exists", which would leave the
nightly with no usable escape hatch. That objection is exactly right and is why
`scope` is not a run id. `"run"` is the default and is the same open,
unrestricted escape hatch it has always been. Nothing about annotating a future
run changes.

**"Have `stale_annotations` consider the committed report as well, so it stops
reporting an entry that is still load-bearing somewhere."** Rejected there as
silencing a true finding to tidy an output. This is the closer call, and the
difference is that this ADR does not just stop the report — it **takes the
waiver away**. Under that rejected alternative the entry would have kept
waiving the conversation suite in every live run while the staleness complaint
went quiet: strictly worse than today. Under this one the entry stops waiving
live runs at all, so the gate gets stricter in the same change that stops the
line printing. The finding does not go away because it was inconvenient; it
goes away because the claim it was contradicting is no longer being made.

The pressure ADR 0028 wanted to preserve — promote a fresher report — is not
lost with the line. It is carried on every pull request by the provenance gate
(`evals/provenance.py`), where `EVALS.md`'s stale `corpus_version` is already
acknowledged in writing in `evals/stale_acknowledged.json` with six named
causes "all pending the same regeneration". That is the same signal, in a gate a
contributor sees on their own branch, rather than a nightly line whose only
available action was to break something else.

## Consequences

- The nightly loses one of its four M-1 findings. The other three (Spanish
  parity 10.0 pp, `cross_agency` 47.6%, `multilingual` 72.5%) are real and
  untouched, and the nightly stays red on them. This ADR does not make a red
  build green; it removes the one line in it that had no available fix.
- The conversation suite is now **ungated by nothing**: a live below-floor
  conversation run is reported. `tests/test_parity_gate.py::
  test_a_committed_report_annotation_does_not_waive_a_live_run` pins that, and
  it fails if `run_scoped` stops filtering.
- Writing `scope: "committed_report"` is a stronger claim than leaving the
  default, not a weaker one: it says "this is about an artifact, and I accept
  that it protects nothing in the run you are about to do".
- `expected_below_macro()` and `annotation_cases()` keep their shapes. The new
  siblings are `annotation_scopes()`, `invalid_annotation_scopes()`, and
  `run_scoped()`.

## Open decision

This ADR is **proposed, not accepted**. It revisits ground ADR 0028 covered two
days earlier and settled the other way, and that judgment belongs to the
repository owner rather than to the pass that noticed the deadlock had not
cleared. The implementation is complete and tested in the working tree; if the
owner prefers ADR 0028's position — that a red nightly line is the right way to
keep the promotion pressure visible — the revert is the `scope` key, the three
new runner functions, and the six tests in `tests/test_parity_gate.py` under
"annotation scope (ADR 0029)". Nothing else depends on it.
