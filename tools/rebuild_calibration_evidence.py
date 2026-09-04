"""Rebuild a calibration worksheet's evidence packet from committed history.

`evals/calibration.py --pack` writes a packet from a run directory. This script
exists for the case where the run directory is already gone, which is not a
hypothetical: on 2026-09-04 `make relabel` on
`evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl` exited 2 with "no
results.jsonl in evals/runs/20260712T050117Z". `evals/runs/` is gitignored, the
promoted 2026-07-12 run had been pruned from the one machine that held it, and
so the 37 rows that worksheet exists to have labeled could not be labeled by
anyone, on any checkout. That is the reason the sheet sat at 0 of 37, and it is
not a reason that would have shown up as reluctance.

The run's evidence did survive, spread across four committed artifacts, and this
reassembles it. Nothing here is generated, inferred, or paraphrased: every field
is copied from a file in git, and every answer is checked against the
`answer_sha256` the worksheet already declared before it is written out. A row
whose answer does not hash to its binding is not emitted.

    Answers       evals/govchat/golden.jsonl (the same run's recorded answers),
                  falling back to the blockquoted answer in
                  evals/calibration/judge_label_packet_2026-07-11.md for the
                  three multi-turn cases the GovChat export does not carry.
    Passages      the chunk ids and sections listed per case in that same
                  packet .md, joined to corpus/processed/chunks.jsonl at the
                  promoting commit, which supplies agency, doc title, source URL
                  and fetch date. The section string is required to match, so a
                  mis-joined chunk fails the build rather than being rendered.
    Case metadata evals/suites/*.yaml at the promoting commit: question, prior
                  turns, expected behavior, rationale.
    Refusal       golden.jsonl's `target_response.refused`.
    Criteria      prompts/judge_groundedness.txt and prompts/judge_helpfulness.txt
                  at the promoting commit — v2 and v3 of 2026-07-02, the exact
                  versions EVALS.md records for this run. HEAD's groundedness
                  prompt is already v3 and another change to it is in flight;
                  asking a reviewer to apply a criterion the recorded verdict
                  never faced would measure the prompt edit, not the judge.

Two things the packet cannot restore, and says so rather than filling in:

* **Retrieval scores.** Never committed for that run. Each passage carries
  `"score": null`, which `_passages_block` renders as "score not recorded" — not
  as a score of zero.
* **The judge's reasoning.** It lived in the pruned `results.jsonl`. The
  worksheet still records each row's `judge_said`, so `--review` can reveal the
  verdict after the reviewer answers, and states that the reasoning is gone.

One asymmetry is deliberate and is disclosed in the packet header: the passages
carry their source URL and fetch date, and the groundedness judge of that era
did not see them (`evals/judges.py::_passages_block` gained the provenance line
on 2026-08-16, after this run). Reproducing the blind spot would have been the
wrong call. It is what made the judge fail fresh-001 for a dated claim it had no
way to check, and a human reproducing a judge's blind spot calibrates nothing.

Usage (offline, no model calls):

    uv run python tools/rebuild_calibration_evidence.py \\
        --worksheet evals/calibration/judge_relabel_worksheet_2026-08-05.jsonl \\
        --commit 331441c > evals/calibration/judge_relabel_evidence_2026-08-05.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The commit that promoted the 2026-07-12 run (`fix(evals): remediate eval
#: quality gaps to 192/201, advance baseline`). Corpus and suites are read at
#: this commit, not at HEAD: the corpus has since grown from 11 documents to 52
#: and the suites have moved, so HEAD's fetch dates would be stamped onto
#: passages a July answer was written against.
DEFAULT_COMMIT = "331441c"

GOLDEN = "evals/govchat/golden.jsonl"
PACKET_MD = "evals/calibration/judge_label_packet_2026-07-11.md"
CHUNKS = "corpus/processed/chunks.jsonl"
SUITES = (
    "conversation",
    "cross_agency",
    "edge_cases",
    "freshness",
    "groundedness",
    "multilingual",
    "refusal",
    "sensitivity",
    "stretch_tagalog",
)

_CASE_SPLIT_RE = re.compile(r"\n### \d+\. `")
_ANSWER_RE = re.compile(r"\*\*Assistant answer:\*\*\n\n((?:>.*\n)+)")
_PASSAGE_RE = re.compile(r"^- \*\*\[([^\]]+)\]\*\* _([^_]*)_", re.M)


def _git_show(commit: str, path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(text: str) -> list[dict]:
    return [
        json.loads(line)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _unquote(block: str) -> str:
    """Undo a markdown blockquote. Exact by construction, and checked: the
    result has to hash to the worksheet's `answer_sha256` or it is discarded."""
    lines = []
    for line in block.rstrip("\n").split("\n"):
        if line.startswith("> "):
            lines.append(line[2:])
        elif line.strip() == ">":
            lines.append("")
        else:
            lines.append(line.lstrip(">"))
    return "\n".join(lines)


