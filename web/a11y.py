"""Structural accessibility checker for every public page (WCAG 2.2 AA, the
parts a static check can verify).

This is the merge gate, in the spirit of a pure-Python structural checker: it
catches the regressions a static analysis can catch — a missing page language,
an unlabeled control, a skipped heading level, a link with no text, a disabled
zoom, a control with no minimum target size in the stylesheet. It does NOT
replace a manual screen-reader pass or the advisory axe/pa11y run in CI; colour
contrast and live-region behaviour need those. What it asserts, it asserts
honestly; what it cannot, it leaves to the human pass recorded in the model card.

Until 2026-08-05 this checked `web/index.html` and nothing else, while three
other pages are served publicly: `/embed`, the widget an agency puts on its own
fare page; `/offline`, the printable dated policy reference for a rider with no
signal at the stop; and `/guide`, the no-input-field walkthrough built for
riders who would rather browse than type. Those last two exist specifically for
low-signal and low-literacy riders, which makes them the pages where a
structural regression would land hardest, and they were the ones nothing
watched. All four passed on the day the gate was widened — the point is that
from here a regression on any of them fails a build instead of shipping.

    uv run python -m web.a11y            # check every public page, exit 1 on issues
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, Tag

PAGE = Path(__file__).parent / "index.html"
MIN_TARGET_REM = 1.5  # 24px at the default 16px root; WCAG 2.2 AA 2.5.8


def _attr(el: Tag, name: str) -> str:
    return str(el.get(name) or "")


def _accessible_name(el: Tag, soup: BeautifulSoup) -> bool:
    if _attr(el, "aria-label").strip():
        return True
    labelledby = _attr(el, "aria-labelledby")
    if labelledby and all(soup.find(id=ref) for ref in labelledby.split()):
        return True
    if el.name == "button" and el.get_text(strip=True):
        return True
    el_id = _attr(el, "id")
    if el_id and soup.find("label", attrs={"for": el_id}):
        return True
    return bool(el.find_parent("label"))


def _min_target_ok(css: str) -> bool:
    """The base `button` rule declares a min-height of at least 24px so controls
    meet the 2.2 target-size floor."""
    base = re.search(r"\bbutton\s*\{[^}]*min-height:\s*([\d.]+)rem", css)
    return base is not None and float(base.group(1)) >= MIN_TARGET_REM


def check_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    issues: list[str] = []

    root = soup.find("html")
    if not (isinstance(root, Tag) and _attr(root, "lang").strip()):
        issues.append("html element is missing a lang attribute")
    if not (soup.title and soup.title.get_text(strip=True)):
        issues.append("page has no non-empty <title>")

    viewport = soup.find("meta", attrs={"name": "viewport"})
    content = _attr(viewport, "content") if isinstance(viewport, Tag) else ""
    if "user-scalable=no" in content or re.search(r"maximum-scale=\s*1\b", content):
        issues.append("viewport disables zoom (fails 1.4.4 Resize Text)")

    h1s = soup.find_all("h1")
    if len(h1s) != 1:
        issues.append(f"expected exactly one <h1>, found {len(h1s)}")
    levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    for prev, cur in zip(levels, levels[1:], strict=False):
        if cur > prev + 1:
            issues.append(f"heading level skips from h{prev} to h{cur}")
            break

    for el in soup.find_all(["input", "textarea", "select", "button"]):
        if not isinstance(el, Tag):
            continue
        if el.name == "input" and _attr(el, "type") in {"hidden", "submit", "button", "reset"}:
            # submit/reset inputs are named by their value; hidden has no UI.
            if _attr(el, "type") == "hidden" or _attr(el, "value").strip():
                continue
        if not _accessible_name(el, soup):
            issues.append(f"<{el.name}> has no accessible name: {str(el)[:60]}")

    for a in soup.find_all("a"):
        if isinstance(a, Tag) and not (a.get_text(strip=True) or _attr(a, "aria-label").strip()):
            issues.append(f"<a> has no discernible text: {str(a)[:60]}")

    for img in soup.find_all("img"):
        if img.get("alt") is None:
            issues.append(f"<img> missing alt: {str(img)[:60]}")

    style = soup.find("style")
    if not (style and _min_target_ok(style.get_text())):
        issues.append("no button min-height >= 24px in CSS (fails 2.5.8 Target Size)")

    return issues


def public_pages() -> dict[str, str]:
    """Every HTML surface a member of the public can reach, rendered.

    `/offline` and `/guide` are generated from the committed corpus and `/embed`
    is a module constant, so all three render here with no network, no model
    call, and no credentials — which is why they can be gate-checked on every
    pull request alongside the static demo page.
    """
    # Imported here, not at module scope: `web.a11y` is also imported by the
    # unit tests purely for `check_html`, and those must not pay for a corpus
    # load to exercise a pure string check.
    from assistant.ingest import load_chunks
    from web import embed
    from web.guide import render_guide
    from web.offline import render_offline_reference

    chunks = load_chunks()
    return {
        "web/index.html": PAGE.read_text(encoding="utf-8"),
        "/embed (agency-embeddable widget)": embed.EMBED_HTML,
        "/offline (printable rider reference)": render_offline_reference(chunks),
        "/guide (guided fare finder)": render_guide(chunks),
    }


def main() -> int:
    failed = False
    for name, html in public_pages().items():
        issues = check_html(html)
        if issues:
            failed = True
            print(f"Accessibility issues in {name}:")
            for i in issues:
                print(f"  - {i}")
    if failed:
        return 1
    print("every public page: structural a11y checks pass (WCAG 2.2 AA, static subset)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
