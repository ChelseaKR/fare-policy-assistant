"""Contribution kit: scaffold the files for adding a new agency (EXP-10).

    python -m assistant.scaffold_agency <agency_id> [--agency-full "..."] \
        [--url https://.../fares/] [--write]

Adding a sixth agency should be a guided PR, not bespoke authoring. This command
emits three artifacts a contributor then fills in:

  1. a manifest stanza template (to stdout; ``--write`` appends it, commented
     out, to corpus/manifest.yaml) in the exact shape corpus/manifest.yaml uses,
     plus a robots/permissions reminder;
  2. draft eval-case skeletons — one per already-ingested chunk for the agency —
     written to evals/suites/draft_<id>.yaml with ``draft: true`` on every case;
  3. a parity checklist at docs/agencies/<id>-checklist.md.

The draft cases are deliberately un-runnable: ``evals.runner.validate_cases``
raises if any case carries ``draft: true``. That is the safety rail the roadmap
item requires — an auto-drafted skeleton can never be committed into eval
results until a human has written its question, filled ``required_facts``, and
removed the flag. Run this only after ``make fetch && make ingest`` so the new
agency's chunks exist; without them the manifest stanza and checklist are still
emitted, but there is nothing to draft cases from yet.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from assistant import config
from assistant.ingest import load_chunks

# ── manifest stanza ──────────────────────────────────────────────────────────


def build_manifest_stanza(
    agency_id: str,
    agency_full: str,
    url: str,
    *,
    title: str = "Fares",
    language: str = "en",
) -> str:
    """Return a manifest document stanza matching corpus/manifest.yaml's format.

    The ``license_note`` is a placeholder and the leading comment reminds the
    contributor to record their robots.txt / Content-Signal reading in the
    manifest header (as the existing agencies do), not just in their head.
    """
    doc_id = f"{agency_id.lower()}-fares"
    return "\n".join(
        [
            f"  # ── {agency_full} "
            + "─" * max(0, 60 - len(agency_full))
            + "─",
            "  # TODO: before merging, record this agency's robots.txt and any",
            "  #       Content-Signal / permissions reading in the manifest header",
            "  #       comment block above, dated, as the other agencies do.",
            f"  - id: {doc_id}",
            f"    agency: {agency_id.upper()}",
            f"    agency_full: {agency_full}",
            f'    title: "{title}"',
            f"    url: {url}",
            f"    language: {language}",
            '    license_note: "TODO: public agency fare information; note any Content-Signal."',
        ]
    )


def append_manifest_stanza(stanza: str, manifest_path: Path) -> None:
    """Append the stanza, commented out, to the end of the manifest.

    It is written commented so the corpus a committed eval run saw never changes
    silently: the contributor uncomments it, then runs fetch/ingest deliberately.
    """
    commented = "\n".join(
        f"# {line}" if line.strip() else "#" for line in stanza.splitlines()
    )
    block = (
        "\n"
        "# ── scaffolded by assistant.scaffold_agency ── "
        "uncomment, verify, then fetch/ingest:\n" + commented + "\n"
    )
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(block)


# ── draft eval-case skeletons ────────────────────────────────────────────────


def _quote_yaml(value: str) -> str:
    """Double-quote a scalar for YAML, escaping backslashes and quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_draft_suite(agency_id: str, chunks: list[dict]) -> str:
    """Render a draft suite YAML string: one skeleton case per chunk.

    Each case carries ``draft: true`` (the runner refuses to run it), a question
    stub, an empty ``required_facts`` for a human to fill, and the chunk's source
    passage inline as the ``rationale`` so the case can be written without
    hunting for the passage.
    """
    lid = agency_id.lower()
    # Prefer the agency's real casing as ingested (e.g. "SacRT"); fall back to
    # the id upper-cased when a chunk omits it.
    scope = next(
        (c.get("agency") for c in chunks if c.get("agency")), agency_id.upper()
    )
    lines = [
        f"# DRAFT suite for {scope} — auto-generated skeletons, one per ingested chunk.",
        "#",
        "# Every case carries `draft: true`; evals.runner.validate_cases REFUSES to run",
        "# any suite while a single draft flag remains, so nothing here can land in eval",
        "# results by accident. For each case: write a real rider `question` answerable",
        "# from the passage quoted in the rationale, fill `required_facts` with the",
        "# literal facts (or `re:` regexes) that prove groundedness, then mirror the case",
        "# into the real suites (groundedness / refusal / multilingual / ...) and delete",
        f"# this file. See docs/agencies/{lid}-checklist.md.",
        f"suite: draft_{lid}",
        "cases:",
    ]
    for i, chunk in enumerate(chunks, start=1):
        language = chunk.get("language", "en")
        rationale = str(chunk.get("text", "")).rstrip("\n")
        lines.append(f"  - id: {lid}-draft-{i:03d}")
        lines.append("    draft: true")
        lines.append(
            "    question: "
            + _quote_yaml("TODO: write a rider question answerable from this passage")
        )
        lines.append(f"    agency_scope: {scope}")
        lines.append(f"    language: {language}")
        lines.append("    expected_behavior: answer")
        lines.append("    required_facts: []")
        # Literal block scalar keeps the passage verbatim regardless of its
        # punctuation, quotes, or line breaks.
        lines.append("    rationale: |")
        for text_line in rationale.splitlines() or [""]:
            lines.append("      " + text_line)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_draft_suite(agency_id: str, chunks: list[dict], suites_dir: Path) -> Path:
    """Write the draft suite to <suites_dir>/draft_<id>.yaml and return the path."""
    suites_dir.mkdir(parents=True, exist_ok=True)
    path = suites_dir / f"draft_{agency_id.lower()}.yaml"
    path.write_text(render_draft_suite(agency_id, chunks), encoding="utf-8")
    return path


