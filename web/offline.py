"""Offline fare reference: a single static page built from the committed corpus.

Rural and older riders (persona research F-11, P6/P1) often have no signal at
the stop, which is exactly when they need the fare. This renders every agency's
snapshotted policy text into one accessible, printable page, with no model call
and no network: the same dated passages the assistant cites, grouped by agency,
that a rider can save or print ahead of a trip.

It is deterministic (corpus in, HTML out), so a unit test can assert it and run
it through the same structural a11y gate as the chat page.

    GET /offline                      # served by web/handler.py
    uv run python -m web.offline      # write web/offline.html for inspection
"""

from __future__ import annotations

import sys
from pathlib import Path

from assistant import config
from assistant.ingest import Chunk, load_chunks

_STYLE = """
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif; color: #1a1f24; background: #fff;
    line-height: 1.55; }
  main { max-width: 48rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  .banner { background: #fff8e6; border: 1px solid #d9c27a; border-radius: 6px;
    padding: 0.6rem 0.9rem; margin: 1rem 0; }
  h1 { font-size: 1.5rem; margin: 1rem 0 0.3rem; }
  h2 { font-size: 1.2rem; margin: 1.8rem 0 0.4rem; border-bottom: 2px solid #14532d;
    padding-bottom: 0.2rem; }
  h3 { font-size: 1.05rem; margin: 1.1rem 0 0.2rem; }
  h4 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; color: #4d5860; }
  p { margin: 0.4rem 0; }
  .src { font-size: 0.9rem; color: #4d5860; }
  .src a { color: #1d4ed8; }
  .passage { white-space: pre-wrap; background: #fafaf7; border: 1px solid #d6d3cb;
    border-radius: 6px; padding: 0.6rem 0.8rem; margin: 0.3rem 0 0.6rem; }
  button { font: inherit; border: 1px solid #14532d; background: #14532d;
    color: #fff; border-radius: 6px; padding: 0.55rem 1.1rem;
    /* WCAG 2.2 AA 2.5.8 Target Size (Minimum): at least 24px. */
    min-height: 2.5rem; cursor: pointer; }
  button:focus-visible, a:focus-visible { outline: 4px solid #1d4ed8;
    outline-offset: 3px; box-shadow: 0 0 0 2px #ffffff; }
  footer { border-top: 1px solid #d6d3cb; margin-top: 2rem; padding-top: 1rem;
    font-size: 0.9rem; color: #4d5860; }
  @media print { .banner, button, footer a { color-adjust: exact; } button { display: none; } }
"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _group_by_agency(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """Agency key → its chunks, agencies in the configured order, docs and
    sections in corpus order."""
    by_agency: dict[str, list[Chunk]] = {ag: [] for ag in config.KNOWN_AGENCIES}
    for c in chunks:
        by_agency.setdefault(c.agency, []).append(c)
    return {ag: cs for ag, cs in by_agency.items() if cs}


def _group_by_doc(chunks: list[Chunk]) -> list[tuple[str, list[Chunk]]]:
    """A single agency's chunks grouped by document, in corpus order. Shared by
    `/offline` (flat reference) and `/guide` (progressive-disclosure walkthrough)
    so the two pages never drift on how a document's sections are assembled."""
    seen_docs: list[str] = []
    doc_chunks: dict[str, list[Chunk]] = {}
    for c in chunks:
        if c.doc_id not in doc_chunks:
            doc_chunks[c.doc_id] = []
            seen_docs.append(c.doc_id)
        doc_chunks[c.doc_id].append(c)
    return [(doc_id, doc_chunks[doc_id]) for doc_id in seen_docs]


def _snapshot_window(chunks: list[Chunk], as_of: str | None = None) -> tuple[str, str]:
    """Return the earliest and latest snapshot dates represented on a page.

    ``as_of`` remains a supported rendering override for deterministic previews.
    When supplied, it is the latest date; a date earlier than every chunk is
    treated as a single-day preview instead of producing a reversed range.
    """
    dates = sorted({c.fetch_date for c in chunks if c.fetch_date})
    latest = as_of if as_of is not None else (dates[-1] if dates else "")
    eligible = [date for date in dates if not latest or date <= latest]
    earliest = eligible[0] if eligible else latest
    return earliest, latest


def _snapshot_window_text(earliest: str, latest: str) -> str:
    if not latest:
        return "No dated policy snapshots are available."
    if earliest == latest:
        return f"The policy snapshot on this page was fetched on {_esc(latest)}."
    return (
        "The policy snapshots on this page were fetched from "
        f"{_esc(earliest)} through {_esc(latest)}."
    )


def render_offline_reference(
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

    parts: list[str] = []
    for agency, cs in by_agency.items():
        full = cs[0].agency_full
        parts.append(f'<section aria-label="{_esc(full)}">')
        parts.append(f"<h2>{_esc(full)} ({_esc(agency)})</h2>")
        for _doc_id, dcs in _group_by_doc(cs):
            head = dcs[0]
            parts.append(f"<h3>{_esc(head.doc_title)}</h3>")
            parts.append(
                f'<p class="src">Source: '
                f'<a href="{_esc(head.url)}" rel="noopener">{_esc(head.url)}</a> '
                f"(fetched {_esc(head.fetch_date)})</p>"
            )
            for c in dcs:
                if c.section:
                    parts.append(f"<h4>{_esc(c.section)}</h4>")
                parts.append(f'<p class="passage">{_esc(c.text)}</p>')
        parts.append("</section>")

    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offline fare reference — Transit Fare Policy Assistant</title>
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
  <h1>Offline fare reference</h1>
  <p>The snapshotted fare and reduced-fare policy text for every agency, on one
    page you can save or print. No internet is needed once this page has loaded.</p>
  <p><button type="button" id="print-page">Print or save this page</button></p>
  {body}
  <footer>
    <p>This reference is based on policies published as of the snapshot dates
      shown. {_snapshot_window_text(snapshot_start, snapshot_end)} These are
      saved copies, not live agency pages.</p>
    <p>Corpus version (full) {_esc(full_version)}; active page-view version
      {_esc(active_view_version)} after operator source containment.</p>
    <p><a href="/">Back to the assistant</a></p>
  </footer>
</main>
<script>
  // Wired here rather than as an inline onclick so the page carries no
  // 'unsafe-inline' in its CSP: this block is allowed by its sha256 hash.
  document.getElementById("print-page").addEventListener("click", function () {{
    window.print();
  }});
</script>
</body>
</html>
"""


def main() -> int:
    html = render_offline_reference(load_chunks())
    out = Path(__file__).parent / "offline.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
