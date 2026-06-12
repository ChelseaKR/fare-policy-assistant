# ADR 0002: Pilot corpus agencies and fetching rules

Date: 2026-06-12. Status: accepted.

## Decision

Pilot corpus: MST, SBMTD, Yolobus, SacRT. Eleven pages, manifest-driven,
fetched once with an identified user agent
(`fare-policy-assistant-research/0.1`), honoring robots.txt and a 10-second
per-host delay. Snapshots are committed with fetch dates and SHA-256 hashes.

## Substitutions and exclusions

- **Unitrans → SacRT.** Unitrans was in the original plan; its WAF returns
  403 to non-browser clients, including a standard browser user-agent string
  from curl. The project rule is polite fetching only, so we did not work
  around it. SacRT was the named stretch agency and its pages fetch cleanly.
- **sbmtd.gov/reduced/ excluded.** The URL suggests reduced fares; the page
  is a stale 2022 temporary service-reduction notice. Including it would
  pollute retrieval with non-fare content. SBMTD reduced-fare policy lives on
  the Fares & Passes page, which is in the corpus.
- **MST PDF applications deferred.** MST's Courtesy Card program is published
  partly as PDF applications. PDF ingest is out of scope for v1 (open
  question 3 in CLAUDE.md); the Fares page text covers the program criteria.

## Content signals

mst.org serves a Cloudflare Content-Signal of `search=yes, ai-train=no` with
`ai-input` unspecified, alongside `Allow: /` for general agents. This project
does not train on fetched content; it retrieves and quotes it with
attribution and a link back to the source. The signal and our reading of it
are recorded in the manifest so a reviewer can disagree with the reasoning
rather than discover it.
