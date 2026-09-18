#!/usr/bin/env python3
"""What every page this site publishes says about itself, and a check that it does.

Two different jobs publish to ``evals.chelseakr.com``: the ``workflow_dispatch``
promotion pipeline, which renders through ``scripts/build_evidence_site.py``, and
the ``schedule`` nightly job, whose renderer is embedded in
``.github/workflows/pages.yml`` (ADR 0032). They render different pages from
different inputs on purpose, but a canonical link, a share card and a
``robots.txt`` are facts about *the address*, not about either publisher -- and
holding them in two places is how the second publisher came to emit no
``og:image`` at all and to copy a report page carrying no description, no
canonical and no card, while the first publisher's own tests stayed green.

So the vocabulary lives here, in one stdlib-only module both publishers import.
The nightly step runs on a bare ``python3`` with no dependencies installed, which
is why nothing here imports anything outside the standard library, this module
imports nothing from the rest of the repository, and the HTML reader below is
``html.parser`` rather than BeautifulSoup.

``check_site`` is the gate. It reads a *built* site tree offline -- no network, no
renderer -- derives the page list from the tree rather than from any list written
down anywhere, and reports what each page fails to say. Running it against the
output of both publishers is what makes the two agree.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import struct
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

#: The address this site answers on. A canonical link and a sitemap both publish
#: absolute addresses, so this cannot be inferred from the output directory; it is
#: written down, and `render_evidence_site` refuses to publish a CNAME naming a
#: different host, so the two can never quietly disagree.
SITE_ORIGIN = "https://evals.chelseakr.com"

#: The project a share card names. One string, because a card saying something
#: other than the page is a second description of this project, published where
#: nobody rereads it.
SITE_NAME = "Transit Fare Policy Assistant"

#: The pages offered for indexing, in the order the sitemap lists them. The other
#: published files -- the evidence manifest, the release receipt, the history SVG
#: and the share card -- are data a reader reaches through these pages, not pages.
#: ``privacy.html`` (ADR 0033) says what reading the site sends to Google Analytics;
#: both publishers write it, from `privacy_html`, so it cannot differ between them.
INDEXABLE_PAGES: tuple[str, ...] = ("index.html", "privacy.html", "report.html")

#: The pages that publish evidence from one run, and so carry that run's date in
#: their description. The privacy page describes the site, not a run.
EVIDENCE_PAGES: tuple[str, ...] = ("index.html", "report.html")

#: The share-card image, when one is published. A link preview names an absolute
#: address, so the card has to be a file this site actually serves; see
#: `social_image_meta` for why a card naming a file that is not there is worse
#: than no card at all.
OG_CARD_NAME = "og-card.png"
OG_CARD_WIDTH = 1200
OG_CARD_HEIGHT = 630
OG_CARD_ALT = "Evaluation evidence for the Transit Fare Policy Assistant, at evals.chelseakr.com."

_LOC = re.compile(r"<loc>(.*?)</loc>")

# --- Google Analytics 4 (ADR 0033) -------------------------------------------------
#
# The owner decided on 2026-09-17 to run GA4 on every public site in the portfolio,
# with the privacy copy changed to match. Both publishers pass every HTML page they
# write through `with_analytics`, so the loader, the footer opt-out and the policy
# changes that admit them are defined once, here.

#: The one place the measurement ID goes. ``""`` means no GA on any page: no loader,
#: no footer control, no policy change, and the privacy page says the site runs no
#: analytics. ``G-Y359BGWN12`` is the evals.chelseakr.com web stream of GA4 property
#: 554885073 (provisioned 2026-09-17: 14-month retention, Google signals disabled).
GA4_MEASUREMENT_ID = "G-Y359BGWN12"

#: What the privacy page tells a reader about retention; it must match the property.
GA4_DATA_RETENTION = "14 months"

#: The only hostname the loader runs on, so no local render, test or CI job contacts
#: Google. Derived from `SITE_ORIGIN` so the two cannot disagree.
PRODUCTION_HOST = SITE_ORIGIN.split("://", 1)[1]

#: The footer's opt-out, remembered per browser under this ``localStorage`` key and
#: read before gtag.js is ever requested. Renaming it would silently opt every
#: opted-out reader back in.
OPT_OUT_STORAGE_KEY = "fare-policy-evals:analytics-opt-out"

GTAG_ORIGIN = "https://www.googletagmanager.com"
#: Where gtag.js sends hits, for ``connect-src`` and ``img-src``.
COLLECTION_ORIGINS: tuple[str, ...] = (
    "https://*.google-analytics.com",
    "https://*.analytics.google.com",
)

#: ``analytics_storage`` defaults to denied for readers in these regions: the 27 EU
#: member states, Iceland, Liechtenstein and Norway, the United Kingdom, Switzerland.
ANALYTICS_DENIED_REGIONS: tuple[str, ...] = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO", "GB", "CH",
)  # fmt: skip

_MEASUREMENT_ID = re.compile(r"G-[A-Z0-9]{4,20}")

#: The status line after each state change, announced by its ``role="status"`` region.
OPT_OUT_MESSAGES: dict[str, str] = {
    "__MSG_OPTED_OUT__": (
        "Opted out. From the next page you open, this site will not load Google "
        "Analytics in this browser."
    ),
    "__MSG_IS_OUT__": (
        "You have opted out: this site does not load Google Analytics in this browser."
    ),
    "__MSG_BACK_IN__": "Opted back in. Analytics resumes from the next page you open.",
    "__MSG_SIGNAL__": (
        "Analytics is off: your browser sends Global Privacy Control or Do Not Track."
    ),
    "__MSG_NO_STORAGE__": (
        "This browser is blocking site storage, so an opt-out cannot be remembered here. "
        "Global Privacy Control or Do Not Track keeps analytics off."
    ),
}

_ANALYTICS_TEMPLATE = """(function () {
  "use strict";
  var w = window, n = navigator, d = document, KEY = __OPT_OUT_KEY__, OFF = __GA_DISABLE__;
  var store = null;
  try { store = w.localStorage; store.getItem(KEY); } catch (e) { store = null; }
  function optedOut() {
    try { return !!store && store.getItem(KEY) === "1"; } catch (e) { return false; }
  }
  var dnt = n.doNotTrack || w.doNotTrack || n.msDoNotTrack;
  var signal = n.globalPrivacyControl === true || dnt === "1" || dnt === "yes";
  d.addEventListener("DOMContentLoaded", function () {
    var box = d.querySelector("[data-analytics-choice]");
    if (!box) return;
    var button = box.querySelector("button"), status = box.querySelector("[role=status]");
    function render(message) {
      button.textContent = optedOut() ? "Opt back in" : "Opt out of analytics";
      button.hidden = signal || !store;
      status.textContent = message;
      box.hidden = false;
    }
    button.addEventListener("click", function () {
      try {
        if (optedOut()) {
          store.removeItem(KEY);
          w[OFF] = false;
          render(__MSG_BACK_IN__);
        } else {
          store.setItem(KEY, "1");
          w[OFF] = true;
          render(__MSG_OPTED_OUT__);
        }
      } catch (e) {
        store = null;
        render(__MSG_NO_STORAGE__);
      }
    });
    render(
      signal ? __MSG_SIGNAL__ : !store ? __MSG_NO_STORAGE__ : optedOut() ? __MSG_IS_OUT__ : ""
    );
  });
  if (w.location.hostname !== __HOST__) return;
  if (n.globalPrivacyControl === true) return;
  if (dnt === "1" || dnt === "yes") return;
  if (optedOut()) return;
  var kept = [];
  w.location.search.replace(/^\\?/, "").split("&").forEach(function (pair) {
    if (/^utm_(?:source|medium|campaign|term|content|id)=/.test(pair)) kept.push(pair);
  });
  var query = kept.length ? "?" + kept.join("&") : "";
  var ref = /^(https?:\\/\\/[^/?#]+)/.exec(d.referrer || "");
  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "denied", region: __DENIED_REGIONS__
  });
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "granted"
  });
  gtag("js", new Date());
  gtag("config", __ID__, {
    allow_google_signals: false, allow_ad_personalization_signals: false,
    page_location: w.location.origin + w.location.pathname + query,
    page_referrer: ref ? ref[1] + "/" : ""
  });
  var s = d.createElement("script");
  s.async = true;
  s.src = __GTAG_SRC__;
  d.head.appendChild(s);
})();"""

#: The footer's look. Every page this site publishes allows inline styles already.
#: The button is a ``<button>`` because it changes a setting; it reads as a link.
ANALYTICS_STYLE = """<style>
.analytics-footer { max-width: 64rem; margin: 0 auto; padding: 0 1rem 2rem; font-size: .9rem; }
.analytics-footer p { margin: .4rem 0; }
.analytics-toggle { padding: 0; border: 0; background: none; color: inherit; font: inherit;
  text-decoration: underline; cursor: pointer; }
