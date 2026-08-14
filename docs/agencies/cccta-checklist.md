# Adding County Connection (Central Contra Costa Transit Authority) (CCCTA) — parity checklist

Worked through against the scaffold's checklist (`python -m
assistant.scaffold_agency cccta`). The bar is the same eval coverage the
existing agencies have. Boxes are checked only where the work is actually
done; the two that are not checked are not oversights, and each says what is
left.

## Corpus

- [x] robots.txt and any Content-Signal / permissions reading for this agency's
      host recorded, dated, in the `corpus/manifest.yaml` header comment block
      (not just in your head). `countyconnection.com/robots.txt` checked
      2026-08-13: WordPress default plus `Crawl-delay: 10`, nothing disallowed
      but `/wp-admin/`. WP Engine behind Cloudflare; no Content-Signal in
      robots.txt or in any response header; every page HTTP 200 to this
      project's identified user agent, first try, no WAF challenge.
- [x] Manifest stanza filled: real `url`, `agency_full`, and a `license_note`
      that states the actual license / Content-Signal. Five documents: the
      fare-types page, the Clipper Card page, the Transfers page, the RTC
      Discount Card Program page, and the LINK paratransit fares page. The
      agency's own "Fares & Passes" landing page is excluded: its whole body
      renders "Coming soon" (page metadata last modified 2018-05-22).
- [x] A Spanish (`language: es`) fares page added if the agency publishes one;
      if it does not, say so in the PR so the multilingual gap is on purpose.
      County Connection publishes no Spanish policy pages — the Spanish links
      on the RTC page are application-form PDFs, not a policy page — so both
      Spanish cases (`ml-028`, `ml-029`) are honest cross-lingual ones, the
      SolTrans pattern rather than the MST/FAX one. The gap is deliberate.
- [x] `make fetch && make ingest` run; snapshots committed under `corpus/raw/`.
      Fetched through `assistant.ingest fetch` with the manifest's user agent
      and 10-second crawl delay, not by hand. 25 chunks.

## Eval cases

- [x] Each case given a real rider `question` and `required_facts` filled from
      the quoted passage. (The scaffold's draft-suite step was folded into
      writing the cases directly; no `draft: true` flag ever landed on this
      branch, so there is no `draft_cccta.yaml` to delete.)
- [x] Edge-case boundaries this agency actually publishes found and cased (age
      cutoffs, income limits, document alternatives, what stacks with what).
      The boundaries that matter here: the Day Pass is an automatic fare cap,
      not a purchased product (`edge-068`); the youth discount exists on
      Clipper and not in cash (`edge-069`, which also pins the ingest-dedupe
      hazard recorded in the manifest); Medicare-under-65 vs "Medi-Cal is not
      accepted" for the RTC card (`edge-070`); the over-65 steer to the Senior
      Clipper card (`edge-071`); the veterans 50%-rating / aid-and-attendance
      threshold (`edge-072`); the BART-to-bus transfer fare (`edge-073`); and
      children under 6 free with an adult (`edge-074`).
- [x] Cases mirrored into the real suites: `groundedness`, `refusal`,
      `cross_agency`, `multilingual`, `freshness` — matching the coverage the
      other agencies get. 16 cases: ground-043..046, edge-068..074, fresh-021,
      ml-028, ml-029, xagency-012, refuse-029. `make coverage` reports no
      blind spot for CCCTA in any of the seven program columns. The one blind
      spot it still reports, SolTrans / child free, arrived with SolTrans and
      is not CCCTA's to close.
- [x] Ids were allocated after FAX's (the largest merged numbers) and may need
      renumbering if a parallel agency branch merges first; nothing but the
      ids would change, as with the four 2026-08-12/13 additions.

## Gate

- [x] `make verify` green (lint + typecheck + coverage-gated tests + i18n +
      a11y + report-regression + provenance).
- [x] `uv run python -m evals.runner --offline` runs the new cases with the
      deterministic checks passing. All 16 execute, retrieve CCCTA passages,
      and resolve their citations. Offline uses the mock model, so
      `required_facts` cannot pass there for any case in any suite — that is
      the harness, not these cases.
- [ ] New rider-facing behavior validated with a live `make eval` if it touched
      prompts / retrieval / answer (see CONTRIBUTING.md). **Not done.** This
      branch bumps the system prompt to v12 (it must: since #125,
      `tests/test_prompt_agencies.py` fails a corpus agency the prompt does not
      name, so the deferred-prompt pattern the four 2026-08-12/13 agencies used
      is no longer available). The header is marked NOT YET LIVE-VALIDATED per
      CONTRIBUTING; no live run ships with this PR, the 16 cases have never
      been scored against a real model, and `EVALS.md` still describes a corpus
      without CCCTA. The provenance waivers in `evals/stale_acknowledged.json`
      say so out loud. A maintainer with credentials runs the live eval before
      anyone points a rider at CCCTA answers.
- [ ] Re-check on the next corpus refresh: whether the "Fares & Passes" hub
      page still says "Coming soon", and whether Clipper START is still
      described as "a pilot program" (`fresh-021` pins the published framing).
