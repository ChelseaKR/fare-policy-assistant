"""Per-agency fare-change feeds (Atom and JSON Feed) built from corpus diffs.

Issue #219, RE7. Every corpus refresh already records exactly what changed —
`corpus/versions/<id>/` retains each distinct `corpus_version` with its full
chunk set and a `version.json` carrying the archive date — and the freshness
workflow writes that into a pull request. A repository is a poor subscription
mechanism for the people who most want the signal: an agency's communications
staff, a 511 operator, a downstream assistant. A static feed is a good one.

What this is not: an interpretation. An entry says which documents changed in a
corpus version and when, and links to the snapshot; it does not summarise the
policy change in prose, because that would be this project asserting something
about an agency's fares that no citation stands behind.

Determinism is a requirement, not a nicety. Nothing here reads the clock: a
feed's `updated` is its newest entry's archive timestamp, so regenerating
against an unchanged corpus produces byte-identical files and `--check` can be
a merge gate that the committed feeds match the corpus. A generator that
stamped "now" would rewrite 38 files on every run and the gate would be
meaningless.

Reads only committed corpus archives — no network, no git, no model.

    python -m assistant.feeds           # write docs/pages/feeds/
    python -m assistant.feeds --check   # fail if the committed feeds are stale
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from assistant import config, corpus

FEEDS_DIR = config.REPO_ROOT / "docs" / "pages" / "feeds"
CNAME_PATH = config.REPO_ROOT / "docs" / "pages" / "CNAME"
DEFAULT_SITE_URL = "https://evals.chelseakr.com"
# Where a subscriber goes to read the snapshot an entry describes. Retained
# corpus versions are append-only (`archive_version` never overwrites one), so
# a path under `main` stays valid for the life of the archive.
REPO_TREE_URL = "https://github.com/ChelseaKR/fare-policy-assistant/tree/main"
COMBINED_SLUG = "all"
# A stable, non-dereferenceable id space for feeds and entries, per RFC 4151.
# The date is the tag's minting date and never moves; changing it would change
# every entry id and re-notify every subscriber.
TAG_PREFIX = "tag:evals.chelseakr.com,2026:fare-policy-assistant"
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ChangeEntry:
    """One corpus version, as it touched one agency (or, combined, all of them)."""

    corpus_version: str
    archived_at: str
    as_of: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    first_snapshot: bool = False

    @property
    def documents(self) -> tuple[str, ...]:
        return tuple(sorted({*self.added, *self.removed, *self.changed}))

    @property
    def summary(self) -> str:
        if self.first_snapshot:
            return f"First retained snapshot: {len(self.added)} document(s)."
        parts = [
            f"{len(self.added)} added" if self.added else "",
            f"{len(self.changed)} changed" if self.changed else "",
            f"{len(self.removed)} removed" if self.removed else "",
        ]
        return ", ".join(p for p in parts if p) + "."


def agency_slug(agency: str) -> str:
    """A filename-safe id for an agency name.

    Deliberately lossy in one direction only: two agencies whose names collide
    after slugging would overwrite each other's feed, so `build_feeds` asserts
    the slugs are distinct rather than trusting this.
    """
    return _SLUG_STRIP.sub("-", agency.lower()).strip("-") or "agency"


def site_url() -> str:
    """The published site's origin, read from the CNAME the Pages job serves,
    so the feed's own links cannot drift from where it is actually hosted."""
    try:
        host = CNAME_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SITE_URL
    return f"https://{host}" if host else DEFAULT_SITE_URL


def _version_metadata(version: str) -> dict[str, str]:
    path = config.VERSIONS_DIR / version / "version.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: str(v) for k, v in loaded.items() if isinstance(v, str)}


def _agency_of_documents(chunks: list[corpus.Chunk]) -> dict[str, str]:
    return {chunk.doc_id: chunk.agency for chunk in chunks}


