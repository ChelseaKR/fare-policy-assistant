"""Content-Security-Policy helpers for the demo's HTML pages.

The pages ship their CSS and JS in one inline ``<style>`` and one inline
``<script>`` block each (there is no build step and nothing to serve from a CDN).
Rather than allow those with ``'unsafe-inline'`` — which would also allow any
injected inline script — we hash the exact contents of every inline block and
list the ``'sha256-…'`` tokens in ``script-src``/``style-src``. A browser then
runs only the blocks whose hash we published; anything injected is blocked.

Hashes are derived from the *served* body, so the CSP can never drift out of
sync with the markup (see the drift test in tests/test_web.py). Note the browser
hashes the text *between* the tags, so the tokens cover ``<script>``/``<style>``
elements only — not inline ``on*=`` handlers or ``style=""`` attributes, which
these pages therefore avoid.
"""

from __future__ import annotations

import base64
import hashlib
import re

# Non-greedy, DOTALL: capture the text of each inline block. Attributes on the
# opening tag (e.g. ``<script type="...">``) are tolerated and excluded from the
# hash, matching how browsers compute the element hash.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)


def _hash_token(block: str) -> str:
    digest = hashlib.sha256(block.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def script_hashes(html: str) -> list[str]:
    """CSP ``'sha256-…'`` tokens for every inline ``<script>`` block in *html*."""
    return [_hash_token(m) for m in _SCRIPT_RE.findall(html)]


def style_hashes(html: str) -> list[str]:
    """CSP ``'sha256-…'`` tokens for every inline ``<style>`` block in *html*."""
    return [_hash_token(m) for m in _STYLE_RE.findall(html)]


def html_csp(html: str, *, frame_ancestors: str | None = None) -> str:
    """Build the Content-Security-Policy for an HTML page from its own markup.

    ``default-src 'none'`` denies everything by default; ``script-src`` and
    ``style-src`` allow only the hashed inline blocks (``'none'`` if a page has
    none). ``connect-src 'self'`` permits the page's same-origin fetch to
    ``/api/ask``; ``form-action`` and ``base-uri`` are locked down. Pass
    *frame_ancestors* to append a ``frame-ancestors`` directive (the embed).
    """
    styles = style_hashes(html)
    scripts = script_hashes(html)
    style_src = " ".join(styles) if styles else "'none'"
    script_src = " ".join(scripts) if scripts else "'none'"
    csp = (
        f"default-src 'none'; style-src {style_src}; script-src {script_src}; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'"
    )
    if frame_ancestors:
        csp += f"; frame-ancestors {frame_ancestors}"
    return csp
