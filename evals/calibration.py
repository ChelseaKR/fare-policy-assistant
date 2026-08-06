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
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from assistant import config

LABELS_PATH = config.REPO_ROOT / "evals" / "calibration" / "judge_labels.jsonl"

# CLAUDE.md: "Judge disagreement spot-checked by hand on a 10% sample."
# Reported as a denominator rather than left as prose, so a shortfall is a
# number on the page instead of an adjective.
SAMPLE_FRACTION = 0.10

# Marker `--emit` writes into every template row. A row still carrying it has
# not been looked at by a person, and must never be scored as a human verdict:
# see `load_labels`.
TEMPLATE_NOTE = "TEMPLATE"


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
        where = f"{path.name}: {d.get('case_id', '?')}/{d.get('judge', '?')}"
        # An unfilled `--emit` template must never be scored. It carries a null
        # verdict and the TEMPLATE marker; scoring one would grade the judge
        # against a verdict nobody made, and — because templates used to be
        # pre-filled with the judge's own call — would report perfect agreement
        # while measuring nothing. Fail loudly instead: a relabeling pass that
        # stalls half-done should stop the calibration, not quietly shrink it.
        if str(d.get("note", "")).startswith(TEMPLATE_NOTE):
            raise ValueError(
                f"{where} still carries the {TEMPLATE_NOTE} marker — a template row is not a "
                "human label. Fill in human_passed from an independent review of the answer "
                "and replace the note, or delete the row."
            )
        if not isinstance(d.get("human_passed"), bool):
            raise ValueError(
                f"{where}: human_passed must be true or false, not "
                f"{d.get('human_passed')!r} — an unreviewed pair is not a verdict"
            )
        labels.append(
            Label(
                d["case_id"],
                d["judge"],
                d["human_passed"],
                d.get("answer_sha256", ""),
                d.get("note", ""),
            )
        )
    return labels


def _cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two binary raters over (judge, human) verdicts.

    None when kappa is undefined, which is two distinct situations: an empty
    sample, and a sample where both raters gave the same verdict every time.
    In the second, expected agreement is 1.0 and the formula is 0/0.

    Returning 1.0 there — the previous behavior, and a common convention — is
    what published κ = 1.000 in `EVALS.md` on 2026-07-12 off a sample of four
    all-pass labels. Nothing in that number was measured: the two labels that
    recorded a human/judge *disagreement* (ml-004, ground-024) had gone stale
    when a prompt bump changed their answers, so the surviving sample was the
    agreeing half of the set and κ was 1.0 by definition. A κ that cannot come
    out any other way is not evidence about the judge, and a reader has no way
    to tell it apart from a real 1.0. Undefined is reported as undefined.
    """
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pj_yes = sum(1 for a, _ in pairs if a) / n
    ph_yes = sum(1 for _, b in pairs if b) / n
    pe = pj_yes * ph_yes + (1 - pj_yes) * (1 - ph_yes)
    if pe == 1.0:
        return None
    return (po - pe) / (1 - pe)


def judged_pair_count(records: list[dict]) -> int:
    """(case, judge) pairs the judge actually scored in a run — the population
    the label sample is drawn from, and so the denominator the floor is a
    fraction of. Judge errors (`passed is None`) are excluded: an errored
    verdict is not something a human can be compared against."""
    return sum(1 for r in records for v in r.get("judges", []) if v.get("passed") is not None)


def sample_floor(n_judged: int, fraction: float = SAMPLE_FRACTION) -> int:
    """The label count `CLAUDE.md`'s "10% sample" asks for, rounded up."""
    return math.ceil(n_judged * fraction)


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
    n_judged = judged_pair_count(records)
    floor = sample_floor(n_judged)
    # A sample in which one rater never varies cannot produce a kappa, and a
    # sample in which neither rater ever disagrees with the other cannot
    # produce evidence about the judge at all. Both are reported, because a
    # reader cannot tell either from the agreement percentage alone.
    disagreements = sum(1 for a, b in pairs if a != b)
    return {
        "n_labels": len(labels),
        "n_matched": n,
        "unmatched": unmatched,
        "n_stale": len(stale),
        "stale": stale,
        "n_unbound": len(unbound),
        "unbound": unbound,
        "n_judged": n_judged,
        "floor": floor,
        "meets_floor": n >= floor,
        "n_disagreements": disagreements,
        "kappa_defined": kappa is not None,
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

    `human_passed` is emitted as null, not as the judge's own verdict. It used
    to be the latter, "as a placeholder" — which meant a relabeling pass that
    accepted the defaults would grade the judge against itself and report
    perfect agreement. The whole point of this file is to be independent of the
    judge, so the template cannot start out agreeing with it, and `load_labels`
    refuses to score a row that still reads null.
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
                    "human_passed": None,
                    "answer_sha256": answer_hash(r["answer"]),
                    "note": f"{TEMPLATE_NOTE} — read the answer and its passages, then set "
                    "human_passed and replace this note",
                }
            )
    return rows


