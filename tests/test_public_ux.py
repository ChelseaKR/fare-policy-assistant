"""Regression checks for the public rider surfaces.

These checks deliberately stay outside ``test_web.py``: they exercise the
static/generator contracts owned by the public UX slice without coupling them
to Lambda routing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from assistant.ingest import load_chunks
from web.embed import EMBED_HTML
from web.guide import render_guide
from web.offline import render_offline_reference

INDEX = Path(__file__).parents[1] / "web" / "index.html"


@pytest.mark.parametrize("renderer", [render_guide, render_offline_reference])
def test_static_modes_disclose_the_complete_snapshot_window(renderer):
    chunks = load_chunks()
    dates = sorted({chunk.fetch_date for chunk in chunks})

    html = renderer(chunks)

    assert f"from {dates[0]} through {dates[-1]}" in html
    assert "not live agency pages" in html
    assert "Corpus version (full)" in html
    assert "active page-view version" in html


def test_main_ui_has_distinct_progress_and_conversation_live_regions():
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")

    status = soup.find(id="status")
    assert status is not None
    assert status.get("role") == "status"
    assert status.get("aria-live") == "polite"
    assert status.get("aria-atomic") == "true"

    transcript = soup.find(id="transcript")
    assert transcript is not None
    assert transcript.get("role") == "log"
    assert transcript.get("aria-live") == "polite"
    assert transcript.get("aria-relevant") == "additions"
    assert transcript.get("aria-label")


def test_embed_answer_region_is_named_and_announced():
    soup = BeautifulSoup(EMBED_HTML, "html.parser")

    status = soup.find(id="status")
    assert status is not None
    assert status.get("role") == "status"
    assert status.get("aria-atomic") == "true"

    answer = soup.find(id="answer")
    assert answer is not None
    assert answer.get("role") == "region"
    assert answer.get("aria-label") == "Assistant answer"
    assert answer.get("aria-live") == "polite"
    assert answer.get("aria-busy") == "false"


def test_main_and_embed_use_strong_visible_focus_treatment():
    main = INDEX.read_text(encoding="utf-8")

    for html in (main, EMBED_HTML):
        assert ":focus-visible" in html
        assert "outline: 4px solid #1d4ed8" in html
        assert "outline-offset: 3px" in html


def test_public_beta_renders_prose_and_ignores_additive_structured_payload():
    html = INDEX.read_text(encoding="utf-8")

    assert "renderStructured" not in html
    assert "ans.innerHTML = render(data.answer);" in html
    assert "The API may expose an additive `structured` field" in html


def test_main_and_embed_strip_combined_citation_tags_from_rider_prose():
    main = INDEX.read_text(encoding="utf-8")

    assert r"(?:,\s*doc:[a-z0-9-]+)*" in main
    assert r"(?:,\s*doc:[a-z0-9-]+)*" in EMBED_HTML


def test_public_evaluation_status_separates_baseline_from_latest_red_run():
    main = INDEX.read_text(encoding="utf-8")
    evidence = (INDEX.parents[1] / "docs" / "pages" / "index.html").read_text(encoding="utf-8")

    for html in (main, evidence):
        assert "Committed promoted baseline" in html
        assert "192" in html and "201" in html
        assert "Latest observed" in html
        assert "190" in html and "175" in html and "186" in html
        assert "cross-agency" in html
        assert "red" in html


def test_only_supported_answers_are_added_to_client_follow_up_history():
    html = INDEX.read_text(encoding="utf-8")

    assert 'if (r.data.kind === "answered")' in html
    assert 'r.data.kind === "refused_input" ? "Question withheld for privacy."' in html
    assert html.index('input.value = "";') < html.index('fetch("/api/ask"')


def test_embed_clears_transient_question_before_request():
    assert EMBED_HTML.index('input.value = "";') < EMBED_HTML.index('fetch("/api/ask"')


def test_rider_page_share_card_names_an_image_that_exists_in_this_repository():
    """A link preview is fetched by a crawler that never reports a 404 back here.

    The rider surface is a Lambda with a fixed route table and no static-asset
    route, so its card points at the copy committed to this repository. That is
    only true while the file is actually committed, which is what this checks --
    a renamed or deleted card would otherwise fail silently, in somebody else's
    timeline.
    """
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")
    card = {
        tag.get("property") or tag.get("name"): tag.get("content") for tag in soup.find_all("meta")
    }

    prefix = "https://raw.githubusercontent.com/ChelseaKR/fare-policy-assistant/main/"
    address = card["og:image"]
    assert address.startswith(prefix)
    assert card["twitter:image"] == address
    committed = Path(__file__).parents[1] / address[len(prefix) :]
    assert committed.is_file()
    assert committed.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_rider_page_share_card_repeats_the_page_it_previews():
    """A card saying something the page does not is an unreviewed second description."""
    soup = BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")
    card = {
        tag.get("property") or tag.get("name"): tag.get("content") for tag in soup.find_all("meta")
    }

    title = soup.title.get_text(strip=True)
    description = card["description"]
    assert description
    assert len(description) <= 200
    assert card["og:title"] == title
    assert card["twitter:title"] == title
    assert card["og:description"] == description
    assert card["twitter:description"] == description
    assert card["twitter:card"] == "summary_large_image"
    canonical = soup.find("link", rel="canonical")
    assert canonical is not None
    assert card["og:url"] == canonical["href"]
    # The preview is where a reader is most likely to mistake this for an agency
    # service, so the disclaimer the page leads with has to survive the trip.
    assert "reference implementation" in description.lower()
