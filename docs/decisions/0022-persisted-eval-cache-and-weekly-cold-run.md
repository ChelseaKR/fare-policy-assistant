# 0022 — Persisted eval cache, cached nightlies, one cold run a week

Date: 2026-08-01. Status: accepted.

## Context

CI is the largest single consumer of this project's model budget. A CloudTrail
reconciliation of the AWS account for 2026-07-25 to 07-31 attributed 3,074
Bedrock `InvokeModel` calls to the `fare-policy-assistant-ci` role, about
$19 for the week. The deployed demo role accounted for 29 calls in the same
window.

The calls come from two jobs in `.github/workflows/ci.yml`:

| Job | Trigger | Cases | Model calls per run |
|---|---|---:|---:|
| `smoke-evals` | every pull request and every push to `main` | 26 | up to 74 |
| `full-evals-nightly` | the nightly schedule | 201 | up to 575 |

A call is one answer completion (Haiku 4.5) or one judge completion
(Sonnet 4.6). The judge is the expensive half: the last committed full run cost
$3.16, of which $2.19 was judging.

`evals/cache.py` already caches both, keyed on the rendered system and user
prompt text. Nothing used it in CI. `evals/cache/` is git-ignored and no job
restored it, so every CI run started cold and re-bought results it already
had. Two patterns dominated:

* **Merge duplication.** Every pull request run was followed within minutes by
  a push-to-`main` run over the same tree. Ten of the twenty smoke runs that
  actually executed in the sample week were the second half of such a pair.
* **Unchanged inputs.** Six of the eleven pull requests merged in that week
  touched no prompt, no corpus document, and no retrieval or answer-assembly
  code. Their evals could not have produced a different answer, and re-ran at
  full price anyway.

## Decision

**Persist `evals/cache/` across CI runs**, restored and saved by
`actions/cache` in both eval jobs under one shared key prefix. Restore and
save are separate steps so a run whose gate goes red still keeps the calls it
paid for.

**Keep the smoke suite on every pull request and every push to `main`.** The
README states this and it is a real property of the project; the fix is to
stop paying twice for it, not to stop doing it. A `paths` filter was
considered and rejected: the content key already decides, exactly and without
a hand-maintained path list, whether a change can alter an answer, and a
stale path list would silently stop evaluating something that matters.

**Run six of seven nightlies from cache, and one — Monday's — cold**, via a
second `cron` entry and the new `--refresh-cache` flag. `--refresh-cache`
reads nothing and writes everything, so the cold run both re-measures the
provider and leaves the stored answers agreeing with the scoreboard it
published. A plain `--no-cache` Monday would re-measure and then leave Tuesday
free to republish the answers Monday had just contradicted.

**Bound the store.** `MAX_ENTRIES_PER_STORE` (4,000, about ten full runs) with
least-recently-used eviction on save. A cache nothing prunes would accumulate
every superseded prompt and corpus version indefinitely.

The judge model stays Sonnet 4.6. Dropping the judge to a cheaper tier would
cut more cost than any of the above, and is rejected here: the committed
judge-versus-human calibration in `evals/calibration/` is measured against
this judge, the answer model is already Haiku, and the harness requires the
two to differ. Changing the judge invalidates the calibration evidence that
makes the scoreboard meaningful.

## Consequences

A pull request that changes no prompt, no corpus document, and no code
affecting retrieval or prompt assembly now makes zero paid model calls: every
rendered prompt is byte-identical to one already stored. A pull request that
changes any of them misses on exactly the affected cases and pays for those.
The nightly scoreboard is unchanged on an unchanged tree, which is the correct
result and now the cheap one.

**What is given up is provider-drift detection on six nights out of seven.**
If Bedrock's serving of a pinned model changes behaviour on a Tuesday, a
cached nightly will not see it; Monday's cold run will, within a week. Case
coverage, suite composition, the regression gate, the parity gate, and the
deterministic checks are all unchanged — the deterministic checks and both
gates re-execute on every run regardless, because only the model call is
cached and everything downstream of it runs fresh.

Cache correctness rests on temperature-0 determinism, which the README records
as directly probed for both models. Where it does not hold, the effect is that
a run reuses a previous sample instead of drawing a new one; `--no-cache`
remains the escape hatch for variance measurement (`--replicates` forces it),
and every run summary records the cache's hit rate so a suspiciously fast or
cheap run explains itself. The generated report says so on the cost line.

Cache scope is per-ref. A pull request restores from its own branch and falls
back to the default branch, so it inherits `main`'s warm cache; it can only
write to its own scope, so no branch can poison `main`'s. Fork pull requests
never receive AWS credentials and run the suite offline, unchanged.
