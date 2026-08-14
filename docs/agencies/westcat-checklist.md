# Adding WestCAT (Western Contra Costa Transit Authority) — parity checklist

Worked through against the scaffold's checklist (`python -m
assistant.scaffold_agency westcat`). The bar is the same eval coverage the
existing agencies have. Boxes are checked only where the work is actually
done; the unchecked ones are not oversights, and each says what is left.

## Corpus

- [x] robots.txt and any Content-Signal / permissions reading for this agency's
      host recorded, dated, in the `corpus/manifest.yaml` header comment block.
      westcat.org publishes NO robots.txt — both hosts return HTTP 404 — which
      RFC 9309 §2.3.1.3 reads as no crawl restrictions. IIS/Plesk, no
      Cloudflare, no WAF; every page HTTP 200 to this project's identified
      user agent, first try. No Content-Signal anywhere. The manifest's
      10-second crawl delay was honored even though no Crawl-delay exists to
      honor.
- [x] Manifest stanza filled: real `url`, `agency_full`, and a `license_note`
      that states the actual license / Content-Signal. Four documents: All
      Fares, Transfers, Clipper Card, and Buying & Ordering Passes (the
      yolobus-purchasing precedent, so eligibility handoffs can name a
      concrete first step — the Pinole office address and hours).
- [x] A Spanish (`language: es`) fares page added if the agency publishes one;
      if it does not, say so in the PR so the multilingual gap is on purpose.
      WestCAT publishes English-only pages, so `ml-030` and `ml-031` are
      honest cross-lingual cases, the SolTrans pattern.
- [x] `make fetch && make ingest` run; snapshots committed under `corpus/raw/`.
      Fetched through `assistant.ingest fetch`, not by hand. 13 chunks.

## Eval cases

- [x] Each case given a real rider `question` and `required_facts` filled from
      the quoted passage. (No `draft: true` flag ever landed on this branch,
      so there is no `draft_westcat.yaml` to delete.)
- [x] Edge-case boundaries this agency actually publishes found and cased:
      the local-to-LYNX transfer is a $3.25 upcharge, not free (`edge-075`);
      paper transfers are one-hour, no-round-trip, established-points-only
      (`edge-076`); WestCAT honors but does not certify Clipper Access
      (`edge-077`); the East Bay Day Pass cap excludes LYNX (`edge-078`);
      "Photo ID and Medicare card (not Medi-Cal)" (`edge-079`); paratransit is
      priced by distance class, local $1.25 vs regional $3.00, both cheaper
      than the local cash fare (`edge-080`); and the youth Clipper boarding
      minimum equals the adult one (`edge-081`).
- [x] Cases mirrored into the real suites: 17 cases — ground-047..050,
      edge-075..081, fresh-022, ml-030..031, xagency-013..014, refuse-030.
      `make coverage` reports no blind spot for WestCAT (the child-free cell
      is `-` because the fare table says "Under age 6", never "child", and the
      matrix's keyword floor is conservative by design; ground-050 and ml-031
      test it anyway). The one blind spot the matrix still reports,
      SolTrans / child free, predates this branch.
- [x] Ids were allocated after the County Connection sibling branch's
      (ground-043..046, edge-068..074, fresh-021, ml-028..029, xagency-012,
      refuse-029) so the two branches cannot collide with each other;
      renumber on merge if other branches land first — nothing but ids
      changes.

## Gate

- [x] `make verify` green (lint + typecheck + coverage-gated tests + i18n +
      a11y + report-regression + provenance).
- [x] `uv run python -m evals.runner --offline` runs the new cases with the
      deterministic checks passing: all 17 execute, retrieve WestCAT passages,
      and resolve their citations. Offline uses the mock model, so
      `required_facts` cannot pass there for any case in any suite — that is
      the harness, not these cases.
- [ ] New rider-facing behavior validated with a live `make eval` if it touched
      prompts / retrieval / answer (see CONTRIBUTING.md). **Not done.** This
      branch bumps the system prompt to v12 (it must: since #125,
      `tests/test_prompt_agencies.py` fails a corpus agency the prompt does not
      name). The header is marked NOT YET LIVE-VALIDATED per CONTRIBUTING; no
      live run ships with this PR and the 17 cases have never been scored
      against a real model. The provenance waivers in
      `evals/stale_acknowledged.json` say so out loud.
- [ ] Re-check on the next corpus refresh: whether the virtual adult Clipper
      card is still "Free (limited time)" (`fresh-022`), and whether WestCAT's
      Transfers page still honors paper transfers from County Connection at
      Martinez while County Connection's own pages describe Clipper-only
      transfers (`xagency-013` and the manifest's cross-agency finding note).
