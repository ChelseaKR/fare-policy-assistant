"""Corpus identity and change tracking.

The corpus is dated, but until now it had no single, stable name a deployment
could approve and pin to. `corpus_version` is that name: a short, deterministic
hash over the committed chunk content and fetch dates. It changes if and only if
the policy text or its dates change, so an operator can approve version X, set
`FPA_PINNED_CORPUS_VERSION=X`, and be told when a deploy is serving something
else (persona research R2-6, P12/P21).

`diff_corpus` is the other half: given the previous and current chunk sets, it
names which documents were added, removed, or changed. The weekly
corpus-freshness automation is the intended caller, turning a snapshot drift into
a human-readable changelog entry.

    uv run python -m assistant.corpus        # print the current corpus summary
"""

from __future__ import annotations

import hashlib
import json
import sys

from assistant.ingest import Chunk, load_chunks


def _digest(parts: list[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def corpus_version(chunks: list[Chunk] | None = None) -> str:
    """A short, stable id for the corpus content. Order-independent: chunks are
    sorted, so a reprocess that only reorders does not change the version."""
    chunks = chunks if chunks is not None else load_chunks()
    parts: list[str] = []
    for c in sorted(chunks, key=lambda c: c.chunk_id):
        parts += [c.chunk_id, c.fetch_date, c.text]
    return _digest(parts)[:12]


def corpus_summary(chunks: list[Chunk] | None = None) -> dict:
    chunks = chunks if chunks is not None else load_chunks()
    return {
        "corpus_version": corpus_version(chunks),
        "as_of": max((c.fetch_date for c in chunks), default=""),
        "agencies": sorted({c.agency for c in chunks}),
        "documents": len({c.doc_id for c in chunks}),
        "chunks": len(chunks),
    }


def _doc_hashes(chunks: list[Chunk]) -> dict[str, str]:
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    out: dict[str, str] = {}
    for doc_id, cs in by_doc.items():
        parts: list[str] = []
        for c in sorted(cs, key=lambda c: c.chunk_id):
            parts += [c.text, c.fetch_date]
        out[doc_id] = _digest(parts)[:12]
    return out


def diff_corpus(old: list[Chunk], new: list[Chunk]) -> dict:
    """Document-level changes between two corpus snapshots: added, removed, and
    changed doc-ids (a doc changed when its text or fetch date moved)."""
    o, n = _doc_hashes(old), _doc_hashes(new)
    return {
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "changed": sorted(d for d in set(o) & set(n) if o[d] != n[d]),
    }


def main() -> int:
    print(json.dumps(corpus_summary(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
