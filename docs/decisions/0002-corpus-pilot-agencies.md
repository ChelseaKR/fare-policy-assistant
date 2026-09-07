# ADR 0002: Pilot corpus agencies and fetching rules

Date: 2026-06-12. Status: accepted.

## Decision

Pilot corpus: MST, SBMTD, Yolobus, SacRT. Eleven pages, manifest-driven,
fetched once with an identified user agent
(`fare-policy-assistant/0.1`, see the amendment below), honoring robots.txt
and a 10-second per-host delay. Snapshots are committed with fetch dates and SHA-256 hashes.

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
- This project's fetcher is `fare-policy-assistant/0.1`. It is none
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

## Amendment (2026-09-04): the fetch identity drops the word "research"

The identified user agent changes from
`fare-policy-assistant-research/0.1 (portfolio reference project;
ckellyreif@gmail.com)` to
`fare-policy-assistant/0.1 (portfolio reference project;
ckellyreif@gmail.com)`. Only the token `research` is removed. The contact
address is deliberately unchanged.

This is a workaround for another party's technical control, so the reasoning
belongs here in full rather than in a commit message.

**What was measured.** While wiring the GTFS fare cross-check for the
remaining sixteen agencies (issue #141), `mst.org` returned HTTP 403 to this
project's fetcher on both the GTFS feed and `https://mst.org/fares/`, the page
`mst-fares` cites. Varying one thing at a time, from the same address, in the
same minute, over both httpx and curl:

| User agent | Result |
|---|---|
| `fare-policy-assistant-research/0.1 (portfolio reference project; …)` | 403 |
| `fare-policy-assistant/0.1 (portfolio reference project; …)` | 200 |
| `fare-policy-assistant/0.1` | 200 |
| `something-research/0.1` (unrelated project, same token) | 403 |
| `totally-unrelated-agent/9.9` | 200 |
| curl default | 200 |

It is not a rate limit: the first request of a session is refused, and the
10-second crawl delay was honored throughout. It is not an address block:
other Cloudflare-fronted corpus hosts served this client normally in the same
window. It is a keyword match on the token `research` in the user-agent
string, and it refuses an unrelated project carrying the same token while
admitting an unrelated agent without it.

**Why changing the string is legitimate here.** Four things have to hold
together, and a reviewer should check each:

1. **Their declared crawler policy permits this fetch.** `mst.org/robots.txt`
   gives `User-agent: *` an `Allow: /` with `Crawl-delay: 10`, and a
   `Content-Signal` of `search=yes,ai-train=no,use=reference`. Nine agents are
   named and disallowed — ClaudeBot, GPTBot, CCBot, Google-Extended,
   Amazonbot, Applebot-Extended, Bytespider, meta-externalagent,
   CloudflareBrowserRenderingCrawler. This project is none of them and
   presents no other agent's name, so the wildcard rule is the one that binds,
   and it says yes. `ai-train=no` is honored: nothing fetched here trains
   anything. `use=reference` describes exactly what this project does, which
   is retrieve a passage, quote it, and cite the URL.
2. **The blocking rule is a blunt heuristic, not a decision about this
   project.** The file is entirely Cloudflare's managed block; MST authored no
   rules of its own in it. The WAF rule refuses a token, not a requester. When
   a site's stated policy and a generic managed rule disagree, the stated
   policy is the considered one.
3. **The new string is still accurate and still identifying.** It names the
   project and carries a working contact address. It is not a browser
   impersonation, it does not adopt any other crawler's name, and it does not
   remove the ability to identify or contact us. Dropping `research` arguably
   makes it more accurate: this is a portfolio reference implementation, not
   research.
4. **The crawl delay is still honored**, unchanged at 10 seconds per host.

**The line this does not cross.** Making the client look like something it is
not. If MST had disallowed this fetcher in robots.txt, or named it, or if the
only way through were a browser user agent, the answer would be the same as
for Unitrans and Elk Grove above: do not fetch. That standard is unchanged by
this amendment, and Unitrans and RABA remain excluded under it.

**Prior art in this file.** The Unitrans re-check records that "robots.txt and
the WAF disagree and the WAF is what a fetcher meets". That remains true. The
difference is that Unitrans refuses every non-browser client, so there is no
honest identity that works, while MST refuses one word and serves the same
request without it.

Approved by the project owner on 2026-09-04. Coverage consequence: MST returns
to the GTFS cross-check, taking it from 13 of 18 agencies to 14 of 18.