def load_suite_cases(commit: str) -> dict[str, dict]:
    """Every case at `commit`, keyed by id. A sensitivity suite is written as
    `pairs:` of `variants:`; the runner flattens it and so does this."""
    cases: dict[str, dict] = {}
    for name in SUITES:
        data = yaml.safe_load(_git_show(commit, f"evals/suites/{name}.yaml"))
        listed = data.get("cases")
        if listed is None:
            listed = [v for pair in data["pairs"] for v in pair["variants"]]
        for case in listed:
            case["suite"] = data["suite"]
            cases[case["id"]] = case
    return cases


def parse_packet_md(text: str) -> dict[str, dict]:
    """Per case: the blockquoted answer and the (chunk_id, section) list."""
    out: dict[str, dict] = {}
    for section in _CASE_SPLIT_RE.split(text)[1:]:
        case_id = section.split("`")[0]
        answer_match = _ANSWER_RE.search(section)
        out[case_id] = {
            "answer": _unquote(answer_match.group(1)) if answer_match else None,
            "passages": _PASSAGE_RE.findall(section),
        }
    return out


def judge_criteria(commit: str, judges: list[str]) -> dict[str, str]:
    """The criterion text each judge was given at `commit`, verbatim."""
    return {j: _git_show(commit, f"prompts/judge_{j}.txt") for j in judges}


def _resolve_answer(binding: str, gold: dict | None, packet_case: dict | None) -> str | None:
    """The committed answer that hashes to `binding`, or None.

    Never a best match: an answer that does not hash to what the worksheet
    declared is the wrong answer, and a verdict recorded against it would be a
    verdict on text the reviewer did not read.
    """
    if gold is not None and _sha256(gold["target_response"]["text"]) == binding:
        return gold["target_response"]["text"]
    candidate = (packet_case or {}).get("answer")
    if candidate is not None and _sha256(candidate) == binding:
        return candidate
    return None


