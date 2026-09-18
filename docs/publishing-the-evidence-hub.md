# Publishing the evidence hub

How <https://evals.chelseakr.com/> is published, why it currently cannot be,
and what an operator would have to do first. Everything below was checked
against the repository, the GitHub Actions history, and the two live
endpoints on 2026-08-28; nothing is inferred from the workflow file alone.
Re-checked the same way on 2026-09-13, when the domain had been serving the
same bytes for 63 days: all three blockers still stand, and two of them are
narrower than this document said. The corrections are in place below, marked.

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

   *Correction, 2026-09-13.* Adding that missing entry point would not clear
   this blocker, and it is worth being exact about why, because "no command
   exists" invites the wrong fix. `export` consumes three private receipts —
   `summary.json`, `results.jsonl`, `promotion.json` — and the third is the
   binding one. `promotion.json` is written in exactly one place in this
   repository: `infra/deploy.sh`, from `scripts/build_promotion_attestation.py`,
   against a runtime projection whose `function_version` is the Lambda version
   that same deploy just published. No eval run produces it and no run
   directory has ever contained one: of the eleven run directories on this
   machine and the one inside the most recent nightly's `eval-report`
   artifact, twelve hold `summary.json` and `results.jsonl` and none holds
   `promotion.json`. So this blocker is downstream of blocker 3 rather than
   independent of it: the receipts come from a deploy, and there is no deploy
   to take them from.

2. **The evidence would be refused as stale.** The only promoted run is
   `2026-07-12T05:01:17+00:00` (`EVALS.md`, `evals/baseline.json`,
   `docs/eval-report.html`). `require_current_public_evidence` fails with
   "public evidence is stale at verification time" once `now - run_at`
   exceeds the manifest's `max_age_seconds`. That budget is operator-chosen
   at export time via `--freshness-seconds`, so this blocker alone could be
   argued past by declaring a wide budget.

   *Correction, 2026-09-13.* The measurement is not what is wrong here, and it
   is worth saying so plainly so nobody goes looking for an off-by-one to
   repair. `require_current_public_evidence` recomputes `now - run_at` from
   the manifest's own `run_at` rather than trusting its recorded
   `age_seconds`, which is the correct reading, and it returns 63 days against
   a budget an operator would have to declare at 63 days or wider. The gate is
   measuring accurately and the evidence is genuinely that old. Nothing in the
   repository can make this blocker go away; only a newer run can.

3. **The runtime tuple does not match, whatever the budget.** The July run
   attests corpus_version `0938fff0539a`. The live Lambda reports
   `35ec70d6359d` (checked at both `fare.chelseakr.com/version` and the API
   Gateway URL). `compare_runtime_version` compares corpus_version among
   `_RUNTIME_FIELDS` and fails with "runtime version differs from public
   evidence: corpus_version". This blocker is not operator-tunable, and it is
   the honest one: the promoted scores were computed against a corpus the
   deployed assistant no longer serves.

   *Correction, 2026-09-13.* There are three corpora in play, not two, and the
   third is what decides the shape of the fix. `0938fff0539a` is what the
   promoted July scores were computed against; `35ec70d6359d` is what the
   deployed Lambda serves (`as_of` 2026-08-10, eleven documents, five
   agencies); and `assistant.corpus.corpus_version()` at `origin/main` returns
   `10deac978967` — eighteen agencies — which is also what every full run in
   this repository since 2026-08-21 attests, today's nightly included. So
   re-running the evaluation at `main` and exporting the result does not
   satisfy `compare-runtime` either: it swaps one mismatched corpus for a
   different mismatched corpus. What `compare_runtime_version` requires is
   that the evaluated release identity equal the *deployed* one, and
   `scripts/build_promotion_attestation.py::_evaluated_release` enforces the
   same equality a step earlier, over `source_revision`, `config_version`,
   `content_version`, `snapshot_version`, `release_version` and
   `corpus_version` together. Two of the eight fields `compare-runtime`
   checks — `artifact_code_sha256` and `function_version` — are facts about a
   published Lambda version that no local run can attest at all. The only
   procedure that produces all eight consistently is a deploy.

