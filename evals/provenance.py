"""Provenance gate: published artifacts must match HEAD's code.

The repo's thesis is "here is how I test it, honestly." That only holds if the
three published artifacts describe the *current* system:

* `EVALS.md`             — the headline eval report;
* `evals/baseline.json`  — the regression-gate reference;
* `evals/govchat/golden.jsonl` — the independent-audit dataset.

Each of these is generated against a specific set of prompt versions and a
specific corpus. When a prompt is bumped (v6→v7) or the corpus changes, an
artifact that still records the old versions silently describes a different
system than the one at HEAD. For a journalist or procurement reviewer a stale
report that *looks* current is worse than no report.

This module extracts the versions each artifact declares and compares them to
HEAD (`config.prompt_version` for the four prompts, `corpus.corpus_version` for
the corpus). A mismatch fails the gate unless it is explicitly listed in
`evals/stale_acknowledged.json` — the loud, documented escape that lets an
author be honest about a known-stale artifact (typically one whose refresh is
credential-gated) without lying about it.

    python -m evals.provenance          # check; exit 1 on unacknowledged drift

The artifacts declare their provenance in machine-readable form:

* `EVALS.md`     — an HTML-comment block:  <!-- provenance {json} -->
* `baseline.json`— a top-level "provenance" object
* `golden.jsonl` — a "# provenance: {json}" header line (a comment the external
  govchat-eval reader skips)

emitted by `evals/report.py`, `evals/runner.py:update_baseline`, and
`evals/govchat_export.py` respectively.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from assistant import config, corpus

# The four versioned prompts a full eval run pins.
ALL_PROMPTS = ("system", "answer_user", "judge_groundedness", "judge_helpfulness")
# The golden dataset records answers only, so its provenance covers the two
# answer-side prompts; the judge prompts belong to govchat-eval's own judge.
ANSWER_PROMPTS = ("system", "answer_user")

EVALS_MD_PATH = config.REPO_ROOT / "EVALS.md"
BASELINE_PATH = config.REPO_ROOT / "evals" / "baseline.json"
GOLDEN_PATH = config.REPO_ROOT / "evals" / "govchat" / "golden.jsonl"
ACK_PATH = config.REPO_ROOT / "evals" / "stale_acknowledged.json"

_EVALS_BLOCK_RE = re.compile(r"<!--\s*provenance\s*(\{.*?\})\s*-->", re.S)
_GOLDEN_LINE_RE = re.compile(r"^#\s*provenance:\s*(\{.*\})\s*$")


@dataclass(frozen=True)
class Mismatch:
    artifact: str
    field: str
    declared: str | None
    expected: str


def head_prompt_versions(names: tuple[str, ...] = ALL_PROMPTS) -> dict[str, str]:
    return {name: config.prompt_version(name) for name in names}


def head_corpus_version() -> str:
    return corpus.corpus_version()


def provenance_block(run_id: str, prompt_names: tuple[str, ...] = ALL_PROMPTS) -> dict:
    """The machine-readable provenance payload for an artifact generated now."""
    return {
        "run_id": run_id,
        "corpus_version": head_corpus_version(),
        "prompt_versions": head_prompt_versions(prompt_names),
    }


def render_evals_md_block(payload: dict) -> str:
    """The HTML-comment block report.py appends to EVALS.md."""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return f"<!-- provenance {body} -->"


# ── readers ──────────────────────────────────────────────────────────────────


def read_evals_md(text: str) -> dict | None:
    m = _EVALS_BLOCK_RE.search(text)
    return json.loads(m.group(1)) if m else None


def read_baseline(data: dict) -> dict | None:
    return data.get("provenance")


def read_golden(text: str) -> dict | None:
    for line in text.splitlines():
        m = _GOLDEN_LINE_RE.match(line.strip())
        if m:
            return json.loads(m.group(1))
    return None


# ── the check ────────────────────────────────────────────────────────────────


def _compare(
    artifact: str,
    declared: dict | None,
    expected_prompts: dict[str, str],
    expected_corpus: str,
) -> list[Mismatch]:
    if declared is None:
        return [Mismatch(artifact, "provenance", None, "a declared provenance block")]
    out: list[Mismatch] = []
    if declared.get("corpus_version") != expected_corpus:
        out.append(
            Mismatch(artifact, "corpus_version", declared.get("corpus_version"), expected_corpus)
        )
    declared_prompts = declared.get("prompt_versions", {}) or {}
    for name, want in expected_prompts.items():
        got = declared_prompts.get(name)
        if got != want:
            out.append(Mismatch(artifact, f"prompt_versions.{name}", got, want))
    return out


def load_acknowledgements(path: Path | None = None) -> set[tuple[str, str]]:
    """(artifact, field) pairs whose staleness is explicitly, loudly accepted.

    Each entry must carry a non-empty `reason`; an acknowledgement without a
    reason is rejected so the escape can never be a quiet one.
    """
    path = path or ACK_PATH
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    acked: set[tuple[str, str]] = set()
    for entry in data.get("acknowledged", []):
        if not entry.get("reason", "").strip():
            raise SystemExit(
                f"stale acknowledgement for {entry.get('artifact')}/{entry.get('field')} "
                "has no reason; acknowledgements must be documented"
            )
        acked.add((entry["artifact"], entry["field"]))
    return acked


def check_all(
    *,
    acknowledged: set[tuple[str, str]] | None = None,
    evals_md: str | None = None,
    baseline: dict | None = None,
    golden: str | None = None,
) -> dict:
    """Compare all three artifacts to HEAD.

    Returns {"failures": [...], "acknowledged": [...]}; the gate is green iff
    `failures` is empty. Acknowledged mismatches are downgraded to warnings.
    Inputs default to the committed files but can be injected for tests.
    """
    acknowledged = load_acknowledgements() if acknowledged is None else acknowledged
    evals_md = EVALS_MD_PATH.read_text(encoding="utf-8") if evals_md is None else evals_md
    if baseline is None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    golden = GOLDEN_PATH.read_text(encoding="utf-8") if golden is None else golden

    all_prompts = head_prompt_versions(ALL_PROMPTS)
    answer_prompts = {k: all_prompts[k] for k in ANSWER_PROMPTS}
    cv = head_corpus_version()

    mismatches = (
        _compare("EVALS.md", read_evals_md(evals_md), all_prompts, cv)
        + _compare("baseline.json", read_baseline(baseline), all_prompts, cv)
        + _compare("golden.jsonl", read_golden(golden), answer_prompts, cv)
    )
    failures = [m for m in mismatches if (m.artifact, m.field) not in acknowledged]
    warnings = [m for m in mismatches if (m.artifact, m.field) in acknowledged]
    return {"failures": failures, "acknowledged": warnings}


def _fmt(m: Mismatch) -> str:
    return f"{m.artifact}:{m.field}: declared {m.declared!r} but HEAD is {m.expected!r}"


def main() -> int:
    result = check_all()
    for m in result["acknowledged"]:
        print(f"ACKNOWLEDGED (stale, accepted): {_fmt(m)}", file=sys.stderr)
    if result["failures"]:
        print("PROVENANCE DRIFT — published artifacts disagree with HEAD:", file=sys.stderr)
        for m in result["failures"]:
            print(f"  {_fmt(m)}", file=sys.stderr)
        print(
            "\nRegenerate the affected artifact (make eval / --update-baseline / make audit), "
            "or record a documented waiver in evals/stale_acknowledged.json.",
            file=sys.stderr,
        )
        return 1
    print(
        "provenance: EVALS.md, baseline.json, and golden.jsonl match HEAD "
        f"(corpus {head_corpus_version()})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
