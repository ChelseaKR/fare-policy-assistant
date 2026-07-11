#!/usr/bin/env python3
"""Close the corpus-freshness loop: version-keyed diff, changelog, staleness lint.

The weekly ``corpus-freshness`` workflow re-fetches and re-processes the manifest
URLs, then needs to decide whether anything *substantive* changed and, if so,
open a reviewable PR describing the change. A raw ``git diff -- corpus/`` cannot
do that: it fires on furniture-only churn (a reordered nav block, a changed
whitespace run) that leaves the policy text — and therefore the answers — intact.

This tool keys the decision on ``assistant.corpus.corpus_version`` (a hash over
chunk text + fetch dates) instead. It runs in two phases:

    # before re-processing, while the OLD processed chunks are still on disk
    corpus_refresh_report.py --snapshot-old /tmp/old.json

    # after re-processing, compares against the snapshot
    corpus_refresh_report.py --report /tmp/old.json --out /tmp/pr-body.md

``--report`` prints ``changed=false`` and exits 0 when the corpus_version is
unchanged (nothing opens). On a real change it:

  * appends a dated entry to ``corpus/CHANGELOG.md`` (added / removed / changed
    documents from ``assistant.corpus.diff_corpus``),
  * lints ``evals/suites/*.yaml`` for staleness — a case whose ``required_facts``
    no longer appear in the scoped corpus, or whose ``forbidden_content`` now
    does, using the SAME matcher as the eval gate (``evals.checks``), and
  * writes a Markdown PR-body fragment to ``--out``.

It is deterministic and makes no model calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

# The eval gate (evals/) is a repo-root package, not pip-installed; put the repo
# root on the path so `evals.checks` imports the same way it does under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import config  # noqa: E402
from assistant.corpus import corpus_version, diff_corpus  # noqa: E402
from assistant.ingest import Chunk, load_chunks  # noqa: E402
from evals.checks import fact_matches, phrase_present  # noqa: E402

# ── snapshot (phase 1) ────────────────────────────────────────────────────────


def write_snapshot(path: Path, chunks: list[Chunk]) -> str:
    """Serialize the current corpus (version + full chunks) so a later ``--report``
    invocation can diff against it without any git plumbing. Returns the version."""
    version = corpus_version(chunks)
    payload = {"corpus_version": version, "chunks": [asdict(c) for c in chunks]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return version


def load_snapshot(path: Path) -> tuple[str, list[Chunk]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    chunks = [Chunk(**c) for c in payload["chunks"]]
    return payload["corpus_version"], chunks


# ── changelog + staleness (phase 2) ───────────────────────────────────────────


def render_changelog_entry(version: str, diff: dict, *, day: str) -> str:
    """A CHANGELOG.md entry for a new corpus version, in the file's existing
    ``## <version> (YYYY-MM-DD)`` shape, listing the changed documents."""
    lines = [f"## {version} ({day})", ""]

    def _bullets(label: str, ids: list[str]) -> None:
        if ids:
            lines.append(f"{label}:")
            lines.extend(f"- {doc_id}" for doc_id in ids)
            lines.append("")

    _bullets("Added", diff["added"])
    _bullets("Removed", diff["removed"])
    _bullets("Changed", diff["changed"])
    if not (diff["added"] or diff["removed"] or diff["changed"]):
        # corpus_version moved without a doc-level diff (e.g. a fetch_date-only
        # change that _doc_hashes also folds in); still record the version.
        lines.append("Corpus version changed with no document added, removed, or changed.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_changelog(entry: str, *, path: Path | None = None) -> None:
    """Insert a new entry directly beneath the header prose, above the newest
    existing ``## `` entry, so the file stays newest-first."""
    path = path or (config.CORPUS_DIR / "CHANGELOG.md")
    text = path.read_text(encoding="utf-8")
    marker = "\n## "
    idx = text.find(marker)
    if idx == -1:
        new_text = text.rstrip() + "\n\n" + entry
    else:
        head, tail = text[: idx + 1], text[idx + 1 :]
        new_text = head + entry + "\n" + tail
    path.write_text(new_text, encoding="utf-8")


def _scoped_text(chunks: list[Chunk], scope: str | None) -> str:
    """All corpus text for an agency scope joined into one blob (or the whole
    corpus when a case declares no ``agency_scope``)."""
    return "\n".join(c.text for c in chunks if scope is None or c.agency == scope)


def lint_stale_cases(suites: list[dict], new_chunks: list[Chunk]) -> list[dict]:
    """Flag eval cases the refreshed corpus no longer supports.

    For each case, a ``required_fact`` that no longer appears in the corpus text
    scoped to the case's ``agency_scope`` is a stale assertion; a
    ``forbidden_content`` phrase that now appears is a regression. Matching reuses
    the eval gate's ``fact_matches`` / ``phrase_present`` (so ``re:`` prefixes and
    case-insensitivity behave identically), never a private reimplementation.
    """
    violations: list[dict] = []
    for suite in suites:
        for case in suite.get("cases", []):
            scope = case.get("agency_scope")
            blob = _scoped_text(new_chunks, scope)
            missing = [f for f in case.get("required_facts", []) if not fact_matches(f, blob)]
            present = [p for p in case.get("forbidden_content", []) if phrase_present(p, blob)]
            if missing or present:
                violations.append(
                    {
                        "id": case.get("id", "<unknown>"),
                        "suite": suite.get("suite", "?"),
                        "missing_required_facts": missing,
                        "present_forbidden_content": present,
                    }
                )
    return violations


def load_suites(suites_dir: Path | None = None) -> list[dict]:
    """Read every eval suite YAML (deterministic; no import of the eval runner so
    the tool stays free of model/answer dependencies)."""
    import yaml

    suites_dir = suites_dir or config.EVAL_SUITES_DIR
    out: list[dict] = []
    for path in sorted(suites_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.setdefault("suite", path.stem)
        out.append(data)
    return out


# ── PR body ───────────────────────────────────────────────────────────────────


def render_pr_body(
    old_version: str, new_version: str, diff: dict, changelog_entry: str, stale: list[dict]
) -> str:
    lines = [
        "## Corpus refresh",
        "",
        f"Corpus version `{old_version}` → `{new_version}`.",
        "",
        "### Document-level changes",
        "",
    ]

    def _section(label: str, ids: list[str]) -> None:
        if ids:
            lines.append(f"- **{label}:** {', '.join(f'`{i}`' for i in ids)}")

    _section("Added", diff["added"])
    _section("Removed", diff["removed"])
    _section("Changed", diff["changed"])
    if not (diff["added"] or diff["removed"] or diff["changed"]):
        lines.append("- Version changed with no document added, removed, or changed.")
    lines += ["", "### Changelog entry", "", "```", changelog_entry.rstrip(), "```", ""]

    lines += ["### Eval staleness lint", ""]
    if not stale:
        lines.append("No eval case asserts a fact the refreshed corpus dropped. ✅")
    else:
        lines.append(
            "The following eval cases may now assert a stale fact — review before merging:"
        )
        lines.append("")
        for v in stale:
            bits = []
            if v["missing_required_facts"]:
                bits.append("missing required_facts: " + ", ".join(v["missing_required_facts"]))
            if v["present_forbidden_content"]:
                bits.append(
                    "forbidden_content now present: " + ", ".join(v["present_forbidden_content"])
                )
            lines.append(f"- `{v['suite']}::{v['id']}` — {'; '.join(bits)}")
    lines.append("")
    lines.append(
        "The normal CI on this PR runs the smoke evals, so the answer-level impact "
        "arrives attached to the diff."
    )
    return "\n".join(lines) + "\n"


# ── output plumbing ───────────────────────────────────────────────────────────


def _emit_changed(changed: bool) -> None:
    """Print ``changed=<bool>`` for humans and append it to $GITHUB_OUTPUT when the
    workflow provides one, so the step's ``outputs.changed`` is set either way."""
    line = f"changed={'true' if changed else 'false'}"
    print(line)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────


def run_report(old_path: Path, out_path: Path | None, *, day: str | None = None) -> bool:
    """Compare the working-tree corpus against the pre-refresh snapshot. Returns
    True when the corpus_version changed (and side effects were applied)."""
    day = day or datetime.now(UTC).date().isoformat()
    old_version, old_chunks = load_snapshot(old_path)
    new_chunks = load_chunks()
    new_version = corpus_version(new_chunks)

    if new_version == old_version:
        return False

    diff = diff_corpus(old_chunks, new_chunks)
    changelog_entry = render_changelog_entry(new_version, diff, day=day)
    append_changelog(changelog_entry)

    stale = lint_stale_cases(load_suites(), new_chunks)
    body = render_pr_body(old_version, new_version, diff, changelog_entry, stale)
    if out_path:
        out_path.write_text(body, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot-old", metavar="PATH", help="record the current corpus snapshot")
    group.add_argument("--report", metavar="PATH", help="diff against a recorded snapshot")
    parser.add_argument("--out", metavar="PATH", help="write the PR-body fragment here")
    args = parser.parse_args(argv)

    if args.snapshot_old:
        version = write_snapshot(Path(args.snapshot_old), load_chunks())
        print(f"snapshot corpus_version={version} -> {args.snapshot_old}")
        return 0

    out_path = Path(args.out) if args.out else None
    changed = run_report(Path(args.report), out_path)
    _emit_changed(changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