.analytics-toggle:focus-visible { outline: 3px solid #075ea8; outline-offset: 3px; }
.analytics-choice[hidden], .analytics-toggle[hidden] { display: none; }
</style>"""


def measurement_id() -> str | None:
    """`GA4_MEASUREMENT_ID` when it is set and well formed, ``None`` when it is blank.

    Read at call time, so a test can switch GA off. Anything else raises rather than
    being written into a script: a typo should stop the publish, not ship a broken tag.
    """
    value = GA4_MEASUREMENT_ID.strip()
    if not value:
        return None
    if not _MEASUREMENT_ID.fullmatch(value):
        raise ValueError(f"not a GA4 measurement ID (expected G-XXXXXXXXXX): {value!r}")
    return value


def analytics_script() -> str:
    """The inline loader, or ``""`` when no measurement ID is configured."""
    mid = measurement_id()
    if mid is None:
        return ""
    replacements = {
        "__HOST__": json.dumps(PRODUCTION_HOST),
        "__DENIED_REGIONS__": json.dumps(list(ANALYTICS_DENIED_REGIONS)),
        "__ID__": json.dumps(mid),
        "__GTAG_SRC__": json.dumps(f"{GTAG_ORIGIN}/gtag/js?id={mid}"),
        "__OPT_OUT_KEY__": json.dumps(OPT_OUT_STORAGE_KEY),
        "__GA_DISABLE__": json.dumps(f"ga-disable-{mid}"),
        **{token: json.dumps(message) for token, message in OPT_OUT_MESSAGES.items()},
    }
    script = _ANALYTICS_TEMPLATE
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script


def script_source(script: str) -> str:
    """The CSP source expression admitting exactly this inline script."""
    digest = hashlib.sha256(script.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def analytics_footer() -> str:
    """The footer every page carries: the disclosure, the privacy link, the control."""
    link = '<a href="privacy.html">Privacy: what it records and how to turn it off</a>'
    if measurement_id() is None:
        return (
            '<footer class="analytics-footer"><p>This site runs no analytics and sets no '
            f"cookies. {link}.</p></footer>"
        )
    return (
        '<footer class="analytics-footer"><p>Pages on this site use Google Analytics 4 to '
        "count visits, with its advertising features off. It does not load when your "
        f"browser sends Global Privacy Control or Do Not Track. {link}.</p>"
        '<p class="analytics-choice" data-analytics-choice hidden>'
        '<button type="button" class="analytics-toggle">Opt out of analytics</button> '
        '<span class="analytics-status" role="status"></span></p></footer>'
    )


_POLICY = re.compile(r'(<meta http-equiv="Content-Security-Policy"\s+content=")([^"]*)(")')


def extend_policy(policy: str, script: str) -> str:
    """``policy`` admitting this inline loader, gtag.js, and the hosts it reports to.

    Each directive is extended rather than replaced, and one the policy lacks is added
    with only what GA needs: a page whose policy is ``default-src 'none'`` gains an
    ``img-src`` naming the collection hosts and nothing else. No ``'unsafe-inline'``
    is ever added; the loader is admitted by its own digest.
    """
    directives: list[tuple[str, list[str]]] = []
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            directives.append((tokens[0], tokens[1:]))
    additions = {
        "script-src": [script_source(script), GTAG_ORIGIN],
        "connect-src": list(COLLECTION_ORIGINS),
        "img-src": list(COLLECTION_ORIGINS),
    }
    names = [name for name, _ in directives]
    for name, sources in additions.items():
        if name in names:
            existing = directives[names.index(name)][1]
            if existing == ["'none'"]:
                existing.clear()
            existing.extend(source for source in sources if source not in existing)
        else:
            directives.append((name, list(sources)))
            names.append(name)
    return "; ".join(" ".join([name, *sources]) for name, sources in directives)


def with_analytics(page: str) -> str:
    """One published page with GA4 added: loader and style in ``<head>``, footer last.

    Fails closed on a page it cannot place both halves on, because a loader with no
    footer would give a reader of that page no way to opt out.
    """
    head_end = page.find("</head>")
    body_end = page.rfind("</body>")
    if head_end == -1 or body_end == -1 or body_end < head_end:
        raise ValueError("page has no </head> or no </body> to place analytics in")
    script = analytics_script()
    page = page[:body_end] + analytics_footer() + "\n" + page[body_end:]
    head = ANALYTICS_STYLE + "\n"
    if script:
        head = f"<script>{script}</script>\n" + head
    page = page[:head_end] + head + page[head_end:]
    if script:

        def extended(match: re.Match[str]) -> str:
            return match.group(1) + extend_policy(match.group(2), script) + match.group(3)

        page = _POLICY.sub(extended, page)
    return page


_PRIVACY_TITLE = "Privacy: what this site measures"
_PRIVACY_DESCRIPTION = (
    "What Google Analytics records when you read the fare-policy evaluation evidence, "
    "what is switched off, and how to turn it off."
)


def _privacy_body() -> str:
    hosting = (
        "<h2>Hosting</h2><p>The site is static files served by GitHub Pages. GitHub "
        "receives each request for a page, as any web host does, and its handling of that "
        'is covered by the <a href="https://docs.github.com/en/site-policy/privacy-policies/'
        'github-general-privacy-statement">GitHub General Privacy Statement</a>. This '
        "project sees no server log of readers, and this site has no accounts, sign-in or "
        "forms. The rider assistant it evaluates is a separate service with its own "
        "privacy design, described in the repository's DPIA.</p>"
    )
    if measurement_id() is None:
        return (
            "<h1>What this site measures</h1><p>Nothing. This site runs no analytics, loads "
            f"no script from anyone else, and sets no cookies.</p>{hosting}"
        )
    retention = html.escape(GA4_DATA_RETENTION)
    key = html.escape(OPT_OUT_STORAGE_KEY)
    return f"""<h1>What this site measures</h1>
<p>The pages of evals.chelseakr.com use Google Analytics 4 to count visits. Its advertising
features are off, and it does not load at all if your browser asks not to be tracked.</p>
<h2>What Google Analytics records</h2>
<p>When a page loads, Google Analytics records a page view: the page's address, the site you
came from, your browser, device type and screen size, and an approximate location. Google
derives that location from your IP address and says it does not store the address itself. It
also records some interactions Google turns on by default, such as scrolling to the end of a
page, following a link to another site, and downloading a file.</p>
<p>The address it receives is cut down first: everything after the page's path is removed,
except campaign tags (<code>utm_</code> parameters), and a site you arrived from is sent as that
site's address only.</p>
<p>It sets two first-party cookies, <code>_ga</code> and <code>_ga_&hellip;</code>, which let it
tell a returning browser from a new one. They last up to two years. Google LLC receives and
stores the data, and this project keeps it for {retention}.</p>
<h2>What is switched off</h2>
<p>Google signals and ad personalization are off, and the advertising consent settings are
denied for every reader. Nothing collected here is used to show you ads, joined to a Google
account, or sold. This project has no ad account and no other analytics tool.</p>
<h2>Readers in Europe, the UK and Switzerland</h2>
<p>If you are in the European Economic Area, the United Kingdom or Switzerland, analytics storage
defaults to denied. Google Analytics sets no cookie for you, but it still sends Google a
cookieless ping for each page view, without an identifier that links one visit to the next.</p>
<h2>How to turn it off</h2>
<p>Google Analytics does not load at all if your browser sends Global Privacy Control or Do Not
Track. You can also use the <strong>Opt out of analytics</strong> button at the foot of every
page. It stores <code>{key}</code> in this browser's local storage, not in a cookie, and every
page checks it before loading anything from Google. It covers this browser on this device only,
clearing the site's data clears it, and it does not delete Google cookies already set. The same
button reads <strong>Opt back in</strong> once you have opted out.</p>
<h2>What is never measured</h2>
<p>The data files, <code>public-evidence.json</code>, <code>release.json</code> and the feeds,
carry no script, so a program or feed reader that fetches them is not counted.</p>
{hosting}
<section lang="es" aria-labelledby="privacidad">
<h2 id="privacidad">En espa&ntilde;ol</h2>
<p>Las p&aacute;ginas de evals.chelseakr.com usan Google Analytics 4 para contar visitas, con sus
funciones publicitarias desactivadas. No se carga si su navegador env&iacute;a Global Privacy
Control o Do Not Track. Registra la direcci&oacute;n de la p&aacute;gina (recortada a su ruta, sin
otros par&aacute;metros que las etiquetas de campa&ntilde;a <code>utm_</code>), el sitio del que
lleg&oacute; (solo su direcci&oacute;n principal), su navegador y dispositivo, y una
ubicaci&oacute;n aproximada que Google obtiene de su direcci&oacute;n IP. Crea las cookies
<code>_ga</code> y <code>_ga_&hellip;</code>, que duran hasta dos a&ntilde;os; en el Espacio
Econ&oacute;mico Europeo, el Reino Unido y Suiza no crea cookies, pero sigue enviando a Google un
aviso sin cookies por cada vista de p&aacute;gina. Las se&ntilde;ales de Google y la
personalizaci&oacute;n de anuncios est&aacute;n desactivadas; nada se usa para mostrarle anuncios
ni se vende. Google LLC recibe los datos y este proyecto los conserva durante 14 meses. El
bot&oacute;n <strong>Opt out of analytics</strong> (desactivar la anal&iacute;tica) al pie de cada
p&aacute;gina guarda <code>{key}</code> en el almacenamiento local de este navegador; cada
p&aacute;gina lo comprueba antes de cargar nada de Google.</p>
</section>"""


def privacy_html(*, card: bool) -> str:
    """``privacy.html``, identical from both publishers, with GA4 added like every page."""
    url = page_url("privacy.html")
    description = (
        _PRIVACY_DESCRIPTION
        if measurement_id() is not None
        else "This site runs no analytics and sets no cookies."
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{html.escape(_PRIVACY_TITLE)}</title>
{head_meta(title=_PRIVACY_TITLE, description=description, url=url, card=card)}
<style>
body {{ margin: 0; color: #17201b; background: #f7faf8; font: 1rem/1.55 system-ui, sans-serif; }}
main {{ max-width: 46rem; margin: auto; padding: 1.5rem 1rem 2rem; }}
a {{ color: #075ea8; }}
a:focus-visible {{ outline: 3px solid #075ea8; outline-offset: 3px; }}
</style>
</head>
<body>
<main>
<p><a href="index.html">Back to evidence overview</a></p>
{_privacy_body()}
</main>
</body>
</html>
"""
    return with_analytics(page)


def page_url(name: str) -> str:
    """The address a published page answers on. The root is the bare origin.

    A canonical naming ``index.html`` would publish a second address for a page
    that already has one, which is the thing a canonical exists to prevent.
    """
    return f"{SITE_ORIGIN}/" if name == "index.html" else f"{SITE_ORIGIN}/{name}"


def social_image_meta(*, card: bool) -> tuple[str, ...]:
    """The image half of a share card, and the card type that follows from it.

    An ``og:image`` naming a file this site does not serve is worse than none at
    all: the tag is read once, by a crawler, somewhere this project will never
    see the result. So the image tags are emitted only when the card is actually
    being published in the same render, and ``twitter:card`` says ``summary``
    rather than ``summary_large_image`` when there is no large image to show.
    """
    if not card:
        return ('<meta name="twitter:card" content="summary">',)
    address = f"{SITE_ORIGIN}/{OG_CARD_NAME}"
    return (
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta property="og:image" content="{html.escape(address)}">',
        '<meta property="og:image:type" content="image/png">',
        f'<meta property="og:image:width" content="{OG_CARD_WIDTH}">',
        f'<meta property="og:image:height" content="{OG_CARD_HEIGHT}">',
        f'<meta property="og:image:alt" content="{html.escape(OG_CARD_ALT)}">',
        f'<meta name="twitter:image" content="{html.escape(address)}">',
        f'<meta name="twitter:image:alt" content="{html.escape(OG_CARD_ALT)}">',
    )


def social_meta(*, title: str, description: str, url: str, card: bool) -> str:
    """The OpenGraph and Twitter tags for one page."""
    return "\n".join(
        (
            '<meta property="og:type" content="website">',
            f'<meta property="og:site_name" content="{html.escape(SITE_NAME)}">',
            '<meta property="og:locale" content="en_US">',
            f'<meta property="og:url" content="{html.escape(url)}">',
            f'<meta property="og:title" content="{html.escape(title)}">',
            f'<meta property="og:description" content="{html.escape(description)}">',
            *social_image_meta(card=card),
            f'<meta name="twitter:title" content="{html.escape(title)}">',
            f'<meta name="twitter:description" content="{html.escape(description)}">',
        )
    )


def head_meta(*, title: str, description: str, url: str, card: bool) -> str:
    """Everything a page's head has to carry to be findable, as one block.

    For a page assembled somewhere other than a template with markers in it --
    the nightly publisher's ``report.html``, which arrives as a finished
    document from a CI artifact and is given a head rather than rendered with
    one.
    """
    return "\n".join(
        (
            f'<meta name="description" content="{html.escape(description)}">',
            f'<link rel="canonical" href="{html.escape(url)}">',
            social_meta(title=title, description=description, url=url, card=card),
        )
    )


def robots_txt() -> bytes:
    """What a crawler is told at ``/robots.txt``.

    This site is served from its own apex, ``evals.chelseakr.com``, so this file
    lands at the origin root, which is the only place a crawler reads one. (A
    project site published under ``<owner>.github.io/<repo>/`` would put it at a
    subpath nothing ever fetches; that is not the shape here, and the CNAME check
    in `render_evidence_site` is what keeps it from becoming the shape here.)

    Nothing is disallowed. Everything this site publishes is published on purpose:
    the renderer writes a fixed list of files, plus the feeds under ``/feeds/`` --
    a set rather than a list, but a checked one, since `_validated_feeds` refuses
    any entry that is not a feed instead of skipping it -- and the workflow
    asserts the private ones are absent. There is no path here that wants hiding.

    The feeds are not in ``sitemap.xml``, and that is not an oversight: a feed is
    data a reader subscribes to, not a page they land on, which is the same reason
    the evidence manifest and the release receipt are absent from it. They are
    reachable through the ``<link rel="alternate">`` the page carries for the
    combined feed.
    """
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_ORIGIN}/sitemap.xml\n".encode()


