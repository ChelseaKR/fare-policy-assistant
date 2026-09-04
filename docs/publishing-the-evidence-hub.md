# Publishing the evidence hub

How <https://evals.chelseakr.com/> is published, why it currently cannot be,
and what an operator would have to do first. Everything below was checked
against the repository, the GitHub Actions history, and the two live
endpoints on 2026-08-28; nothing is inferred from the workflow file alone.

## Update, 2026-09-04: a second publication path exists now (issue #140)

Everything below this notice describes the `workflow_dispatch` promotion
pipeline as it stood on 2026-08-28, and it is still accurate about that
pipeline: it has still never run against real evidence, and the three
blockers this document lists still stand. What has changed is that it is no
longer the *only* path to the domain. `pages.yml` also has its own `schedule`
trigger and a job pair (`nightly-build` / `nightly-deploy`) that polls the CI
workflow's run history four times a day and republishes whatever the most
recently completed nightly `ci.yml` run is, whether that run's gates passed,
failed, or never ran at all, labeled honestly either way and carrying a
read-time staleness banner.
See [ADR 0032](decisions/0032-the-nightly-hub-publishes-every-run-pass-or-fail.md)
for why publishing a failing run is correct here rather than a regression of
the guarantee below, and for the one thing that pipeline deliberately does
not do on its own: publish. It is gated behind
`vars.NIGHTLY_HUB_PUBLISH_ENABLED`, unset until an operator sets it — landing
that workflow does not, by itself, change what `evals.chelseakr.com` serves.

## What is published today

`.github/workflows/pages.yml` has run **once**, run `29184323093`, dispatched
2026-07-12T07:29:43Z against `c788f9efc2ecd61e892965d7bac9708c4750db4b`. At
that commit the workflow was a four-line copy step: `docs/pages/index.html`,
`docs/eval-report.html`, `docs/eval-history.svg` and `docs/pages/CNAME` into
`_site`. It took no inputs and verified nothing. The bytes it published are
still what the domain serves, `Last-Modified: Sun, 12 Jul 2026 07:29:59 GMT`.

The evidence-pinned workflow that replaced it landed 2026-07-30 in `fcee166`
("Bind promotions to exact evaluation evidence"), which is **not** an ancestor
of the deployed commit. It has never run. `public-evidence.json` and
`release.json` are linked by the current `docs/pages/index.html` template and
return 404 on the live host, because no run has ever written them.

## What the three inputs are for

| Input | Meaning |
|---|---|
| `source_revision` | Full SHA of a commit on `main` that supplies the *renderer*: `scripts/build_evidence_site.py` and `docs/pages/index.html`. Checked to be an ancestor of `origin/main`, and required to equal the `source_revision` attested inside the evidence manifest. |
| `evidence_ref` | Full SHA of a commit whose entire tree is exactly one regular file, `public-evidence.json`. Kept separate from `source_revision` so the reviewed evidence and the reviewed renderer are two independent approvals. |
| `expected_public_manifest_sha256` | SHA-256 of the exact canonical manifest bytes, checked before render and again after deploy against the bytes the CDN actually serves. |

The split is the point: neither a source commit nor an evidence commit alone
can change what the public page claims.

## The guards that run before anything is published

In `build`, in order: input shape; both checkouts clean and at the requested
SHAs; `source_revision` an ancestor of `origin/main`; the evidence checkout
holding exactly one non-symlink `public-evidence.json`; its digest equal to
`expected_public_manifest_sha256`; then `compare-runtime` against a freshly
fetched `https://fare.chelseakr.com/version`; then render, which refuses to
emit `results.jsonl`, `summary.json` or `promotion.json`. In `deploy`, every
one of those evidence checks runs again after deployment, and the published
`public-evidence.json` is re-fetched and re-digested until it converges.

Both `render` and `compare-runtime` begin with
`require_current_public_evidence`, which recomputes age from `run_at` rather
than trusting the manifest's recorded `age_seconds`.

## Why a dispatch today would publish nothing

Three independent blockers, in the order the workflow would hit them.

1. **No `evidence_ref` exists.** `git log --all -- public-evidence.json` is
   empty and `git ls-files` has no such path; `git ls-remote` shows no ref
   outside `heads`, `tags` and `pull`. `build_evidence_site.py export` is
   invoked nowhere in the repository outside its own tests: no Makefile
   target, no workflow, no script. The export step has never been run, so
   there is nothing to point the second key at.

2. **The evidence would be refused as stale.** The only promoted run is
   `2026-07-12T05:01:17+00:00` (`EVALS.md`, `evals/baseline.json`,
   `docs/eval-report.html`). `require_current_public_evidence` fails with
   "public evidence is stale at verification time" once `now - run_at`
   exceeds the manifest's `max_age_seconds`. That budget is operator-chosen
   at export time via `--freshness-seconds`, so this blocker alone could be
   argued past by declaring a wide budget.