def change_entries() -> dict[str, list[ChangeEntry]]:
    """Every retained corpus version, split by the agency it touched.

    Newest first within each agency, which is the order a feed reader expects.
    The oldest retained version has no predecessor to diff against, so its
    documents are reported as a first snapshot rather than as changes — saying
    "52 documents changed" about the beginning of the record would be an
    absence rendered as an event.
    """
    versions = corpus.list_versions()
    per_agency: dict[str, list[ChangeEntry]] = {}
    previous: list[corpus.Chunk] | None = None
    for version in versions:
        chunks = corpus.load_chunks(version)
        meta = _version_metadata(version)
        owners = _agency_of_documents(chunks)
        if previous is None:
            diff = {"added": sorted(owners), "removed": [], "changed": []}
        else:
            diff = corpus.diff_corpus(previous, chunks)
            owners = {**_agency_of_documents(previous), **owners}
        _collect(per_agency, version, meta, diff, owners, first=previous is None)
        previous = chunks
    return {agency: list(reversed(entries)) for agency, entries in sorted(per_agency.items())}


def _collect(
    per_agency: dict[str, list[ChangeEntry]],
    version: str,
    meta: dict[str, str],
    diff: dict[str, list[str]],
    owners: dict[str, str],
    *,
    first: bool,
) -> None:
    by_agency: dict[str, dict[str, list[str]]] = {}
    for kind in ("added", "removed", "changed"):
        for doc_id in diff.get(kind, []):
            agency = owners.get(doc_id, "(unknown)")
            by_agency.setdefault(agency, {"added": [], "removed": [], "changed": []})
            by_agency[agency][kind].append(doc_id)
    for agency, kinds in by_agency.items():
        per_agency.setdefault(agency, []).append(
            ChangeEntry(
                corpus_version=version,
                archived_at=meta.get("archived_at", ""),
                as_of=meta.get("as_of", ""),
                added=tuple(sorted(kinds["added"])),
                removed=tuple(sorted(kinds["removed"])),
                changed=tuple(sorted(kinds["changed"])),
                first_snapshot=first,
            )
        )


def _merge_combined(per_agency: dict[str, list[ChangeEntry]]) -> list[ChangeEntry]:
    """One entry per corpus version across every agency, newest first."""
    merged: dict[str, ChangeEntry] = {}
    for entries in per_agency.values():
        for entry in entries:
            existing = merged.get(entry.corpus_version)
            if existing is None:
                merged[entry.corpus_version] = entry
                continue
            merged[entry.corpus_version] = ChangeEntry(
                corpus_version=entry.corpus_version,
                archived_at=entry.archived_at,
                as_of=entry.as_of,
                added=tuple(sorted({*existing.added, *entry.added})),
                removed=tuple(sorted({*existing.removed, *entry.removed})),
                changed=tuple(sorted({*existing.changed, *entry.changed})),
                first_snapshot=entry.first_snapshot,
            )
    return sorted(merged.values(), key=lambda e: (e.archived_at, e.corpus_version), reverse=True)


def _entry_title(agency: str, entry: ChangeEntry) -> str:
    return f"{agency} — corpus {entry.corpus_version} ({entry.summary})"


def _entry_id(agency: str, entry: ChangeEntry) -> str:
    return f"{TAG_PREFIX}:{agency_slug(agency)}:{entry.corpus_version}"


def _snapshot_url(entry: ChangeEntry) -> str:
    return f"{REPO_TREE_URL}/corpus/versions/{entry.corpus_version}"


def _entry_body(agency: str, entry: ChangeEntry) -> str:
    lines = [
        f"Corpus version {entry.corpus_version}, archived {entry.archived_at or 'unknown'}.",
        f"Documents as of {entry.as_of or 'unknown'}.",
        entry.summary,
    ]
    for kind, docs in (
        ("Added", entry.added),
        ("Changed", entry.changed),
        ("Removed", entry.removed),
    ):
        if docs:
            lines.append(f"{kind}: {', '.join(docs)}")
    lines.append(f"Agency: {agency}.")
    return "\n".join(lines)


def _feed_updated(entries: list[ChangeEntry]) -> str:
    """The feed's own timestamp: its newest entry's, never the clock. An empty
    feed carries the epoch rather than today, so an empty archive cannot look
    like a fresh publication."""
    return max(
        (e.archived_at for e in entries if e.archived_at), default="1970-01-01T00:00:00+00:00"
    )


