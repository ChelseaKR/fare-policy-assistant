# 0032 — The nightly hub publishes every run, pass or fail, behind an explicit switch

Date: 2026-09-04. Status: accepted.

## Context

Issue #140: `https://evals.chelseakr.com/` had one publish in its history —
`.github/workflows/pages.yml` dispatched by hand on 2026-07-12, 22 seconds,
success — and nothing since. The nightly full-eval run (`ci.yml`,
`full-evals-nightly`) regenerates `EVALS.md` and `docs/eval-report.html` and
uploads them as a workflow artifact every night, but nothing had ever
published that artifact. A reader comparing the hub to the repository saw a
100% three-case `cross_agency` suite from a nine-suite, 201-case run where the
repository, at eighteen agencies and 385 cases, actually scores `cross_agency`
at 19.0% across twenty-one cases. Absence rendered as a value: the reader has
no way to tell "this is current" from "this is two months old and nobody
looked."

Separately, `docs/decisions/0030` and the `workflow_dispatch`-only pipeline in
`pages.yml` already solve a narrower, harder problem: publishing a
cryptographically pinned, promoted release, with a page that can only ever say
"Verified" (`require_current_public_evidence` refuses anything else) and
therefore can only be built from a run whose gates passed. That pipeline has
never had a promoted run to publish — the last promotion remains 2026-07-12
per `docs/publishing-the-evidence-hub.md` — and fixing that is a deploy/evals
concern outside this repository area's ownership. Waiting on it is exactly how
the hub went two months stale: nothing else could publish in the meantime.

The nightly, as of this writing, has failed fourteen nights running, on two
gates. That fact forces the actual decision this ADR is about.

## Decision

**Publish every completed nightly run, whether its gates passed or not, and
say which on the page.**

If publication is gated on the nightly's gates passing, and the nightly has
not passed in fourteen nights, the hub does not get unstuck — it freezes at
whatever the last passing run was, which reproduces issue #140 exactly, just
with a later frozen date. A fix that only works when the underlying system is
healthy is not a fix for the case that matters, which is when it is not.

So a second, independent publication path is added: `pages.yml` gains its own
`schedule` trigger, four times a day, that queries the CI workflow's own run
history through the read-only Actions API for the most recently completed
nightly (`schedule`-triggered) run and renders and deploys a **nightly
snapshot** from whatever it finds — a different page
(`docs/pages/nightly-index.html`, not `docs/pages/index.html`) with different
claims:

- It never says "Verified." Its status line reads "Nightly gate: passed" or
  "Nightly gate: FAILED," sourced directly from the CI run's own conclusion —
  not re-derived, not inferred, the same boolean CI already computed.
- Its scoreboard is `EVALS.md`'s own `<!-- provenance {...} --> ` block for
  that run, republished suite by suite, failing suites included. This is the
  same machine-readable format `evals/provenance.py` and
  `evals/report.py` already establish and CI already gates on drifting;
  nothing new is invented to read it.
- It states plainly, with a link, that it is not the promoted-evidence page
  and that a stricter pipeline exists and remains blocked, so a reader who
  wants the cryptographic guarantee knows where that claim is made and where
  it currently is not.

A page that can only ever be "Verified" cannot report a failing run honestly;
the fix there (ADR 0030) was to be honest about staleness within a state space
that has no room for failure. This page's state space has room: passed, failed,
and — the second half of issue #140's ask — stale, decided at read time by an
inline script that compares `data-run-at` / `data-expires-at` against the
reader's clock, on the same technique as ADR 0030, independently implemented
so this page owes the promotion pipeline's renderer nothing and can change
without touching it. The freshness budget is 36 hours: the nightly cron fires
daily, so a gap that wide means the nightly has gone quiet, not just run late.
`docs/decisions/0031`'s "GitHub Actions billing block RECURRED" is the concrete
failure mode this defends: publishing stops silently, and only a page that
notices its own age catches it.

## Why `schedule` polling a run history, not `workflow_run` reacting to one