def sitemap_xml() -> bytes:
    """The pages, as a sitemap.

    No ``lastmod``. The evidence carries its own dates -- the run, the promotion,
    the runtime release -- and they are on the page; a build date stamped here
    would be a third date, about the rendering rather than about the evidence,
    and a sitemap date is worth publishing only while it is true.
    """
    locations = "".join(
        f"<url><loc>{html.escape(page_url(name))}</loc></url>" for name in INDEXABLE_PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</urlset>\n"
    ).encode()


def png_dimensions(payload: bytes) -> tuple[int, int]:
    """The pixel size in a PNG's own IHDR chunk.

    The card is the one published file a reader never opens: it is fetched by a
    crawler, off the page, and pasted into somebody else's timeline. Nothing
    downstream reports back that it was a text file with a ``.png`` name or that
    it was half the size the ``og:image:width`` beside it claimed, so the header
    is read rather than the filename trusted.
    """
    if not payload.startswith(b"\x89PNG\r\n\x1a\n") or payload[12:16] != b"IHDR":
        raise ValueError("share card must be a PNG whose first chunk is IHDR")
    width, height = struct.unpack(">II", payload[16:24])
    return int(width), int(height)


class _Head(HTMLParser):
    """The head tags of one page, read without a third-party parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self._reading_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        if tag == "title":
            self._reading_title = True
            self.title = ""
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                # First wins: a page that states a tag twice has a problem this
                # check should report against the value it actually declared
                # first, not one silently overwritten by a later duplicate.
                self.meta.setdefault(key, values.get("content", ""))
        elif tag == "link":
            self.links.append((values.get("rel", ""), values.get("href", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._reading_title = False

    def handle_data(self, data: str) -> None:
        if self._reading_title and self.title is not None:
            self.title += data


@dataclass(frozen=True)
class SiteCheck:
    """What `check_site` swept, and what it found wrong.

    ``pages`` is reported alongside ``problems`` so a caller can assert the sweep
    reached something. A check over zero pages reports zero problems, and that is
    the shape of a gate that proves nothing.
    """

    pages: tuple[str, ...]
    problems: tuple[str, ...]


def _read_head(path: Path) -> _Head:
    head = _Head()
    head.feed(path.read_text(encoding="utf-8"))
    head.close()
    return head


def _check_identity(name: str, head: _Head) -> list[str]:
    """Title, description, canonical, and the card repeating all three."""
    problems: list[str] = []
    title = (head.title or "").strip()
    if not title:
        problems.append(f"{name}: no <title>")
    description = head.meta.get("description", "").strip()
    if not description:
        problems.append(f'{name}: no <meta name="description">')
    url = page_url(name)
    canonical = [href for rel, href in head.links if rel.lower() == "canonical"]
    if canonical != [url]:
        found = ", ".join(canonical) if canonical else "nothing"
        problems.append(f"{name}: canonical names {found}, not {url}")
    for key, expected in (
        ("og:title", title),
        ("og:description", description),
        ("og:url", url),
    ):
        value = head.meta.get(key)
        if value is None:
            problems.append(f"{name}: no {key}")
        elif value.strip() != expected:
            problems.append(f"{name}: {key} is {value.strip()!r}, not {expected!r}")
    return problems


def _check_card(root: Path, name: str, head: _Head, *, published: bool) -> list[str]:
    """The share card a page promises has to be the file this site serves."""
    promised = head.meta.get("og:image")
    address = f"{SITE_ORIGIN}/{OG_CARD_NAME}"
    if not published:
        if promised is not None:
            return [f"{name}: promises og:image {promised}, which this site does not serve"]
        return []
    if promised is None:
        return [f"{name}: no og:image, though this build publishes {OG_CARD_NAME}"]
    if promised != address:
        return [f"{name}: og:image is {promised!r}, not {address!r}"]
    problems: list[str] = []
    try:
        width, height = png_dimensions((root / OG_CARD_NAME).read_bytes())
    except ValueError as exc:
        return [f"{name}: {exc}"]
    if (width, height) != (OG_CARD_WIDTH, OG_CARD_HEIGHT):
        problems.append(
            f"{name}: the published card is {width}x{height}, not {OG_CARD_WIDTH}x{OG_CARD_HEIGHT}"
        )
    declared = (head.meta.get("og:image:width"), head.meta.get("og:image:height"))
    if declared != (str(width), str(height)):
        problems.append(
            f"{name}: og:image is declared {declared}, but the file is {width}x{height}"
        )
    if head.meta.get("twitter:card") != "summary_large_image":
        problems.append(
            f"{name}: publishes a card image but twitter:card is not summary_large_image"
        )
    if not head.meta.get("og:image:alt", "").strip():
        problems.append(f"{name}: no og:image:alt")
    return problems


def _sitemap_locations(root: Path) -> tuple[frozenset[str], list[str]]:
    path = root / "sitemap.xml"
    if not path.is_file():
        return frozenset(), ["sitemap.xml is absent"]
    listed = _LOC.findall(path.read_text(encoding="utf-8"))
    problems = [
        f"sitemap.xml lists {url}, which this site does not serve"
        for url in listed
        if not _serves(root, url)
    ]
    return frozenset(listed), problems


def _serves(root: Path, url: str) -> bool:
    """Whether a sitemap entry names a file in this tree, at this origin.

    The prefix is checked rather than stripped blind: a `<loc>` on some other
    host would otherwise be sliced into a name that might happen to exist here,
    and a sitemap pointing somewhere else would pass.
    """
    if not url.startswith(f"{SITE_ORIGIN}/"):
        return False
    return (root / (url[len(SITE_ORIGIN) + 1 :] or "index.html")).is_file()


def _check_robots(root: Path) -> list[str]:
    path = root / "robots.txt"
    if not path.is_file():
        return ["robots.txt is absent"]
    published = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    expected = [line for line in robots_txt().decode("utf-8").splitlines() if line]
    if published != expected:
        return [f"robots.txt says {published}, not {expected}"]
    return []


def check_site(root: Path) -> SiteCheck:
    """Sweep a built site tree and report every page that does not say where it is.

    The page list comes from the tree, not from `INDEXABLE_PAGES` or any other
    list written down somewhere: a page published without a canonical is exactly
    the page nobody remembered to add to a list, so a check driven by a list
    would be blind to it by construction.
    """
    pages = tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*.html")))
    listed, problems = _sitemap_locations(root)
    card_published = (root / OG_CARD_NAME).is_file()
    for name in pages:
        head = _read_head(root / name)
        problems.extend(_check_identity(name, head))
        problems.extend(_check_card(root, name, head, published=card_published))
        if page_url(name) not in listed:
            problems.append(f"{name}: is published but sitemap.xml does not list it")
    problems.extend(_check_robots(root))
    return SiteCheck(pages=pages, problems=tuple(problems))


def main(argv: list[str] | None = None) -> int:
    """``python -m scripts.site_meta <built site directory>``.

    Offline, over an already-built tree, so it can be pointed at whatever either
    publisher produced without re-running either one.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: site_meta.py <built site directory>", file=sys.stderr)
        return 2
    result = check_site(Path(arguments[0]))
    if not result.pages:
        print("no pages found; this check would prove nothing", file=sys.stderr)
        return 1
    for problem in result.problems:
        print(problem, file=sys.stderr)
    print(f"checked {len(result.pages)} page(s): {', '.join(result.pages)}")
    return 1 if result.problems else 0


if __name__ == "__main__":  # pragma: no cover - exercised through `main`
    raise SystemExit(main())