def atom_feed(agency: str, entries: list[ChangeEntry], *, base: str) -> str:
    slug = agency_slug(agency)
    self_url = f"{base}/feeds/{slug}.xml"
    title = "All agencies" if agency == COMBINED_SLUG else agency
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>Fare-policy corpus changes — {xml_escape(title)}</title>",
        f"  <id>{TAG_PREFIX}:{slug}</id>",
        f"  <updated>{_feed_updated(entries)}</updated>",
        f'  <link rel="self" href="{xml_escape(self_url)}"/>',
        f'  <link rel="alternate" href="{xml_escape(base)}/"/>',
        "  <author><name>fare-policy-assistant</name></author>",
        "  <subtitle>Document-level changes to the published fare pages this "
        "project snapshots. Not an interpretation of the policy change.</subtitle>",
    ]
    for entry in entries:
        out += [
            "  <entry>",
            f"    <title>{xml_escape(_entry_title(title, entry))}</title>",
            f"    <id>{_entry_id(agency, entry)}</id>",
            f"    <updated>{entry.archived_at or _feed_updated(entries)}</updated>",
            f'    <link rel="alternate" href="{xml_escape(_snapshot_url(entry))}"/>',
            f'    <content type="text">{xml_escape(_entry_body(title, entry))}</content>',
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def json_feed(agency: str, entries: list[ChangeEntry], *, base: str) -> str:
    slug = agency_slug(agency)
    title = "All agencies" if agency == COMBINED_SLUG else agency
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"Fare-policy corpus changes — {title}",
        "home_page_url": f"{base}/",
        "feed_url": f"{base}/feeds/{slug}.json",
        "description": (
            "Document-level changes to the published fare pages this project "
            "snapshots. Not an interpretation of the policy change."
        ),
        "items": [
            {
                "id": _entry_id(agency, entry),
                "url": _snapshot_url(entry),
                "title": _entry_title(title, entry),
                "content_text": _entry_body(title, entry),
                "date_published": entry.archived_at,
                "_fare_policy_assistant": {
                    "corpus_version": entry.corpus_version,
                    "as_of": entry.as_of,
                    "added": list(entry.added),
                    "changed": list(entry.changed),
                    "removed": list(entry.removed),
                    "first_snapshot": entry.first_snapshot,
                },
            }
            for entry in entries
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def build_feeds() -> dict[str, str]:
    """Every feed file this corpus produces, as {filename: content}."""
    base = site_url()
    per_agency = change_entries()
    slugs = {agency_slug(agency) for agency in per_agency}
    if len(slugs) != len(per_agency):
        raise ValueError(f"agency names collide after slugging: {sorted(per_agency)}")
    if COMBINED_SLUG in slugs:
        raise ValueError(f"an agency slugs to the reserved combined feed name {COMBINED_SLUG!r}")
    files: dict[str, str] = {}
    for agency, entries in per_agency.items():
        slug = agency_slug(agency)
        files[f"{slug}.xml"] = atom_feed(agency, entries, base=base)
        files[f"{slug}.json"] = json_feed(agency, entries, base=base)
    combined = _merge_combined(per_agency)
    files[f"{COMBINED_SLUG}.xml"] = atom_feed(COMBINED_SLUG, combined, base=base)
    files[f"{COMBINED_SLUG}.json"] = json_feed(COMBINED_SLUG, combined, base=base)
    return files


def write_feeds(directory: Path | None = None) -> list[Path]:
    target = FEEDS_DIR if directory is None else directory
    target.mkdir(parents=True, exist_ok=True)
    files = build_feeds()
    for stale in sorted(target.iterdir()):
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    written = []
    for name, content in sorted(files.items()):
        path = target / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def stale_feeds(directory: Path | None = None) -> list[str]:
    """Filenames whose committed content does not match the corpus. Empty is
    clean; this is what `make feeds-check` gates on."""
    target = FEEDS_DIR if directory is None else directory
    files = build_feeds()
    stale = []
    for name, content in sorted(files.items()):
        path = target / name
        try:
            if path.read_text(encoding="utf-8") != content:
                stale.append(name)
        except OSError:
            stale.append(name)
    if target.exists():
        stale += sorted(p.name for p in target.iterdir() if p.is_file() and p.name not in files)
    return sorted(set(stale))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        stale = stale_feeds()
        if stale:
            print(
                "feeds: committed files do not match the corpus: " + ", ".join(stale),
                file=sys.stderr,
            )
            print("run `make feeds` and commit the result", file=sys.stderr)
            return 1
        print(f"feeds: {len(build_feeds())} committed file(s) match the corpus")
        return 0
    written = write_feeds()
    print(f"wrote {len(written)} feed file(s) -> {FEEDS_DIR}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via tests/test_feeds.py
    raise SystemExit(main())