def _resolve_passages(
    case_id: str, listed: list[tuple[str, str]], chunks: dict[str, dict], commit: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Join the packet's chunk ids to the corpus at `commit`.

    The recorded section string has to match the chunk's, which is what turns
    "these ids look right" into a checked join: a chunk id reused for different
    text between commits fails the rebuild instead of being rendered as the
    evidence a July answer was written against.
    """
    passages: list[dict[str, Any]] = []
    problems: list[str] = []
    for chunk_id, section in listed:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            problems.append(f"{case_id}: chunk {chunk_id} is not in the corpus at {commit}")
            continue
        if chunk["section"].strip() != section.strip():
            problems.append(
                f"{case_id}/{chunk_id}: section is {chunk['section']!r} at {commit} but the "
                f"packet recorded {section!r}"
            )
            continue
        passages.append(
            {
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk["doc_id"],
                "agency": chunk["agency"],
                "doc_title": chunk["doc_title"],
                "url": chunk["url"],
                "fetch_date": chunk["fetch_date"],
                "section": chunk["section"],
                "score": None,
                "text": chunk["text"],
            }
        )
    return passages, problems


def build(worksheet: Path, commit: str) -> tuple[list[dict], list[str]]:
    rows = _read_jsonl(worksheet.read_text(encoding="utf-8"))
    cases = load_suite_cases(commit)
    chunks = {c["chunk_id"]: c for c in _read_jsonl(_git_show(commit, CHUNKS))}
    golden = {g["id"]: g for g in _read_jsonl((REPO_ROOT / GOLDEN).read_text(encoding="utf-8"))}
    packet = parse_packet_md((REPO_ROOT / PACKET_MD).read_text(encoding="utf-8"))

    problems: list[str] = []
    out: list[dict] = []
    for case_id in dict.fromkeys(r["case_id"] for r in rows):
        declared = {r["answer_sha256"] for r in rows if r["case_id"] == case_id}
        case = cases.get(case_id)
        if len(declared) != 1:
            problems.append(f"{case_id}: worksheet rows disagree about answer_sha256")
            continue
        if case is None:
            problems.append(f"{case_id}: no such case in the suites at {commit}")
            continue
        binding = declared.pop()
        gold = golden.get(case_id)
        answer = _resolve_answer(binding, gold, packet.get(case_id))
        if answer is None:
            problems.append(f"{case_id}: no committed answer hashes to {binding[:12]}…")
            continue

        listed = packet.get(case_id, {}).get("passages") or []
        passages, passage_problems = _resolve_passages(case_id, listed, chunks, commit)
        problems += passage_problems
        out.append(
            {
                "case_id": case_id,
                "suite": case["suite"],
                "language": case.get("language", "en"),
                "question": case.get("question") or (case.get("turns") or [None])[-1],
                "turns": case.get("turns"),
                "history": case.get("history"),
                "expected_behavior": case["expected_behavior"],
                "rationale": (case.get("rationale") or "").strip(),
                "answer": answer,
                "refused": bool(gold and gold["target_response"].get("refused")),
                "passages": passages,
                "passages_recorded": bool(listed),
            }
        )
    return out, problems


def header(commit: str, rows: list[dict]) -> list[str]:
    with_passages = sum(1 for r in rows if r["passages_recorded"])
    return [
        "# Evidence packet for judge_relabel_worksheet_2026-08-05.jsonl.",
        "#",
        "# Rebuilt by tools/rebuild_calibration_evidence.py because the run this worksheet",
        "# is bound to, evals/runs/20260712T050117Z, no longer exists: evals/runs/ is",
        "# gitignored, so `make relabel` had been exiting 2 rather than showing a row.",
        f"# Every field is copied from a committed file at {commit} (answers from",
        "# evals/govchat/golden.jsonl and evals/calibration/judge_label_packet_2026-07-11.md,",
        "# passages joined to corpus/processed/chunks.jsonl, case metadata from",
        "# evals/suites/*.yaml). Every answer hashes to the answer_sha256 the worksheet",
        "# already declared; --review re-checks that before printing anything.",
        "#",
        "# Two things are absent and are rendered as absent, not as values:",
        "#  - Retrieval scores were never committed for this run. Passages carry a null",
        "#    score, shown as 'score not recorded'.",
        f"#  - {len(rows) - with_passages} of {len(rows)} cases carry no passage list, because",
        "#    the 2026-07-11 packet covered groundedness only. Every one of those cases is",
        "#    reached by a helpfulness row, and the helpfulness judge is not shown passages.",
        "#",
        "# One asymmetry, on purpose: each passage carries its source URL and fetch date.",
        "# The groundedness judge of this era did not see them (evals/judges.py gained the",
        "# provenance line on 2026-08-16). Withholding them from you as well would",
        "# reproduce the blind spot that made the judge wrong about fresh-001, which would",
        "# calibrate nothing. If it changes your verdict, say so in your reason.",
        "#",
        "# The judge criteria are the versions this run used (groundedness v2, helpfulness",
        "# v3, both 2026-07-02), read from prompts/ at the same commit rather than from HEAD.",
        "# HEAD's groundedness prompt is already v3 and PR #179 takes it to v4. Applying a",
        "# later criterion to an answer an earlier judge ruled on would measure the prompt",
        "# edit, not the judge.",
        "#",
        "# This file holds no judge verdict and no judge reasoning.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", required=True, help="the worksheet to build evidence for")
    parser.add_argument("--commit", default=DEFAULT_COMMIT, help="commit to read history at")
    args = parser.parse_args()

    worksheet = Path(args.worksheet)
    rows, problems = build(worksheet, args.commit)
    if problems:
        for problem in problems:
            print(f"cannot rebuild: {problem}", file=sys.stderr)
        return 1
    judges = sorted({r["judge"] for r in _read_jsonl(worksheet.read_text(encoding="utf-8"))})
    for line in header(args.commit, rows):
        print(line)
    print(
        json.dumps(
            {
                "packet": {
                    "judge_criteria": judge_criteria(args.commit, judges),
                    "judge_criteria_source": f"prompts/judge_*.txt at {args.commit}",
                }
            },
            ensure_ascii=False,
        )
    )
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
