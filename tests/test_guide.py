"""The guided fare finder (`/guide`): deterministic, zero-model-call rendering
of the committed corpus into a "which fare applies to me" walkthrough.

Parity is the load-bearing property: every corpus section a rider could ask
about must be reachable in the rendered guide, since there is no fact table
(EXP-01) yet to check against — the corpus itself is the source of truth this
page must not drop anything from.
"""

from __future__ import annotations

from assistant.ingest import load_chunks
from web.a11y import check_html
from web.guide import render_guide


def test_every_corpus_section_is_reachable_in_the_guide():
    from web.offline import _esc

    chunks = load_chunks()
    html = render_guide(chunks)
    missing = [c.chunk_id for c in chunks if _esc(c.section) not in html]
    assert missing == [], f"sections missing from /guide: {missing}"


def test_every_chunk_text_is_rendered_verbatim():
    chunks = load_chunks()
    html = render_guide(chunks)
    for c in chunks:
        # Escaped the same way `/offline` escapes source text: no paraphrase,
        # no summarization, no model in the loop.
        from web.offline import _esc

        assert _esc(c.text) in html


def test_no_input_fields_anywhere_in_the_page():
    html = render_guide(load_chunks())
    for tag in ("<input", "<textarea", "<select", "<form"):
        assert tag not in html


def test_never_computes_or_claims_eligibility():
    html = render_guide(load_chunks())
    assert "does not decide whether you qualify" in html
    # The excellence bar's specific hazard: no field inviting rider attributes.
    # Every bait is phrased as the page ADDRESSING the rider, because that is the
    # hazard — copy this page authored, not policy text it quotes. The page is
    # required to render every chunk verbatim (test_every_chunk_text_is_rendered_
    # verbatim), so a bare noun phrase here would fire on the agencies' own words:
    # "date of birth" was such a bait until Santa Cruz METRO joined the corpus
    # listing "Identification that displays date of birth (e.g. passports & birth
    # certificates)" among the documents that prove age at the farebox. That is an
    # agency naming an accepted document, the opposite of this page asking a rider
    # for one. Narrowed to "your date of birth", which still catches "enter your
    # date of birth" and "what is your date of birth" and matches no corpus text.
    # The structural guarantee is separate and unweakened: see
    # test_no_input_fields_anywhere_in_the_page.
    for bait in ("enter your age", "what is your age", "your income is", "your date of birth"):
        assert bait not in html.lower()


def test_every_category_carries_a_source_and_next_step():
    html = render_guide(load_chunks())
    # One "Source:" and one "Next step:" per rendered category screen.
    assert html.count("Source:") == html.count("Next step:")
    assert html.count("Source:") > 0


def test_passes_structural_a11y():
    assert check_html(render_guide(load_chunks())) == []


def test_agency_reachable_within_two_taps_of_its_jump_link():
    """Every agency has a jump-list anchor straight to its <details id=...>, and
    each fare category inside is a second, independent disclosure — so from the
    jump list, a rider is at most two expansions from a next-step line."""
    html = render_guide(load_chunks())
    import re

    anchors = re.findall(r'<a href="#([^"]+)">', html)
    assert anchors, "expected a jump list of per-agency anchors"
    for anchor in anchors:
        assert f'id="{anchor}"' in html


def test_as_of_and_corpus_version_present():
    chunks = load_chunks()
    html = render_guide(chunks, as_of="2026-01-01")
    assert "2026-01-01" in html
    assert "Corpus version" in html
