# Adding Napa Valley Vine Transit (VINE) — parity checklist

Worked through against the scaffold's checklist (`python -m
assistant.scaffold_agency vine`). The bar is the same eval coverage the
existing agencies have. Boxes are checked only where the work is actually
done; the unchecked ones are not oversights, and each says what is left.

## Corpus

- [x] robots.txt and any Content-Signal / permissions reading for this agency's
      host recorded, dated, in the `corpus/manifest.yaml` header comment block.
      `vinetransit.com/robots.txt` checked 2026-08-13: WordPress default plus
      `Crawl-delay: 10`. WP Engine behind Cloudflare; no Content-Signal
      anywhere; every page HTTP 200 to this project's identified user agent,
      first try. The same header block records the Golden Gate Transit NO-GO
      found during this batch: fetchable but not ingestible (ASP.NET WebForms
      wraps every page body in one form element, which the cleaner strips).
- [x] Manifest stanza filled: real `url`, `agency_full`, and a `license_note`
      that states the actual license / Content-Signal. Two documents:
      Fares & Passes (the whole fixed-route policy on one page) and VineGo
      Paratransit Service (zone fares published nowhere else). Four candidate
      pages were fetched and excluded, each with its reason in the manifest:
      the FAQ (contradicts the fares page on the transfer window — ONE hour
      vs 90 minutes), the Summer Youth Pass news page (an undated seasonal
      product), the online store page (purchase mechanics), and the
      connections page (no fare content).
- [x] A Spanish (`language: es`) fares page added if the agency publishes one;
      if it does not, say so in the PR so the multilingual gap is on purpose.
      The Vine publishes Spanish application PDFs and a Spanish VineGo video
      but no Spanish fare page, so `ml-032` and `ml-033` are honest
      cross-lingual cases, the SolTrans pattern.
- [x] `make fetch && make ingest` run; snapshots committed under `corpus/raw/`.
      Fetched through `assistant.ingest fetch`, not by hand. 6 chunks.

## Eval cases

- [x] Each case given a real rider `question` and `required_facts` filled from
      the quoted passage. (No `draft: true` flag ever landed on this branch,
      so there is no `draft_vine.yaml` to delete.)
- [x] Edge-case boundaries this agency actually publishes found and cased:
      the age-80+ complimentary Lifetime Pass, which requires an application,
      not just a birthday (`edge-082`); free 90-minute transfers EXCEPT
      between Routes 10 and 11 and to Route 29 (`edge-083`, which is also the
      tripwire for the excluded FAQ's contradictory one-hour window); the Day
      Pass and 31-Day Pass are not valid on Route 29 while the $125 BART pass
      is (`edge-084`); the 20-Ride Pass burns one, two, or three rides per
      trip depending on the route tier (`edge-085`); and VineGo zone fares of
      $4.00/$8.00 by distance (`edge-086`).
- [x] Cases mirrored into the real suites: 14 cases — ground-051..054,
      edge-082..086, fresh-023, ml-032..033, xagency-015, refuse-031.
      `make coverage` reports no blind spot for VINE (the veteran and
      child-free cells are `-`: the Vine's pages publish neither program).
      The one blind spot the matrix still reports, SolTrans / child free,
      predates this branch.
- [x] Ids were allocated after the County Connection and WestCAT sibling
      branches' so the three cannot collide with each other; renumber on
      merge if other branches land first — nothing but ids changes.

## Gate

- [x] `make verify` green (lint + typecheck + coverage-gated tests + i18n +
      a11y + report-regression + provenance).
- [x] `uv run python -m evals.runner --offline` runs the new cases with the
      deterministic checks passing: all 14 execute, retrieve VINE passages,
      and resolve their citations. Offline uses the mock model, so
      `required_facts` cannot pass there — that is the harness, not these
      cases.
- [ ] New rider-facing behavior validated with a live `make eval` if it touched
      prompts / retrieval / answer (see CONTRIBUTING.md). **Not done.** This
      branch bumps the system prompt to v12 (it must: since #125,
      `tests/test_prompt_agencies.py` fails a corpus agency the prompt does not
      name). The header is marked NOT YET LIVE-VALIDATED per CONTRIBUTING; no
      live run ships with this PR and the 14 cases have never been scored
      against a real model. The provenance waivers in
      `evals/stale_acknowledged.json` say so out loud.
- [ ] Re-check on the next corpus refresh: whether the fares page still says
      90 minutes while the FAQ says one hour (`edge-083` and the manifest
      note); whether the Summer Youth Pass page has gained a year or lapsed
      (`fresh-023`); and whether the Vine has published anything of its own
      about Clipper inter-agency transfers (`xagency-015`).