The first version of this change used `workflow_run` on `ci.yml`'s
completion, restricted with `github.event.workflow_run.event == 'schedule'` so
it could only ever fire off a genuine nightly run and never a pull_request- or
push-triggered one. That restriction is real and the analysis behind it holds
up; it is not why it changed. It changed because zizmor's `dangerous-triggers`
rule flags `workflow_run` categorically — "almost always used insecurely,"
regardless of how the consumer guards it — and this repository's zizmor gate
(`zizmor (blocking; no mute)`) is deliberately unsuppressible: 26 other
findings across the workflow set are muted with inline justifications, and
this rule is not one of them, on purpose. Arguing a suppression past a gate
built specifically not to take one is the same move as widening a freshness
budget nobody can see (ADR 0030) or loosening a gate under pressure — correct
in this one instance, maybe, and a habit this repository has chosen not to
practice. So the trigger was replaced rather than the finding suppressed.

The replacement queries `GET /repos/{repo}/actions/workflows/ci.yml/runs`
(`event=schedule`, `status=completed`, newest first) through `gh api`, using
the same same-repo `GITHUB_TOKEN` already scoped `actions: read` for the
artifact download two steps later. Nothing here reaches outside the
repository's own run history, and nothing in the query is attacker-influenced.

This turned out to be strictly better on the merits, not just an equally-safe
substitute. A `workflow_run` consumer only ever runs when CI produces a
completed run to react to — a night the nightly never fires at all (schedule
disabled, the GitHub Actions billing block from ADR 0031's history recurring,
CI itself broken) is a night that consumer would never have run either,
silently, and the hub's only defense would have been the client-side
freshness banner eventually going stale on its own. A poller does not have
that gap: it runs on its own four-times-a-day schedule regardless of what CI
did or did not do, finds whatever the most recently completed nightly run
actually is (however old), and republishes with two independent timestamps on
the page — when that run happened, and when the publisher last checked for a
newer one. When the second keeps advancing and the first does not, a reader
knows the checker is alive and the nightly itself is what went quiet, which is
exactly the "absence rendered as a value" shape this portfolio keeps finding
in other repos, closed here instead of reproduced.

## What is not done here

**Publication is not automatic on landing.** `nightly-build` and
`nightly-deploy` both require `vars.NIGHTLY_HUB_PUBLISH_ENABLED == 'true'`, a
repository variable that does not exist until an operator sets it in Settings
-> Secrets and variables -> Actions -> Variables. Every run before that is a
no-op — both jobs evaluate their `if:` and skip. This repository's thesis is
that publishing the real, currently-failing number is correct; whether *this*
PR is the moment that number goes live on a public site is not this repo
area's call to make on its own. Merging this workflow changes nothing
observable until that switch is set once, deliberately, by someone who has
read this document.

**The two pipelines are not reconciled.** Both deploy to the same GitHub Pages
site (`evals.chelseakr.com`, confirmed CNAME + `actions/deploy-pages`, not a
separately hosted target); each publish fully replaces what the other most
recently published, serialized by the shared `concurrency: group: pages`.
Today that is moot — the promotion pipeline has never run — but the day it
does, a promoted "Verified" page could be superseded by the following night's
nightly snapshot. That is an intentional, disclosed tradeoff (the nightly page
says outright it is not the promoted page) rather than an oversight, but it is
a real one: this repo area owns publishing machinery, not the promotion
pipeline's semantics, and reconciling the two — e.g., a nightly snapshot that
defers to a still-fresh promoted page instead of overwriting it — is future
work, not solved here.

**Nothing under `evals/runs/` is read or published.** Only `EVALS.md`'s
provenance block and a byte-identical copy of `docs/eval-report.html` are
used, both already the artifact this project has published from the
repository root since before this change.

## Consequences

A reader at `evals.chelseakr.com`, once the switch is set, sees either a
promoted "Verified" page (if one exists and is fresh) or last night's real
scoreboard, labeled as exactly that, never older than one missed night before
the page itself says so. The two months of silence issue #140 found cannot
recur silently: it can only recur loudly, as a page that has turned red and
says why.
