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

import html
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
INDEXABLE_PAGES: tuple[str, ...] = ("index.html", "report.html")

#: The share-card image, when one is published. A link preview names an absolute
#: address, so the card has to be a file this site actually serves; see
#: `social_image_meta` for why a card naming a file that is not there is worse
#: than no card at all.
OG_CARD_NAME = "og-card.png"
OG_CARD_WIDTH = 1200
OG_CARD_HEIGHT = 630
OG_CARD_ALT = "Evaluation evidence for the Transit Fare Policy Assistant, at evals.chelseakr.com."

_LOC = re.compile(r"<loc>(.*?)</loc>")


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
    """The two pages, as a sitemap.

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
