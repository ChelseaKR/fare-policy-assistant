# 0033: Google Analytics 4 on the evidence hub, guarded and disclosed

Date: 2026-09-17. Status: accepted.

## Context

The owner decided on 2026-09-17 that every public site in the portfolio runs Google Analytics 4,
with privacy pages and claims changed so nothing published becomes false. The property for this
site was provisioned the same day: GA4 property 554885073, web stream measurement ID
`G-Y359BGWN12`, event data retention 14 months, Google signals disabled on the property.

The site is `evals.chelseakr.com`, the evaluation evidence hub. Two publishers write it (ADR
0032): the `workflow_dispatch` promotion pipeline through `scripts/build_evidence_site.py`, and
the scheduled nightly job whose renderer is embedded in `.github/workflows/pages.yml`. Both import
`scripts/site_meta.py` for what a page says about its own address. Every page carries a
`Content-Security-Policy` meta tag that starts from `default-src 'none'` and admits its one inline
script by digest, never by `'unsafe-inline'`.

The rider assistant this repository evaluates is a separate service with its own privacy design
(`docs/dpia.md`). This decision does not touch it.

## Decision

**One transform, used by both publishers.** `site_meta.with_analytics(page)` adds GA4 to a page:
the inline loader and a small style block in `<head>`, a footer with the disclosure, a link to
`privacy.html` and the opt-out control, and the policy changes that admit them. Both publishers
pass every HTML page they write through it, and both write `privacy.html` from
`site_meta.privacy_html`, so the two cannot differ.

**Where the ID lives.** `site_meta.GA4_MEASUREMENT_ID`. It is public, so it is committed as site
configuration. `""` turns GA off: no loader, no control, no policy change, and the footer and
`privacy.html` say the site runs no analytics. A malformed value raises, which stops the publish.

**When nothing loads.** The loader returns before creating `dataLayer` or requesting gtag.js:

- off `evals.chelseakr.com` (`site_meta.PRODUCTION_HOST`, derived from `SITE_ORIGIN`), so no
  local render, test or CI job contacts Google;
- when `navigator.globalPrivacyControl === true`;
- when `navigator.doNotTrack`, `window.doNotTrack` or `navigator.msDoNotTrack` is `"1"` or `"yes"`;
- when `localStorage["fare-policy-evals:analytics-opt-out"]` is `"1"`, which the footer's
  "Opt out of analytics" button writes. The button also sets Google's own
  `window["ga-disable-G-Y359BGWN12"]` and toggles to "Opt back in". It stays `hidden` without
  JavaScript, and under GPC, DNT or blocked storage it is hidden with a status line saying why.

**How it is configured.** Consent Mode v2 defaults deny `ad_storage`, `ad_user_data` and
`ad_personalization` everywhere, and deny `analytics_storage` through `region` for the 27 EU
states, Iceland, Liechtenstein, Norway, the UK and Switzerland, granting it elsewhere. There is no
consent banner, so readers in those regions get no GA cookie and gtag sends Google cookieless
pings, which the owner accepted. The config sets `allow_google_signals: false` and
`allow_ad_personalization_signals: false`, sends `page_location` as origin, path and `utm_*`
parameters only, and sends `page_referrer` as the referring origin.

**The policy.** `extend_policy` extends each page's own policy rather than replacing it:
`script-src` gains the loader's `sha256` digest and `https://www.googletagmanager.com`;
`connect-src` and `img-src` gain `https://*.google-analytics.com` and
`https://*.analytics.google.com`. A directive a page lacks is added with only those sources, so a
`default-src 'none'` page gains nothing else. `'unsafe-inline'` is never added to `script-src`.

**Not a single-page app.** Every page is its own document, and none rewrites its address.

## Consequences

- Reading the site now sends Google a page view with the cut-down address, the referring origin,
  browser and device data and an approximate location, and outside the EEA, UK and Switzerland
  sets the `_ga` cookies for up to two years. `privacy.html` (in the sitemap, linked from every
  footer, English with a Spanish summary) says so. The data files and feeds carry no script.
- `privacy.html` is the third indexable page. Its description names no run date, because it
  describes the site rather than a run; `site_meta.EVIDENCE_PAGES` is the list the run-date
  check reads.
- `tests/test_site_analytics.py` runs the loader in Node against stubbed browser objects and
  holds the policy to the exact digest of the inlined bytes. Removing any guard is caught by a
  negative control that first asserts its sabotage landed. `tests/test_pages_workflow.py` holds
  the nightly job's pages to the same shape.
- **Nothing changes on the live site until a publisher runs.** The dispatch path needs an owner
  dispatch with a source revision that contains this change, and the nightly path is off until
  `vars.NIGHTLY_HUB_PUBLISH_ENABLED` is `true` (ADR 0032).
- **Owner step in the GA4 web stream:** "Form interactions" and "Site search" have nothing to act
  on here and can be turned off.