# ── parity checklist ─────────────────────────────────────────────────────────


def render_checklist(agency_id: str, agency_full: str) -> str:
    """Render the parity checklist markdown for the new agency."""
    scope = agency_id.upper()
    lid = agency_id.lower()
    return f"""# Adding {agency_full} ({scope}) — parity checklist

Auto-generated by `python -m assistant.scaffold_agency {lid}`. The bar is the
same eval coverage the existing agencies have. Work top to bottom; the PR is
ready when every box is checked and `make verify` is green.

## Corpus

- [ ] robots.txt and any Content-Signal / permissions reading for this agency's
      host recorded, dated, in the `corpus/manifest.yaml` header comment block
      (not just in your head).
- [ ] Manifest stanza uncommented and filled: real `url`, `agency_full`, and a
      `license_note` that states the actual license / Content-Signal.
- [ ] A Spanish (`language: es`) fares page added if the agency publishes one;
      if it does not, say so in the PR so the multilingual gap is on purpose.
- [ ] `make fetch && make ingest` run; snapshots committed under `corpus/raw/`.

## Eval cases

- [ ] `evals/suites/draft_{lid}.yaml` reviewed: each skeleton given a real rider
      `question` and `required_facts` filled from the quoted passage.
- [ ] Edge-case boundaries this agency actually publishes found and cased (age
      cutoffs, income limits, document alternatives, what stacks with what).
- [ ] Cases mirrored into the real suites: `groundedness`, `refusal`,
      `cross_agency`, `multilingual`, `freshness` — matching the coverage the
      other agencies get.
- [ ] Every `draft: true` flag removed and the `draft_{lid}.yaml` file deleted
      once its cases have moved into the real suites.

## Gate

- [ ] `make verify` green (lint + typecheck + coverage-gated tests + i18n).
- [ ] `uv run python -m evals.runner --offline` runs the new cases with the
      deterministic checks passing.
- [ ] New rider-facing behavior validated with a live `make eval` if it touched
      prompts / retrieval / answer (see CONTRIBUTING.md).
"""


def write_checklist(agency_id: str, agency_full: str, docs_dir: Path) -> Path:
    """Write the checklist to <docs_dir>/<id>-checklist.md and return the path."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"{agency_id.lower()}-checklist.md"
    path.write_text(render_checklist(agency_id, agency_full), encoding="utf-8")
    return path


# ── entry point ──────────────────────────────────────────────────────────────


def _docs_agencies_dir() -> Path:
    return config.REPO_ROOT / "docs" / "agencies"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m assistant.scaffold_agency",
        description="Scaffold the manifest stanza, draft eval skeletons, and "
        "parity checklist for adding a new agency.",
    )
    parser.add_argument("agency_id", help="short agency id, e.g. hta")
    parser.add_argument(
        "--agency-full",
        default=None,
        help="full agency name (default: a TODO placeholder)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="fares page URL (default: a TODO placeholder)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="also append the stanza, commented out, to corpus/manifest.yaml",
    )
    args = parser.parse_args(argv)

    agency_full = args.agency_full or f"TODO: full name of {args.agency_id.upper()}"
    url = args.url or "https://TODO.example/fares/"

    stanza = build_manifest_stanza(args.agency_id, agency_full, url)
    print(stanza)

    if args.write:
        append_manifest_stanza(stanza, config.MANIFEST_PATH)
        print(f"\n# appended commented stanza to {config.MANIFEST_PATH}", file=sys.stderr)

    chunks = [asdict(c) for c in load_chunks()]
    matching = [c for c in chunks if c["agency"].upper() == args.agency_id.upper()]
    if matching:
        suite_path = write_draft_suite(args.agency_id, matching, config.EVAL_SUITES_DIR)
        print(
            f"# wrote {len(matching)} draft case(s) to {suite_path} "
            "(all `draft: true` — the runner will refuse them until you fill and unflag)",
            file=sys.stderr,
        )
    else:
        print(
            f"# no ingested chunks for {args.agency_id.upper()} yet; run "
            "`make fetch && make ingest` then re-run to draft cases",
            file=sys.stderr,
        )

    checklist_path = write_checklist(args.agency_id, agency_full, _docs_agencies_dir())
    print(f"# wrote parity checklist to {checklist_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
