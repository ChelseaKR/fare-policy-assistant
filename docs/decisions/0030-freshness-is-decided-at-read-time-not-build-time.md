# 0030 — The published page decides its own freshness at read time

Date: 2026-08-29. Status: accepted.

## Context

`scripts/build_evidence_site.py` publishes the evidence hub at
<https://evals.chelseakr.com/>. `render_evidence_site` calls
`require_current_public_evidence` before it renders anything, and that function
refuses any manifest whose status is not `verified` and whose `run_at` is
outside `max_age_seconds` recomputed against the wall clock. This is a good
gate. Stale evidence should not be published.

The gate was also the only place freshness was ever checked. Nothing runs after
a page is deployed, and nothing rebuilds it as it ages. The consequence was
structural rather than cosmetic:

- `validate_public_manifest` accepts `status: "warning"` with
  `warnings: ["evaluation.stale"]`.
- `_template_html` rendered that as `STATUS_CLASS: warning` and
  `STATUS_LABEL: "Verified with freshness warning"`.
- `docs/pages/index.html` styles `.notice.warning` for it.
- None of it was reachable. `_template_html`'s only caller runs
  `require_current_public_evidence` first, which rejects every status but
  `verified`.

So the page had exactly one publishable state and it was "Verified", for as
long as the page stayed up. `evals.chelseakr.com` had been serving one
unchanged verdict for 48 days when this was written, and was structurally
incapable of serving a different one. A page that can only ever return one
answer is not reporting a verdict; it is asserting one. On a repository whose
subject is evaluation honesty, that is the defect worth fixing first: it is the
same shape as a check that is green because it cannot fail.

## Decision

Freshness is decided against the time the page is **read**, not the time it was
built. Concretely:

1. `require_current_public_evidence` is unchanged, and publishing stale
   evidence stays refused. The bug was that publication was the *only* place
   freshness was checked, not that it was checked there.
2. `_template_html` refuses to render anything but `verified`, and its dead
   build-time `warning` branch is deleted rather than left as a branch that
   looks live. A hardcoded "Verified" label with no such refusal would be the
   same lie in fewer lines.
3. The renderer emits `data-expires-at`, which is `run_at + max_age_seconds`,
   printed on the page as a `<time>` element. `max_age_seconds` is an operator
   judgement chosen at export time with `--freshness-seconds`, and until now no
   reader could see it. A budget widened to make an old run publishable is now
   a claim made in public.
4. The page carries one inline script. It reads the two instants already
   printed beside it, compares them to the reader's own clock, and past expiry
   swaps the notice to `.notice.warning`, relabels it "Verified with freshness
   warning", and states the age in days. The strings the dead branch held now
   live where a reader reaches them.

## Consequences

**A read-time check needs the reader's clock, so it needs script.** There is no
other way to know when "now" is on a static page served from a CDN. The page
had run nothing before this. The cost is accepted because the alternative is an
unqualified "Verified" that nothing can retract.

**The security posture does not loosen.** The page keeps `default-src 'none'`
and does not use `'unsafe-inline'`. `_script_csp_hash` computes the SHA-256 of
the exact bytes about to be inlined and publishes it as the `script-src` source
expression, so the policy admits that one script and nothing else. The digest
is computed by the renderer rather than written into the template, so the two
cannot drift: editing either recomputes the other. A mismatched hash is the
worst failure available here, because every gate would stay green while the
check silently never ran, so a test recomputes the digest from the published
bytes.

**The offline guarantee holds.** The script fetches nothing, stores nothing,
and sends nothing anywhere. Every value it reads is already on the page.

**Scripting off is handled explicitly.** The static text carries the expiry
instant and says outright that the status above it was settled at build time
and is not maintained, so the fallback is a checkable claim rather than a bare
"Verified". That is weaker than a computed verdict, and it is stated as such
rather than papered over.

**The proof has to execute.** `make test` runs the published script in node at
four clocks: one day in, the last second of the budget, one second past it, and
48 days past it. A structural assertion that the stale strings appear somewhere
in the file would pass just as happily against a page whose script never runs,
which is the exact failure being repaired. This makes `node` a prerequisite for
the local gate, alongside `msgfmt`. When node is absent the test fails rather
than skips: a skip would report the stale state as proven while nothing had run
it.

**It does not correct the page currently up.** That page was published on
2026-07-12 by a workflow definition that no longer exists and carries none of
this. Every page published from here on can report its own age. The three
blockers in `docs/publishing-the-evidence-hub.md` still stand between the live
page and a replacement.
