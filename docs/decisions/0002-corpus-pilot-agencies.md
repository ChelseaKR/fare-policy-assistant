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

## Re-check (2026-08-12): the Unitrans exclusion holds

Unitrans came up again as the candidate sixth agency, so the June finding was
re-tested rather than assumed. Two things had changed since June: the fetcher
has been rewritten, and a WAF that blocks one client does not necessarily block
every client, so a 403 seen once is not a permanent property of a site. Neither
changed the answer.

Evidence, gathered from a residential California IP rather than a datacenter or
CI runner, using the manifest's own user agent and its 10-second crawl delay:

- `unitrans.ucdavis.edu/robots.txt` returns HTTP 200 and is permissive. Stock
  Drupal file, `User-agent: *`, disallowing only `/core/`, `/profiles/`,
  `/admin/`, `/search/`, `/comment/reply/`, and the `/user/` auth paths. None
  of the fare pages is disallowed. No `Crawl-delay` directive. No
  Content-Signal header on any response from the host.
- `unitrans.ucdavis.edu/sitemap.xml` also returns 200. Both it and robots.txt
  are served from the Cloudflare edge cache (`cf-cache-status: HIT`), which is
  why the two static text files get through while pages do not.
- Every HTML page returns 403 with a Cloudflare "Attention Required!" body:
  `/`, `/fares`, `/passes`, `/fare-policy`, `/other-passes-and-accepted-fares`,
  and the Spanish `/es/passes`.
- `assistant.ingest.fetch_all`, this project's own fetcher, was pointed at
  `/passes` and `/` through a scratch manifest. Both failed 403.
- Control, same user agent, same session: `yolobus.com/fares/`,
  `www.sacrt.com/fares/`, and `hta.org/fares/` all returned 200. The user agent
  is not the problem and the fetcher is not broken.

So robots.txt and the WAF disagree about this site, and the WAF is what a
fetcher actually meets. Reading the pages would mean sending a User-Agent this
project is not, which is the workaround declined above in June. The decision
stands. The way in is to ask UC Davis to allowlist an identified research
crawler, not to disguise the client, and until that happens Unitrans stays out
of the corpus. Anyone re-litigating this should re-run the probes rather than
trust this paragraph: the point of dating it is that it can expire.

Two things were noticed while checking and are recorded here because they would
otherwise be lost, not because this ADR acts on them:

- Unitrans publishes localized `/es/` and `/zh-hans/` pages. That is genuine
  multilingual corpus value the block costs, and an argument for making the
  allowlist request rather than writing Unitrans off.
- The sitemap lists a news item at `/news/zippass-retiring-june-30-0`
  (`lastmod` 2026-06-22) whose slug says the UC Davis ZipPass retired June 30.
  The page itself is behind the same 403, so the claim is unread. The committed
  Yolobus fares snapshot lists a "UC Davis Zip Pass" among the passes accepted
  for unlimited rides. That is a possible staleness in a document already in the
  corpus, not a Unitrans question, and someone should check it against Yolobus.

## Candidate check (2026-08-13): RABA (Redding) is out — robots.txt disallows this fetcher

Redding Area Bus Authority came up as a candidate agency: a North State city
with no corpus neighbor, and deliberately outside the fare ecosystems the
recent additions cluster in. It fails at step one, and unlike Unitrans it
fails in robots.txt itself, not at a WAF.

Evidence, gathered 2026-08-13 (server dates 2026-08-14 UTC) with the manifest's
own user agent:

- `www.rabaride.com/robots.txt` answers HTTP 302 to
  `https://cms3.revize.com/revize/reddingbusauthority/robots.txt` (the site is
  hosted on the Revize government-CMS platform). Following the redirect, as
  RFC 9309 instructs, yields HTTP 200 `text/plain`, and the file is an
  allowlist: `Googlebot` Allow all; `FacebookBot`, `LinkedInBot/1.0`, and
  `Twitterbot` Allow all; `Bingbot` Disallow all; then `User-agent: *` /
  `Disallow: /`.
- This project's fetcher is `fare-policy-assistant-research/0.1`. It is none
  of the named agents, so it falls under `User-agent: *`, which disallows
  everything. No fare page, and no other page, was fetched from the host —
  the check stopped at robots.txt, as it should.
- The authoritative-publisher fallback that saved Elk Grove does not exist
  here. RABA is administered by the City of Redding, and
  `www.cityofredding.gov/robots.txt` is the same Revize-managed shape (302 to
  `files.cityofredding.gov/robots.txt`, then an allowlist naming the same
  crawlers plus Bingbot) ending in the same `User-agent: *` / `Disallow: /`.
  Both candidate publishers close the same door. Nothing was fetched from
  either host.

This is a cleaner exclusion than Unitrans: there, a permissive robots.txt
disagreed with a WAF and the project honored the stricter voice. Here the
published robots.txt speaks directly, and what it says is no. The way in is to
ask RABA (or Revize, whose platform default this allowlist appears to be) to
allow an identified research crawler — not to present a browser's or
Googlebot's name. Anyone re-litigating this should re-run the two robots
fetches rather than trust this paragraph; the point of dating it is that it
can expire.

One thing noticed and recorded so it is not lost: the allowlist admits four
commercial crawlers and blocks everything else — on the RABA host that
includes Bingbot — which reads like a Revize platform default rather than a
considered agency policy. That is an argument for making the allowlist
request, not for treating the file as less binding.
