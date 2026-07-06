"""Judge calibration against human labels.

The model card promises that judge verdicts are spot-checked against a
hand-labeled sample and that the agreement is recorded in the report. This
module is that check. `evals/calibration/judge_labels.jsonl` holds independent
human verdicts on a sample of (case, judge) pairs; `calibrate` compares them to
the LLM judge's verdicts from a run and reports agreement and Cohen's kappa.

The labels are authored by review of each case's answer against its retrieved
passages and expected behavior; they are the human ground truth the automated
judge is audited against, not derived from the judge. Refresh them when the
judge prompt or the sampled answers change.

Each label carries an `answer_sha256` that binds it to the exact answer it was
written against. When a prompt bump changes an answer, `calibrate` treats the
label as *stale* — skipped and counted — rather than silently scoring the judge
against a human verdict on a different answer. Without this, a v6→v7 prompt
change could inflate or deflate the reported kappa on the strength of labels
that no longer describe what the judge saw.

    python -m evals.calibration --emit <run_dir>   # emit label templates from a run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from assistant import config

LABELS_PATH = config.REPO_ROOT / "evals" / "calibration" / "judge_labels.jsonl"


def answer_hash(answer: str) -> str:
    """Stable content hash of a graded answer; the binding key for a label."""
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


@dataclass
class Label:
    case_id: str
    judge: str  # "groundedness" | "helpfulness"
    human_passed: bool
    # sha256 of the answer this verdict was authored against. Empty for a legacy
    # label written before binding existed; such a label cannot be checked for
    # staleness and is reported separately (`unbound`) rather than trusted blindly.
    answer_sha256: str = ""
    note: str = ""


def load_labels(path: Path | None = None) -> list[Label]:
    path = path or LABELS_PATH
    labels: list[Label] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        labels.append(
            Label(
                d["case_id"],
                d["judge"],
                bool(d["human_passed"]),
                d.get("answer_sha256", ""),
                d.get("note", ""),
            )
        )
    return labels


def _cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two binary raters over (judge, human) verdicts."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pj_yes = sum(1 for a, _ in pairs if a) / n
    ph_yes = sum(1 for _, b in pairs if b) / n
    pe = pj_yes * ph_yes + (1 - pj_yes) * (1 - ph_yes)
    if pe == 1.0:  # both raters constant and identical → perfect by definition
        return 1.0
    return (po - pe) / (1 - pe)


def calibrate(records: list[dict], labels: list[Label] | None = None) -> dict:
    """Compare human labels to the run's judge verdicts.

    Returns coverage (how many labels matched a verdict present in the run),
    raw agreement, and Cohen's kappa. A label is only scored when the answer it
    was written against still matches the run's answer for that case:

    * `unmatched` — the judge verdict is missing or errored (None) in the run.
    * `stale`     — the case ran, but its answer changed since the label was
      written (the bound `answer_sha256` no longer matches). Scoring here would
      grade the judge against a human verdict on a different answer, so the
      label is skipped and reported. Relabel with `--emit`.
    * `unbound`   — a legacy label with no `answer_sha256`; it cannot be checked
      for staleness. Still scored, but surfaced so the gap is visible.
    """
    labels = labels if labels is not None else load_labels()
    by_id = {r["case_id"]: r for r in records}
    pairs: list[tuple[bool, bool]] = []
    unmatched: list[str] = []
    stale: list[str] = []
    unbound: list[str] = []
    for lab in labels:
        rec = by_id.get(lab.case_id)
        verdict = None
        if rec:
            for v in rec["judges"]:
                if v["name"] == lab.judge and v["passed"] is not None:
                    verdict = bool(v["passed"])
                    break
        if verdict is None:
            unmatched.append(f"{lab.case_id}/{lab.judge}")
            continue
        # Staleness check: only meaningful when the run record carries the graded
        # answer (every real run does) and the label is bound to a hash.
        if lab.answer_sha256 and "answer" in rec:
            if answer_hash(rec["answer"]) != lab.answer_sha256:
                stale.append(f"{lab.case_id}/{lab.judge}")
                continue
        elif not lab.answer_sha256:
            unbound.append(f"{lab.case_id}/{lab.judge}")
        pairs.append((verdict, lab.human_passed))
    n = len(pairs)
    agreement = sum(1 for a, b in pairs if a == b) / n if n else None
    kappa = _cohen_kappa(pairs)
    return {
        "n_labels": len(labels),
        "n_matched": n,
        "unmatched": unmatched,
        "n_stale": len(stale),
        "stale": stale,
        "n_unbound": len(unbound),
        "unbound": unbound,
        "agreement": round(agreement, 3) if agreement is not None else None,
        "cohen_kappa": round(kappa, 3) if kappa is not None else None,
        # Most sampled answers are correct, so the label set skews to "pass";
        # kappa is deflated by that imbalance and should be read with the n.
        "note": "small, pass-skewed sample; read agreement alongside n and kappa",
    }


def emit_label_templates(run_dir: Path) -> list[dict]:
    """Emit label-row templates for every judged (case, judge) pair in a run.

    Each template carries the `answer_sha256` of the recorded answer so
    relabeling after a prompt bump is a mechanical pass (fill in `human_passed`
    and `note`) rather than archaeology against an old run. Only pairs the judge
    actually scored (passed is not None) are emitted.
    """
    records = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rows: list[dict] = []
    for r in records:
        for v in r.get("judges", []):
            if v.get("passed") is None:
                continue
            rows.append(
                {
                    "case_id": r["case_id"],
                    "judge": v["name"],
                    "human_passed": bool(v["passed"]),  # placeholder: the judge's own call
                    "answer_sha256": answer_hash(r["answer"]),
                    "note": "TEMPLATE — replace human_passed with an independent human verdict",
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit",
        metavar="RUN_DIR",
        help="emit label-row templates (with answer_sha256) from a run directory",
    )
    args = parser.parse_args()
    if args.emit:
        for row in emit_label_templates(Path(args.emit)):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
