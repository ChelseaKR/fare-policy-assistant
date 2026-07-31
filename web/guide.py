"""Guided fare finder: a zero-model-call, statically pre-rendered "which fare
applies to me" walkthrough built entirely from the committed corpus.

The chat modality serves some riders worst: no signal at the stop (persona
research P6/F-11), low literacy, or screen-reader users who find a form easier
to scan than free text. This page costs nothing to serve, calls no model, and
carries zero hallucination risk, because every screen is the agency's
published text, rendered verbatim with its source and fetch date — no
retrieval, no generation, no judgment call.

It is also the sharpest demonstration of the no-determination line the rest of
the assistant enforces: agency, then fare category, then the published
criteria and next step, but the UI never asks the rider anything about
themselves and never computes an answer. The temptation with a form-shaped
walkthrough is always to add "enter your age" or "enter your income" — this
page must not, on purpose.

Note on scope: EXP-01 (a typed, per-agency `FareFact` table) has not been
built yet, so this walks the same agency → document → section structure
`/offline` already renders from `assistant.ingest.Chunk`, at the section
(rider-category) granularity the corpus already carries — rather than a typed
fact table that does not exist in this repo yet. When EXP-01 lands, this
module's tree-building step is the natural place to switch inputs to
`facts.jsonl` without changing the page shape.

Navigation is native `<details>`/`<summary>` disclosure widgets plus a jump
list of plain anchors — no JavaScript is required for any of it; the "print"
button is a progressive enhancement, same as `/offline`.

    GET /guide                      # served by web/handler.py
    uv run python -m web.guide      # write web/guide.html for inspection
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from assistant.ingest import Chunk, load_chunks
from web.offline import (
    _esc,
    _group_by_agency,
    _group_by_doc,
    _snapshot_window,
    _snapshot_window_text,
)

_STYLE = """
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif; color: #1a1f24; background: #fff;
    line-height: 1.55; }
  main { max-width: 44rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  .banner { background: #fff8e6; border: 1px solid #d9c27a; border-radius: 6px;
    padding: 0.6rem 0.9rem; margin: 1rem 0; }
  .rule { background: #eef2f6; border: 1px solid #c7d2dd; border-radius: 6px;
    padding: 0.6rem 0.9rem; margin: 1rem 0; }
  h1 { font-size: 1.5rem; margin: 1rem 0 0.3rem; }
  h2 { font-size: 1.2rem; margin: 1.6rem 0 0.4rem; }
  h3 { font-size: 1.05rem; margin: 0; display: inline; }
  h4 { font-size: 0.98rem; margin: 0; display: inline; color: #1a1f24; }
  p { margin: 0.4rem 0; }
  .jump { margin: 0.6rem 0 1.2rem; padding: 0; }
  .jump li { display: inline; margin-right: 0.9rem; }
  .jump a { color: #1d4ed8; }
  details.agency { border: 1px solid #d6d3cb; border-radius: 8px; margin: 0.7rem 0;
    padding: 0.5rem 0.9rem; }
  details.agency > summary { cursor: pointer; padding: 0.4rem 0.1rem; list-style: revert; }
  details.category { border-left: 3px solid #14532d; border-radius: 4px;
    margin: 0.6rem 0 0.6rem 0.2rem; padding: 0.4rem 0 0.4rem 0.7rem; background: #fafaf7; }
  details.category > summary { cursor: pointer; padding: 0.3rem 0.1rem; list-style: revert; }
  summary:focus-visible { outline: 4px solid #1d4ed8; outline-offset: 3px;
    box-shadow: 0 0 0 2px #ffffff; }
  .passage { white-space: pre-wrap; background: #fff; border: 1px solid #d6d3cb;
    border-radius: 6px; padding: 0.6rem 0.8rem; margin: 0.4rem 0 0.5rem; }
  .src { font-size: 0.88rem; color: #4d5860; }
  .src a { color: #1d4ed8; }
  .next { font-size: 0.92rem; border-top: 1px dashed #c7d2dd;
    padding-top: 0.4rem; margin-top: 0.4rem; }
  .next a { color: #1d4ed8; }
  button { font: inherit; border: 1px solid #14532d; background: #14532d;
    color: #fff; border-radius: 6px; padding: 0.55rem 1.1rem;
    /* WCAG 2.2 AA 2.5.8 Target Size (Minimum): at least 24px. */
    min-height: 2.5rem; cursor: pointer; }
  button:focus-visible, a:focus-visible { outline: 4px solid #1d4ed8;
    outline-offset: 3px; box-shadow: 0 0 0 2px #ffffff; }
  footer { border-top: 1px solid #d6d3cb; margin-top: 2rem; padding-top: 1rem;
    font-size: 0.9rem; color: #4d5860; }
  @media print {
    .banner, button, footer a { color-adjust: exact; }
    button { display: none; }
    details { display: block !important; }
    details > summary { list-style: none; }
  }
"""

_ANCHOR_UNSAFE = re.compile(r"[^a-z0-9]+")


def _anchor(agency: str) -> str:
    return "agency-" + _ANCHOR_UNSAFE.sub("-", agency.lower()).strip("-")


def render_guide(
    chunks: list[Chunk],
    as_of: str | None = None,
    *,
    full_corpus_version: str | None = None,
) -> str:
    from assistant.corpus import corpus_version

    snapshot_start, snapshot_end = _snapshot_window(chunks, as_of)
    active_view_version = corpus_version(chunks)
    full_version = full_corpus_version or corpus_version()
    by_agency = _group_by_agency(chunks)

    jump_items: list[str] = []
    agency_parts: list[str] = []
    for agency, cs in by_agency.items():
        full = cs[0].agency_full
        anchor = _anchor(agency)
        jump_items.append(f'<li><a href="#{anchor}">{_esc(full)}</a></li>')

        category_parts: list[str] = []
        for _doc_id, dcs in _group_by_doc(cs):
            head = dcs[0]
            for c in dcs:
                label = (
                    f"{c.section} — {head.doc_title}" if c.section != head.doc_title else c.section
                )
                category_parts.append(
                    '<details class="category">'
                    f"<summary><h4>{_esc(label)}</h4></summary>"
                    f'<div class="passage">{_esc(c.text)}</div>'
                    f'<p class="src">Source: '
                    f'<a href="{_esc(head.url)}" rel="noopener">{_esc(head.doc_title)}</a> '
                    f"(fetched {_esc(head.fetch_date)})</p>"
                    '<p class="next"><strong>Next step:</strong> read the full published page, '
                    "including how to apply, at the source link above. Have a question about "
                    f'your own situation? <a href="/">Ask the assistant</a>.</p>'
                    "</details>"
                )

        agency_parts.append(
            f'<details class="agency" id="{anchor}">'
            f"<summary><h3>{_esc(full)} ({_esc(agency)})</h3></summary>"
            f"<p>Fare categories published for {_esc(full)}. Open one that sounds like it "
            "might describe you to see the published price, proof required, and how to apply.</p>"
            + "".join(category_parts)
            + "</details>"
        )

    jump = '<ul class="jump">' + "".join(jump_items) + "</ul>"
    body = "\n".join(agency_parts)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Which fare applies to me? — Transit Fare Policy Assistant</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <div class="banner" role="note">
    <strong>Reference implementation: dated snapshots, not live agency pages.</strong>
    {_snapshot_window_text(snapshot_start, snapshot_end)} This is a portfolio
    reference implementation, not an official service. Fare policy can change;
    confirm current fares and deadlines with the agency.
  </div>
  <h1>Which fare applies to me?</h1>
  <p>A guided walkthrough of published fare and reduced-fare categories, built
    with no model call and no network request once loaded: choose your agency,
    then the fare category that sounds like it might describe you, to read the
    agency's own published criteria, price, required proof, and how to apply.</p>
  <div class="rule" role="note">
    This page never asks you anything about yourself &mdash; there is nothing
    to type or select. It does not decide whether you qualify for anything;
    only the agency can do that. It shows you what the agency has published,
    word for word, with a source link so you can check it yourself.
  </div>
  <p><button type="button" id="print-page">Print or save this page</button></p>
  <h2>Choose your agency</h2>
  {jump}
  {body}
  <footer>
    <p>This guide is based on policies published as of the snapshot dates shown.
      {_snapshot_window_text(snapshot_start, snapshot_end)} These are saved
      copies, not live agency pages.</p>
    <p>Corpus version (full) {_esc(full_version)}; active page-view version
      {_esc(active_view_version)} after operator source containment.</p>
    <p><a href="/">Back to the assistant</a> &middot;
      <a href="/offline">Full printable reference</a></p>
  </footer>
</main>
<script>
  document.getElementById("print-page").addEventListener("click", function () {{
    window.print();
  }});
</script>
</body>
</html>
"""


def main() -> int:
    html = render_guide(load_chunks())
    out = Path(__file__).parent / "guide.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
