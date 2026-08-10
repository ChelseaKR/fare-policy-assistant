"""Accessibility-gate edge branches.

The existing test_a11y.py covers the shipped page and the common violations.
These add the remaining branches: the accessible-name escape hatches
(aria-label, aria-labelledby, named submit inputs), the empty-title and
multiple-h1 cases, the image alt rule, and the `main` entry point on both a
clean and a broken page. A gate is only trustworthy if it both fires and stays
silent in the right places.
"""

from __future__ import annotations

from web import a11y
from web.a11y import check_html, main

BASE = """<!doctype html><html lang="en"><head><title>T</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>button {{ min-height: 2.5rem; }}</style></head>
<body><h1>H</h1><h2>S</h2>{body}</body></html>"""


def _page(body=""):
    return BASE.format(body=body)


def test_aria_label_satisfies_accessible_name():
    assert check_html(_page('<textarea aria-label="Ask a question"></textarea>')) == []


def test_aria_labelledby_resolving_to_existing_ids_satisfies_name():
    body = '<span id="lbl">Question</span><textarea aria-labelledby="lbl"></textarea>'
    assert check_html(_page(body)) == []


def test_aria_labelledby_with_dangling_ref_is_flagged():
    body = '<textarea aria-labelledby="missing"></textarea>'
    assert any("accessible name" in i for i in check_html(_page(body)))


def test_named_submit_input_needs_no_label():
    assert check_html(_page('<input type="submit" value="Go">')) == []


def test_hidden_input_is_ignored():
    assert check_html(_page('<input type="hidden" name="csrf">')) == []


def test_empty_title_flagged():
    page = _page().replace("<title>T</title>", "<title></title>")
    assert any("non-empty <title>" in i for i in check_html(page))


def test_multiple_h1_flagged():
    assert any("exactly one <h1>" in i for i in check_html(_page("<h1>second</h1>")))


def test_img_without_alt_flagged():
    assert any("missing alt" in i for i in check_html(_page('<img src="x.png">')))


def test_img_with_alt_passes():
    assert check_html(_page('<img src="x.png" alt="a bus">')) == []


def test_main_passes_on_the_shipped_page(capsys):
    assert main() == 0
    assert "checks pass" in capsys.readouterr().out


def test_main_reports_issues_on_a_broken_page(tmp_path, monkeypatch, capsys):
    broken = tmp_path / "broken.html"
    broken.write_text("<html><body><p>no lang, no title, no h1</p></body></html>")
    monkeypatch.setattr(a11y, "PAGE", broken)
    assert main() == 1
    assert "Accessibility issues" in capsys.readouterr().out


# ── every public surface, not just the demo page ─────────────────────────────


def test_the_gate_covers_every_public_page():
    """Until 2026-08-05 the gate read `web/index.html` alone. `/embed` is what
    an agency puts on its own fare page, and `/offline` and `/guide` exist for
    riders with no signal or who would rather browse than type — the audience a
    structural regression hurts most, and the pages nothing was watching."""
    pages = a11y.public_pages()
    assert set(pages) == {
        "web/index.html",
        "/embed (agency-embeddable widget)",
        "/offline (printable rider reference)",
        "/guide (guided fare finder)",
    }
    for name, html in pages.items():
        assert a11y.check_html(html) == [], name


def test_the_sources_caption_is_a_heading_on_both_answering_surfaces():
    """Where an answer came from is the thing a screen-reader user goes looking
    for, and heading navigation is how they look. The caption was a <strong> on
    both surfaces, which is not a heading-nav target — while the recorded
    transcript the independent audit grades already used <h3>Sources</h3>."""
    from pathlib import Path

    from web import embed

    page = Path(a11y.PAGE).read_text(encoding="utf-8")
    assert 'createElement("h3")' in page and '"sources-h"' in page
    # The embed's only other heading is its h1, so h3 would skip a level.
    assert 'createElement("h2")' in embed.EMBED_HTML and '"sources-h"' in embed.EMBED_HTML
    for source in (page, embed.EMBED_HTML):
        assert ".sources-h { font-size: inherit" in source, (
            "the heading must keep the inline-caption look it replaced, or the "
            "accessibility fix lands as a visual regression"
        )
