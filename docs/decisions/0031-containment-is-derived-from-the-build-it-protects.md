# 0031 — Containment is derived from the build it protects, not defaulted per script

Date: 2026-09-01. Status: accepted.

## Context

`yolobus-fares` has been contained since 2026-07. The reason was specific and
good: the committed snapshot's fare table read "All below fares are effective
July 1, 2025 – June 30, 2026", that period had ended, and a rider told a fare
that is no longer charged is worse off than a rider told nothing. Containment
made the deployed assistant refuse Yolobus fare questions.

`infra/deploy.sh` set the bar for lifting it: "remove it only after the
replacement source has been reviewed, ingested, evaluated, and approved." All
four are now met (issue #164). The page was refetched 2026-08-21 and publishes a
period running to 2027-06-30; the three Yolobus documents were re-ingested and
are mutually consistent; 42 cases scored on the 2026-08-22 cold full live run,
36 of them passing; and the approval is the issue itself. Containment that
outlives its reason is not a free safety margin — it is a rider being refused an
answer the corpus holds.

The containment was not one default, though. It was three, in three scripts,
with three different meanings:

- `infra/deploy.sh` decided what a **new** build ships with.
- `infra/check-lambda-version.sh` decided what a checked version is **required to
  have**, which made a correctly un-contained function fail its own health check.
- `infra/rollback.sh` decided what a **retained target** must have before the
  rider-facing alias may move to it.

The first two are bookkeeping. The third is not, and deleting it the way the
other two were deleted would have opened a real hole. A rollback moves the alias
to an *older* version, and an older version carries an older corpus. Production's
pin when #164 was filed, `35ec70d6359d`, is one of five archived corpora that
still hold the expired table. "Expired snapshot, no containment" is the one
combination that must stay impossible, and a plain deletion would have made it
reachable the first time a rollback target predated the corpus refresh.

Inverting it — keeping `yolobus-fares` required at rollback time — fails the
other way. The next build to become the retained target will not contain it, and
rollback would refuse the one action it exists to perform, during an incident.

## Decision

A containment requirement is **derived from the build it applies to**, not
defaulted by the script that happens to be running.

1. `infra/deploy.sh` contains nothing by default. It still inherits the live
   function's `FPA_DISABLED_DOC_IDS` ahead of its default, so a routine deploy
   never silently un-contains a document; clearing it on production takes one
   explicit `FPA_DISABLED_DOC_IDS=""` deploy. The deploy summary prints the
   value and where it came from, so an inherited containment nobody chose any
   more is visible rather than silent.
2. `infra/check-lambda-version.sh` requires nothing by default. Every caller
   that has an opinion passes it: `deploy.sh` passes what it deployed,
   `rollback.sh` passes what it derived. A containment in force is still
   verified, and the Yolobus refusal probe still runs whenever the document is
   named.
3. `infra/rollback.sh` derives its requirement per target.
   `scripts/yolobus_containment.py` reads the fare period out of
   `corpus/versions/<the target's pinned corpus>/chunks.jsonl` and reports
   whether that period has already ended. Both directions are then correct: a
   target carrying the expired table is refused unless it contains the document,
   and a target carrying the refreshed table is accepted without it.

## Why the corpus archive, and not git history or a version allowlist

Three candidate sources of truth were available.

A **hand-maintained allowlist** of safe corpus identities is the shape this
repository already treats as a defect elsewhere: a list that is correct on the
day it is written and silently wrong afterwards, with nothing to notice.

**Git ancestry** — "is the target's `FPA_SOURCE_REVISION` a descendant of the
commit that refreshed the snapshot?" — is derivable and was the first design.
It fails in CI, where `actions/checkout` clones at depth 1 and the refresh commit
is not present, so the derivation would report "cannot determine" on every run
and no test of the lifted path could execute. A safety check whose evidence is
absent in the environment that runs it is not a check.

The **corpus archive** is committed, complete, and is the artifact in question.
`corpus/versions/<corpus_version>/chunks.jsonl` holds every corpus the project
has published, the fare period is stated in the document's own text, and the
target Lambda names its corpus in `FPA_PINNED_CORPUS_VERSION`. So the question
"does this specific build serve an expired Yolobus fare table?" is answered
offline, from the build's own evidence, with no network call, no model call, and
no git history. `tests/test_yolobus_containment.py` asserts that every archived
corpus resolves to a read verdict rather than an unreadable one, so an archive
that stops parsing fails in CI rather than during an incident.

## Consequences

**Every unresolvable case requires containment.** A corpus not archived in this
checkout, a fare period the parser does not recognise, a pin that is not a corpus
identity: none of those are evidence that a build is safe, and the derivation
never reports "not required" except from a period it actually read and found
unexpired. That direction is deliberate and it has a cost — an unreadable target
refuses the rollback — so the escape hatch stays: `FPA_REQUIRED_DISABLED_DOC_IDS`
skips the derivation entirely and makes the call the operator's, on the record,
printed above the alias move.

**An open-ended fare period reads as unresolved, not as current.** If Yolobus
republishes with "effective July 1, 2027" and no end date, the derivation cannot
distinguish "no expiry" from "wording I do not parse" and reports containment as
required. That is the conservative reading and it is the wrong one in that
specific case; the fix then is to teach the parser the new shape, not to widen it
to accept anything it fails to read.

**This does not change rider-facing behaviour on its own.** Production is behind
`main` (issue #140), and lifting the default only affects what the *next* deploy
ships. The live function keeps its inherited containment until someone deploys
with `FPA_DISABLED_DOC_IDS=""`, which is the point at which the refreshed Yolobus
corpus reaches riders anyway.

**Generalisation is deliberately not attempted.** The derivation is named for the
document it protects and knows one document's fare-period wording. A second
contained document would want the same treatment, not this function widened by
guesswork about a page nobody has read yet.
