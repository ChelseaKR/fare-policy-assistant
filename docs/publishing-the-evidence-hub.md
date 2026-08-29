# Publishing the evidence hub

How <https://evals.chelseakr.com/> is published, why it currently cannot be,
and what an operator would have to do first. Everything below was checked
against the repository, the GitHub Actions history, and the two live
endpoints on 2026-08-28; nothing is inferred from the workflow file alone.

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

## The open decision this exposes

`validate_public_manifest` accepts a manifest with `status: "warning"`,
`fresh: false`, `warnings: ["evaluation.stale"]`. `_template_html` renders it
with `STATUS_CLASS: warning` and `STATUS_LABEL: "Verified with freshness
warning"`, and `docs/pages/index.html` styles `.notice.warning` for it. None
of that is reachable: `_template_html`'s only caller is
`render_evidence_site`, which calls `require_current_public_evidence` first,
and that function fails on any status other than `verified`.

So the pipeline has exactly one publishable state, "fresh and matching the
live runtime", and no way to publish the sentence "this evidence is old".
The page that is up cannot be corrected, only replaced by a page that passes
every gate. Whether a stale receipt should be publishable *as* a warning is a
decision for the repository owner, not a defect to be quietly patched: it
loosens an outward-facing gate. It is recorded here rather than acted on.

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
measurement. It becomes `max_age_seconds` in the manifest, it is what
`require_current_public_evidence` later checks against, and it is **not**
rendered onto the page. Widening it to make an old run publishable would move
a real staleness decision into a field no reader can see.
