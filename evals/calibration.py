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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from assistant import config

LABELS_PATH = config.REPO_ROOT / "evals" / "calibration" / "judge_labels.jsonl"


@dataclass
class Label:
    case_id: str
    judge: str  # "groundedness" | "helpfulness"
    human_passed: bool
    note: str = ""


def load_labels(path: Path | None = None) -> list[Label]:
    path = path or LABELS_PATH
    labels: list[Label] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        labels.append(Label(d["case_id"], d["judge"], bool(d["human_passed"]), d.get("note", "")))
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
    raw agreement, and Cohen's kappa. Labels whose judge verdict is missing or
    errored (None) in the run are skipped and counted under `unmatched`.
    """
    labels = labels if labels is not None else load_labels()
    by_id = {r["case_id"]: r for r in records}
    pairs: list[tuple[bool, bool]] = []
    unmatched: list[str] = []
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
        pairs.append((verdict, lab.human_passed))
    n = len(pairs)
    agreement = sum(1 for a, b in pairs if a == b) / n if n else None
    kappa = _cohen_kappa(pairs)
    return {
        "n_labels": len(labels),
        "n_matched": n,
        "unmatched": unmatched,
        "agreement": round(agreement, 3) if agreement is not None else None,
        "cohen_kappa": round(kappa, 3) if kappa is not None else None,
        # Most sampled answers are correct, so the label set skews to "pass";
        # kappa is deflated by that imbalance and should be read with the n.
        "note": "small, pass-skewed sample; read agreement alongside n and kappa",
    }
