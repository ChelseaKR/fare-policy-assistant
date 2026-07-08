"""Corpus identity, change tracking, and long-term retention.

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

`corpus/raw/` and `corpus/processed/` are overwritten in place on every
`make fetch` / `make ingest`; only git history remembers what came before.
EXP-05 closes that gap: `archive_version` retains a processed-only, immutable
copy of every distinct corpus content hash under `corpus/versions/<id>/`, so a
past eval run's exact corpus stays loadable by version id long after the
working snapshot has moved on, and `changelog` can build the full "what changed
when" history from what is actually retained rather than a hand-seeded log.

    uv run python -m assistant.corpus            # print the current corpus summary
    uv run python -m assistant.corpus versions    # list retained corpus versions
    uv run python -m assistant.corpus changelog   # print the full retained changelog
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from datetime import UTC, datetime

import yaml

from assistant import config
from assistant.ingest import Chunk, load_manifest
from assistant.ingest import load_chunks as _load_current_chunks

# Manifest fields that identify a document (as opposed to crawl-policy knobs
# like user_agent/crawl_delay_seconds, which are not part of the corpus's
# retained identity).
_MANIFEST_DOC_FIELDS = ("id", "agency", "agency_full", "title", "url", "format", "language")


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


# ── longitudinal retention (EXP-05) ─────────────────────────────────────────


def load_chunks(version: str | None = None) -> list[Chunk]:
    """The live processed corpus (`version=None`), or a retained past version's
    exact chunk set. Past versions are read from
    `corpus/versions/<version>/chunks.jsonl`, written once by `archive_version`
    when that `corpus_version` was first produced. Raises `FileNotFoundError`
    with the list of known versions if `version` was never archived."""
    if version is None:
        return _load_current_chunks()
    path = config.VERSIONS_DIR / version / "chunks.jsonl"
    if not path.exists():
        known = ", ".join(list_versions()) or "(none)"
        raise FileNotFoundError(
            f"corpus version {version!r} is not archived under {config.VERSIONS_DIR} "
            f"(known versions: {known})"
        )
    return _load_current_chunks(path)


def list_versions() -> list[str]:
    """Retained corpus versions, oldest first by `archived_at` (falling back to
    the version id itself for any archive whose `version.json` is missing or
    unreadable, so a hand-placed archive still sorts deterministically)."""
    if not config.VERSIONS_DIR.exists():
        return []
    entries: list[tuple[str, str]] = []
    for d in config.VERSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        archived_at = ""
        meta_path = d / "version.json"
        if meta_path.exists():
            try:
                archived_at = json.loads(meta_path.read_text(encoding="utf-8")).get(
                    "archived_at", ""
                )
            except (json.JSONDecodeError, OSError):
                pass
        entries.append((archived_at or d.name, d.name))
    entries.sort()
    return [name for _, name in entries]


def archive_version(chunks: list[Chunk] | None = None, manifest: dict | None = None) -> str:
    """Retain this corpus content permanently under `corpus/versions/<id>/`.

    Called at the end of `assistant.ingest.process_all()`, once per distinct
    `corpus_version`; a re-run whose content hash already exists is a no-op
    (the first `archived_at` is kept, not overwritten). Retention is
    processed-only — chunks and a slim manifest snapshot, no raw HTML — per the
    EXP-05 repo-size mitigation; raw snapshots still live (unversioned) in
    `corpus/raw/` for the current pull only. Returns the archived version id.
    """
    chunks = chunks if chunks is not None else _load_current_chunks()
    version = corpus_version(chunks)
    version_dir = config.VERSIONS_DIR / version
    chunks_path = version_dir / "chunks.jsonl"
    if chunks_path.exists():
        return version

    version_dir.mkdir(parents=True, exist_ok=True)
    with chunks_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(dataclasses.asdict(c), ensure_ascii=False) + "\n")

    manifest = manifest if manifest is not None else _safe_load_manifest()
    docs = manifest.get("documents", []) if manifest else []
    manifest_snapshot = {"documents": [{k: d.get(k) for k in _MANIFEST_DOC_FIELDS} for d in docs]}
    (version_dir / "manifest.snapshot.yaml").write_text(
        yaml.safe_dump(manifest_snapshot, sort_keys=False), encoding="utf-8"
    )

    summary = corpus_summary(chunks)
    summary["archived_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (version_dir / "version.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return version


def _safe_load_manifest() -> dict:
    try:
        return load_manifest()
    except (OSError, yaml.YAMLError):
        return {}


def changelog(versions: list[str] | None = None) -> list[dict]:
    """The full retained history: a `diff_corpus` entry between each retained
    version and the one before it, oldest first. Chains across
    `list_versions()` (or an explicit ordered subset) so "what changed when" is
    generated from the retained chunk sets, not hand-seeded. The oldest version
    has no predecessor and starts the chain rather than appearing in it."""
    versions = versions if versions is not None else list_versions()
    entries: list[dict] = []
    for prev, cur in zip(versions, versions[1:], strict=False):
        old_chunks = load_chunks(prev)
        new_chunks = load_chunks(cur)
        entries.append(
            {
                "from_version": prev,
                "to_version": cur,
                "as_of": corpus_summary(new_chunks)["as_of"],
                **diff_corpus(old_chunks, new_chunks),
            }
        )
    return entries


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary":
        print(json.dumps(corpus_summary(), indent=2))
    elif cmd == "versions":
        print(json.dumps(list_versions(), indent=2))
    elif cmd == "changelog":
        print(json.dumps(changelog(), indent=2))
    else:
        print(f"unknown command: {cmd} (expected summary|versions|changelog)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