3. **The runtime tuple does not match, whatever the budget.** The July run
   attests corpus_version `0938fff0539a`. The live Lambda reports
   `35ec70d6359d` (checked at both `fare.chelseakr.com/version` and the API
   Gateway URL). `compare_runtime_version` compares corpus_version among
   `_RUNTIME_FIELDS` and fails with "runtime version differs from public
   evidence: corpus_version". This blocker is not operator-tunable, and it is
   the honest one: the promoted scores were computed against a corpus the
   deployed assistant no longer serves.

Blocker 3 also means the useful `source_revision` is constrained. The live
runtime attests `180aa043f740c076ec7ec9443f2067b56009c985`, which is an
ancestor of `origin/main` and does contain the renderer, so it is the only
value that could satisfy `compare-runtime` against today's deployment.

## The decision this exposed, and what was done about it

Until 2026-08-29 the freshness budget was enforced in exactly one place, at
render time, and never again. `validate_public_manifest` accepted a manifest
with `status: "warning"`, `_template_html` rendered it as "Verified with
freshness warning", and `docs/pages/index.html` styled `.notice.warning` for
it, but none of that was reachable: `_template_html`'s only caller is
`render_evidence_site`, which calls `require_current_public_evidence` first,
and that rejects every status but `verified`.

So the published page had one state and it was "Verified", permanently. That is
not a report. A page that cannot return a second answer is not answering a
question, and the fact that this one sat on a project about evaluation honesty
is the reason it was worth fixing rather than documenting again.

The fix was **not** to loosen the publication gate. Refusing to publish stale
evidence is right, and `require_current_public_evidence` is unchanged. The
error was treating the build as the last moment freshness could be judged.
Publication happens once; reading happens for as long as the page is up.

What the renderer now publishes instead:

- **`data-expires-at`**, the instant the verdict stops being true: `run_at`
  plus `max_age_seconds`, printed on the page as a `<time>` element. This is
  the first time the operator's budget has been visible to a reader at all.
- **One inline script**, which compares that instant to the reader's own clock
  when the page loads and, past expiry, swaps the notice to `.notice.warning`,
  relabels it "Verified with freshness warning", and states the age in days.
  The strings the old dead branch held now live here, where a reader reaches
  them. It fetches nothing, stores nothing, and sends nothing anywhere.
- **A digest-pinned `script-src`.** The page's policy stays `default-src
  'none'`; `'unsafe-inline'` is not used. `_script_csp_hash` computes the
  SHA-256 of the exact bytes about to be inlined, so the policy admits that one
  script and cannot drift from it.

With scripting off, the static text still carries the expiry instant and says
outright that the status above it was settled at build time and is not
maintained. That is weaker than a computed verdict and it is stated as such,
because the alternative is an unqualified "Verified" that nothing can retract.

`tests/test_build_evidence_site.py` runs the published script in node at four
clocks: one day in, the last second of the budget, one second past it, and 48
days past it, which is how long the live hub had been serving one verdict when
this was written. A structural assertion that the stale strings appear
somewhere in the file would pass against a page whose script never runs, so the
script is executed instead.

The reasoning is recorded in
[ADR 0030](decisions/0030-freshness-is-decided-at-read-time-not-build-time.md).

Note what this does not do. It cannot correct the page currently at
`evals.chelseakr.com`, which was published on 2026-07-12 by a workflow
definition that no longer exists and carries none of this. Every page published
from here on can report its own age; that one still cannot, and the three
blockers below still stand between it and a replacement.

## The command, once there is something to publish

The prerequisite is a promoted live run whose runtime tuple matches the
deployed Lambda. Then export the manifest, commit it alone on an
evidence-only ref, and dispatch:

```sh
# 1. Export the canonical manifest from the promoted run's private receipts.
uv run python scripts/build_evidence_site.py export \
  --summary  <run_dir>/summary.json \
  --results  <run_dir>/results.jsonl \
  --promotion <run_dir>/promotion.json \
  --output   public-evidence.json \
  --as-of    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --freshness-seconds 604800

# 2. Commit it alone. The tree must hold this one file and nothing else.
#    Record the resulting commit SHA as EVIDENCE_REF.

# 3. Dispatch, binding renderer, evidence, and exact bytes.
gh workflow run pages.yml \
  --ref main \
  -f source_revision="<40-hex commit on main carrying the renderer, equal to
                       the source_revision the manifest attests>" \
  -f evidence_ref="<40-hex evidence-only commit from step 2>" \
  -f expected_public_manifest_sha256="$(sha256sum public-evidence.json | cut -d' ' -f1)"
```

`--freshness-seconds` is the one number here that is a judgement rather than a
measurement. It becomes `max_age_seconds` in the manifest, and it is what both
`require_current_public_evidence` and the published page's own check measure
against. Until 2026-08-29 it was invisible to readers, so widening it to make an
old run publishable moved a real staleness decision into a field nobody could
see. The page now prints `run_at + max_age_seconds` as the instant its verdict
expires, so a wide budget is a claim made in public and can be argued with.