Blocker 3 also means the useful `source_revision` is constrained. The live
runtime attests `180aa043f740c076ec7ec9443f2067b56009c985`, which is an
ancestor of `origin/main` and does contain the renderer, so it is the only
value that could satisfy `compare-runtime` against today's deployment.

*Correction, 2026-09-13.* "Contains the renderer" is true and is not the whole
story, because the build job runs the renderer **out of the `source_revision`
checkout**, not out of `main`. `180aa043` is dated 2026-08-12, so the renderer
it carries predates both improvements this page has had since: ADR 0030's
read-time freshness script (2026-08-29) and the head tags every published page
now states its own address with (2026-09-13). Checked at that commit,
`scripts/build_evidence_site.py` has no `_FRESHNESS_SCRIPT` and
`docs/pages/index.html` has no `<link rel="canonical">`, no
`<meta name="description">` and no `og:`/`twitter:` tags; the renderer there
also predates `--og-card` and `--feeds-dir`, and emits no `robots.txt` or
`sitemap.xml`. `pages.yml` guards that skew correctly — its `--og-card` and
`--feeds-dir` arguments are added only when those paths exist in the `source`
checkout, and neither exists at `180aa043`, so a dispatch would run rather
than fail on an unknown flag. It would simply publish an older page. That is
the real cost of the constraint: satisfying blocker 3 by pinning to the
deployed commit publishes a page that cannot report its own age and cannot be
shared or indexed, which is strictly less than what the repository can render
today. Redeploying the Lambda from a current `main` is what relaxes it, and it
is the same action blocker 3 already requires.

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
evidence-only ref, and dispatch.

**What produces those receipts, and what they cost.** Step 1 below reads three
receipts, and the paragraph that used to introduce it left the impression they
are lying around in `evals/runs/`. They are not, and no amount of re-running
`make eval` puts them there. `evals.runner` writes `summary.json` and
`results.jsonl` and never writes `promotion.json` at all; it only records
`attestation.promotion.eligible: true` on a run invoked as
`--full --promotion --no-cache` against a verified release descriptor.
`promotion.json` itself is composed afterwards by
`scripts/build_promotion_attestation.py` from that summary plus a runtime
projection of the just-published Lambda version, and the only caller that does
either is `infra/deploy.sh`. Today's nightly is instructive about how far an
ordinary run is from qualifying: its attestation records
`"eligible": false, "reasons": ["cache_enabled", "descriptor_unverified",
"gates_failed", "not_promotion_run"]`, four independent disqualifications.

`--no-cache` is where the money goes. The promotion run re-measures every case
against the live provider with nothing served from the content-keyed cache.
The repository's one committed measurement of a cold full run at this scale is
[the 2026-08-22 audit](audits/eval-full-live-2026-08-22.md): 385 cases, 369
answer calls and 730 judge calls all going to Bedrock, **$8.5006** at list
price for 3,898,166 tokens, 813 seconds at four workers. A promotion run is
that run plus the gates, so budget the same order and expect it to fail closed
if the gates do not pass — the most recent nightly scored 272/385, and
`verify_promotion_evidence` refuses anything whose `gate_status` is not
`passed`. Paying for the run does not buy a publication; it buys a verdict.

So step 0 is a deploy, not an export:

```sh
# 0. Deploy. This is what publishes a numbered Lambda version, runs the
#    full/live/uncached promotion evaluation against it, and writes the three
#    receipts to infra/build/promotion/. Needs AWS credentials and Bedrock access,
#    costs real money (see above), and changes what fare.chelseakr.com serves.
./infra/deploy.sh

# 1. Export the canonical manifest from the promoted run's private receipts
#    (infra/build/promotion/ from step 0 — not an evals/runs/ directory).
uv run python scripts/build_evidence_site.py export \
  --summary  infra/build/promotion/summary.json \
  --results  infra/build/promotion/results.jsonl \
  --promotion infra/build/promotion/promotion.json \
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

`--freshness-seconds` is the one number here that is a judgment rather than a
measurement. It becomes `max_age_seconds` in the manifest, and it is what both
`require_current_public_evidence` and the published page's own check measure
against. Until 2026-08-29 it was invisible to readers, so widening it to make an
old run publishable moved a real staleness decision into a field nobody could
see. The page now prints `run_at + max_age_seconds` as the instant its verdict
expires, so a wide budget is a claim made in public and can be argued with.