def stratified_worksheet(run_dir: Path, size: int | None = None) -> list[dict]:
    """A floor-sized relabeling worksheet, stratified to include the failures.

    `--emit` returns every judged pair in the run, which for a full run is 367
    rows — too many to hand-label, so in practice whoever labels picks a subset
    by eye. That is how the committed sample ended up with 14 of its 16 labels
    on pairs the judge had passed: a reviewer reading down a list agrees, agrees,
    agrees. A sample drawn from the region where the judge never objects cannot
    disagree with it, and the 2026-07-12 report published exactly that — 100%
    agreement over four all-pass labels, with κ undefined.

    So this takes every pair the judge *failed* first (nine in the promoted
    run: the region where a human is most likely to differ, and where differing
    matters most), then fills to `size` from the passed pairs, round-robin
    across suites so one large suite cannot dominate. Selection is deterministic
    given the run, so the worksheet is reproducible and diffable.

    It emits templates, not verdicts. A human still reads every answer.
    """
    records = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    size = size or sample_floor(judged_pair_count(records))
    rows = {(r["case_id"], v["name"]): (r, v) for r in records for v in r.get("judges", [])}
    scored = {k: rv for k, rv in rows.items() if rv[1].get("passed") is not None}
    failed = sorted(k for k, (_, v) in scored.items() if v["passed"] is False)
    chosen = list(failed)

    by_suite: dict[str, list[tuple[str, str]]] = {}
    for key in sorted(scored):
        if key in set(failed):
            continue
        by_suite.setdefault(scored[key][0]["suite"], []).append(key)
    # Round-robin across suites: take the first unused pair from each suite in
    # turn until the worksheet is full or every suite is exhausted.
    depth = 0
    while len(chosen) < size and any(len(v) > depth for v in by_suite.values()):
        for suite in sorted(by_suite):
            if len(chosen) >= size:
                break
            if len(by_suite[suite]) > depth:
                chosen.append(by_suite[suite][depth])
        depth += 1

    return [
        {
            "case_id": case_id,
            "judge": judge,
            "human_passed": None,
            "answer_sha256": answer_hash(scored[(case_id, judge)][0]["answer"]),
            "judge_said": scored[(case_id, judge)][1]["passed"],
            "suite": scored[(case_id, judge)][0]["suite"],
            "note": f"{TEMPLATE_NOTE} — read the answer and its passages, then set "
            "human_passed and replace this note",
        }
        for case_id, judge in chosen
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit",
        metavar="RUN_DIR",
        help="emit label-row templates (with answer_sha256) from a run directory",
    )
    parser.add_argument(
        "--worksheet",
        metavar="RUN_DIR",
        help="emit a floor-sized relabeling worksheet, failures first, spread across suites",
    )
    args = parser.parse_args()
    if args.emit:
        for row in emit_label_templates(Path(args.emit)):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.worksheet:
        for row in stratified_worksheet(Path(args.worksheet)):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
