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

`version_history` walks git history to build the changelog the agency operator
console (EXP-09, `web/console.py`) reviews: every committed corpus snapshot,
named by the same `corpus_version` hash, with its full chunk set so any two
versions can be diffed without a live checkout. It shells out to `git` and reads
the repo's commit history, so it only runs in development/CI (`make history`),
never inside the deployed Lambda — the console reads the resulting
`corpus/version_history.json` instead.

    uv run python -m assistant.corpus            # print the current corpus summary
    uv run python -m assistant.corpus history     # print the git-backed version history
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime

from assistant import config
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


def _run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=config.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _chunks_from_jsonl_text(text: str) -> list[Chunk]:
    return [Chunk(**json.loads(line)) for line in text.splitlines() if line.strip()]


def version_history(limit: int = 20) -> list[dict]:
    """Every commit that changed the committed corpus, newest first, each
    carrying its full chunk set (so `diff_corpus` can run between any two
    entries offline) plus the same `corpus_version` hash `/version` and
    `FPA_PINNED_CORPUS_VERSION` use, so an operator can pin exactly what a
    past deploy or eval run served. Requires a `git` checkout; see module
    docstring for where this may and may not run."""
    rel_path = str(config.CHUNKS_PATH.relative_to(config.REPO_ROOT))
    log = _run_git(["log", f"-{limit}", "--format=%H\t%cI", "--", rel_path])
    versions: list[dict] = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, committed_at = line.split("\t", 1)
        try:
            chunks = _chunks_from_jsonl_text(_run_git(["show", f"{sha}:{rel_path}"]))
        except (RuntimeError, TypeError, KeyError):
            # A commit whose chunks.jsonl predates the current Chunk schema (or
            # the path did not exist yet at that revision). Skip it rather than
            # failing the whole history — a partial changelog beats none.
            continue
        versions.append(
            {
                "commit": sha[:12],
                "committed_at": committed_at,
                "corpus_version": corpus_version(chunks),
                "agencies": sorted({c.agency for c in chunks}),
                "documents": len({c.doc_id for c in chunks}),
                "chunks": [c.__dict__ for c in chunks],
            }
        )
    return versions


def main(argv: list[str] | None = None) -> int:
    # `argv` defaults to [] (not `sys.argv`), so calling `main()` directly — as
    # the test suite does — is never at the mercy of whatever the enclosing
    # process (e.g. pytest) was itself invoked with; only the __main__ block
    # below wires in the real command line.
    args = argv if argv is not None else []
    cmd = args[0] if args else "summary"
    if cmd == "history":
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "versions": version_history(),
        }
    elif cmd == "summary":
        payload = corpus_summary()
    else:
        raise SystemExit(f"unknown command: {cmd} (expected summary|history)")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
