"""Print a sentence unique to yolobus-fares among all corpus chunks.

Used by scripts/smoke-production.sh's containment assertion (issue #145) so
that check stops depending on a hand-written fare-period string. A literal
like "All below fares are effective July 1, 2025" breaks every time Yolobus
republishes its fares page -- it already has twice (2026-08-12 and again
2026-08-21) -- and a marker that matches no text in the corpus cannot match a
page rendered from that corpus, so a stale literal makes the containment
check pass whether or not the document is actually exposed.

Deriving the marker from corpus/processed/chunks.jsonl -- the same file the
deploy bundles -- means the containment check follows the corpus instead of
dating it. Mirrors tests/test_web.py::_yolobus_only_sentence_in, minus that
helper's "actually renders on these exact pages" check: this script has no
prior page bodies to test uniqueness against, only the corpus itself, so it
picks the longest sentence that is unique to yolobus-fares among all chunks.

    uv run python scripts/yolobus_fare_period_marker.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CHUNKS_PATH = Path(__file__).resolve().parent.parent / "corpus" / "processed" / "chunks.jsonl"

# A naive ". "-split on fare-table prose produces two kinds of bad candidate:
# huge multi-clause blobs from table rows with no sentence punctuation at all
# (MAX_SENTENCE_LENGTH), and mid-word fragments from abbreviations like
# "vs." (the balanced-parens and letters-ratio checks below). The shortest
# candidate clearing every filter is preferred: short and unique is more
# likely to survive HTML rendering unchanged than a long one is.
MIN_SENTENCE_LENGTH = 40
MAX_SENTENCE_LENGTH = 160
MIN_LETTER_RATIO = 0.85


def _is_clean_sentence(sentence: str) -> bool:
    if not (MIN_SENTENCE_LENGTH <= len(sentence) <= MAX_SENTENCE_LENGTH):
        return False
    if "|" in sentence:  # markdown table row, not prose
        return False
    if sentence.count("(") != sentence.count(")"):  # split mid-abbreviation, e.g. "vs."
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in sentence)
    return letters / len(sentence) > MIN_LETTER_RATIO


def derive_marker(chunks: list[dict]) -> str:
    others = " ".join(c["text"] for c in chunks if c.get("doc_id") != "yolobus-fares")
    candidates = [
        sentence.strip()
        for chunk in chunks
        if chunk.get("doc_id") == "yolobus-fares"
        for sentence in chunk["text"].replace("\n", " ").split(". ")
        if _is_clean_sentence(sentence.strip()) and sentence.strip() not in others
    ]
    if not candidates:
        raise SystemExit("no yolobus-only sentence found in chunks.jsonl")
    return min(candidates, key=len)


def main() -> int:
    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(derive_marker(chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
