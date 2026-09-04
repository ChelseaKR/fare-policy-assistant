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

A verdict is a judgment about an answer *under a criterion*, so a label carries
a `judge_prompt_sha256` as well. That half was missing until PR #179, which
moves `prompts/judge_groundedness.txt` from v3 to v4 and changes which "as of"
claims count as supported: not one committed label goes stale under it, because
the answers do not move, so all sixteen would have gone on being scored against
verdicts from a rubric their author never read. Criterion drift is now caught
and reported the same way answer drift is — skipped, listed, and relabelable.
A silently wrong agreement number is worse than a missing one.

    python -m evals.calibration --emit <run_dir>      # label templates from a run
    python -m evals.calibration --worksheet <run_dir> # floor-sized, failures first
    python -m evals.calibration --pack <run_dir> --for <worksheet>  # commit its evidence
    python -m evals.calibration --review <worksheet>  # label it, one row at a time

## `--review`: the labeling surface

`--emit` and `--worksheet` produce rows; neither shows the evidence a verdict
needs. Labeling a worksheet meant opening `results.jsonl` and hand-digging the
answer, its retrieved passages, and the case's expected behavior out of it,
once per row, 37 times. `--review` puts the evidence on the screen and writes
the verdict back. It was built on the theory that the hand-digging was why the
committed sample sat at four labels against a floor of 37; the section below
records what the actual obstacle turned out to be.

Three properties of it are load-bearing, and each one is a rule about what the
tool must *not* do:

* **It never proposes a verdict.** No default, no suggestion, no inference from
  the judge. An empty answer at the prompt re-asks. This is the same hole that
  was closed on `--emit`, which used to pre-fill `human_passed` with the judge's
  own call "as a placeholder": accepting the defaults would have graded the
  judge against itself and reported perfect agreement while measuring nothing.
  A new surface is exactly where that would come back.
* **The judge's verdict is hidden until after the human answers.** Both orders
  were available. Showing it first makes the pass rows quick to confirm, which
  is precisely how a sample stops being able to disagree — and this worksheet
  exists because the last one could not. So the reviewer sees the case, the
  passages, and the answer; states a verdict; and only then sees what the judge
  said and why. The cost is that confirming a row the judge got right takes as
  long as one it got wrong, which is the intended trade.
* **A verdict is refused when the answer moved.** Every row is bound to an
  `answer_sha256`. If the recorded answer no longer hashes to it, the row is
  reported and skipped rather than labeled: a verdict pinned to an answer the
  reviewer did not read is worse than a blank.

Each completed row is written back immediately, through a temporary file and an
atomic replace, so an interrupted session keeps every verdict already given and
a partly-labeled worksheet can simply be reopened.

## Evidence packets: why a worksheet has to carry its own evidence

`--review` originally read its evidence out of the run directory named in the
worksheet's header. `evals/runs/` is gitignored. That is the whole reason the
2026-08-05 worksheet sat at 0 of 37 rows for a month: on 2026-09-04 `make
relabel` exited 2 with "no results.jsonl in evals/runs/20260712T050117Z",
because that run had been pruned from the only machine that ever held it and
was never in the repository at all. Nobody declined to do the labeling; the
labeling could not be started, and it could never have been started by anyone
who cloned this project.

So a worksheet now ships with an **evidence packet** beside it: a committed
JSONL file, one row per case, carrying exactly the fields `review_block` reads
— question, prior turns, expected behavior, rationale, retrieved passages with
their provenance, and the answer. `--pack` writes one from a run while the run
still exists; `--review` falls back to it when the run directory is gone. The
packet deliberately carries **no judge verdict and no judge reasoning**, so
moving the evidence into the repository cannot leak the one thing the reviewer
must not see first (`build_evidence_packet` strips them, and a test asserts it).

The `answer_sha256` binding is what makes this safe: a packet cannot smuggle in
the wrong answer, because `binding_problem` still refuses any row whose recorded
answer does not hash to what the worksheet declared. The packet is a place to
keep evidence, not a second source of truth about it.

What the reviewer is shown mirrors what each judge was shown, because
calibration asks whether a human would reach the same verdict *on the same
evidence*. The groundedness judge receives the retrieved passages; the
helpfulness judge does not — it grades against the case's expected behavior and
rationale. Passages are still printed on a helpfulness row (a reviewer who
cannot see the corpus cannot tell a policy claim from an invention) but are
labeled as context the judge did not receive, so the asymmetry is visible
instead of silent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from assistant import config

LABELS_PATH = config.REPO_ROOT / "evals" / "calibration" / "judge_labels.jsonl"

# CLAUDE.md: "Judge disagreement spot-checked by hand on a 10% sample."
# Reported as a denominator rather than left as prose, so a shortfall is a
# number on the page instead of an adjective.
SAMPLE_FRACTION = 0.10

# `STANDARDS/AI-EVALUATION-STANDARD.md` §3, the three auto-gates this module's
# numbers are read against. They are recorded here so the report can say which
# floor a run misses and by how much, instead of calling a sample "provisional".
#
# AIEV-18's own denominator is stricter than this repo's: the standard sizes a
# calibration set at 50–100 labeled traces, while `SAMPLE_FRACTION` derives a
# floor from CLAUDE.md's 10% rule, which on the promoted run is 37. Both are
# reported. The smaller one is not treated as sufficient.
AGREEMENT_FLOOR = 0.80  # AIEV-18
KAPPA_FLOOR = 0.60  # AIEV-19
FRESHNESS_DAYS = 30  # AIEV-20
STANDARD_MIN_LABELS = 50  # AIEV-18's stated set size, low end

#: Optional header directive in a labels file: `# labeled_on: YYYY-MM-DD`, the
#: date a person authored the verdicts. AIEV-20 is a question about that date
#: and nothing else can answer it — a file mtime is a checkout artifact and the
#: git log is not always available offline. A labels file without the directive
#: reports its age as unknown, never as fresh.
_LABELED_ON_RE = re.compile(r"^#\s*labeled_on:\s*(\d{4}-\d{2}-\d{2})\s*$")

# Marker `--emit` writes into every template row. A row still carrying it has
# not been looked at by a person, and must never be scored as a human verdict:
# see `load_labels`.
TEMPLATE_NOTE = "TEMPLATE"


def answer_hash(answer: str) -> str:
    """Stable content hash of a graded answer; the binding key for a label."""
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def criterion_hash(criterion: str) -> str:
    """Stable content hash of a judge prompt; the second binding key for a label.

    A verdict is a judgment about an answer *under a criterion*. `answer_sha256`
    binds the first half and nothing bound the second, which is a hole with a
    date on it: PR #179 moves `prompts/judge_groundedness.txt` from v3 to v4 and
    changes what "as of" claims count as supported. Not one of the sixteen
    committed labels goes stale under that — the answers do not move — so every
    one of them would keep being scored, against verdicts produced by a rubric
    the human who wrote them never saw. Answer drift is caught and reported;
    criterion drift was silent, and a silently wrong agreement number is worse
    than a missing one.
    """
    return hashlib.sha256(criterion.encode("utf-8")).hexdigest()


def head_criteria(judges: Iterable[str]) -> dict[str, str]:
    """`prompts/judge_<name>.txt` at HEAD, for the judges named. Missing prompt
    files yield no entry rather than an error: calibration must still report on
    a checkout where a prompt has been renamed."""
    out: dict[str, str] = {}
    for judge in dict.fromkeys(judges):
        try:
            out[judge] = config.load_prompt(f"judge_{judge}")
        except OSError:
            continue
    return out


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
    # sha256 of the judge criterion the verdict was given under. Empty for a
    # label written before this binding existed; reported as `criterion_unbound`
    # and still scored, the same treatment `answer_sha256` gives its own legacy.
    judge_prompt_sha256: str = ""


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
                d.get("judge_prompt_sha256", ""),
            )
        )
    return labels


def labeled_on(path: Path | None = None) -> dt.date | None:
    """The date the labels in `path` were authored, or None if it is not stated.

    Read from the `# labeled_on:` header directive, not inferred. AIEV-20 fails
    a calibration set older than 30 days, so this is the input to a gate; a
    guess would be a gate on a guess. None means "the file does not say", which
    the report prints as unknown rather than resolving to today.
    """
    path = path or LABELS_PATH
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _LABELED_ON_RE.match(raw.strip())
        if m:
            return dt.date.fromisoformat(m.group(1))
    return None


def calibration_status(
    c: dict, authored: dt.date | None, today: dt.date | None = None
) -> list[dict]:
    """The three §3 auto-gates, each as {id, name, target, actual, verdict}.

    `verdict` is one of "pass", "fail", or "unmeasured". The third is not a
    softer "fail": it is the honest reading of a κ that is undefined, or an
    agreement computed over four labels against a floor of 37, or a label set
    that does not record when it was labeled. A number that could not have come
    out any other way is not evidence, and rendering it as a passing gate is the
    exact failure this module's `_cohen_kappa` docstring exists to describe.
    """
    today = today or dt.date.today()
    enough = bool(c.get("meets_floor")) and c.get("n_matched", 0) >= STANDARD_MIN_LABELS
    agreement = c.get("agreement")
    if agreement is None:
        agr_actual, agr_verdict = "no scored labels", "unmeasured"
    elif not enough:
        agr_actual = (
            f"{agreement:.1%} over {c['n_matched']} scored labels — below both this "
            f"repo's floor of {c['floor']} and the standard's 50-label set"
        )
        agr_verdict = "unmeasured"
    else:
        agr_actual = f"{agreement:.1%} over {c['n_matched']} scored labels"
        agr_verdict = "pass" if agreement >= AGREEMENT_FLOOR else "fail"

    kappa = c.get("cohen_kappa")
    if kappa is None:
        kap_actual = (
            "undefined — every scored label agreed, so there is no disagreement "
            "to chance-correct against"
        )
        kap_verdict = "unmeasured"
    elif not enough:
        kap_actual = f"{kappa:.3f} over {c['n_matched']} scored labels, below the sample floor"
        kap_verdict = "unmeasured"
    else:
        kap_actual = f"{kappa:.3f} over {c['n_matched']} scored labels"
        kap_verdict = "pass" if kappa >= KAPPA_FLOOR else "fail"

    if authored is None:
        fresh_actual, fresh_verdict = "the label set does not record when it was labeled", "fail"
    else:
        age = (today - authored).days
        fresh_actual = f"labeled {authored.isoformat()}, {age} days ago"
        fresh_verdict = "pass" if age <= FRESHNESS_DAYS else "fail"

    return [
        {
            "id": "AIEV-18",
            "name": "judge-to-human raw agreement",
            "target": f"at least {AGREEMENT_FLOOR:.0%} over a 50-100 label set",
            "actual": agr_actual,
            "verdict": agr_verdict,
        },
        {
            "id": "AIEV-19",
            "name": "Cohen's kappa",
            "target": f"at least {KAPPA_FLOOR:.2f}",
            "actual": kap_actual,
            "verdict": kap_verdict,
        },
        {
            "id": "AIEV-20",
            "name": "calibration freshness",
            "target": f"relabeled within {FRESHNESS_DAYS} days",
            "actual": fresh_actual,
            "verdict": fresh_verdict,
        },
    ]


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


def calibrate(
    records: list[dict],
    labels: list[Label] | None = None,
    criteria: dict[str, str] | None = None,
) -> dict:
    """Compare human labels to the run's judge verdicts.

    Returns coverage (how many labels matched a verdict present in the run),
    raw agreement, and Cohen's kappa. A verdict is a judgment about an answer
    *under a criterion*, so a label is only scored when both still hold:

    * `unmatched` — the judge verdict is missing or errored (None) in the run.
    * `stale`     — the case ran, but its answer changed since the label was
      written (the bound `answer_sha256` no longer matches). Scoring here would
      grade the judge against a human verdict on a different answer, so the
      label is skipped and reported. Relabel with `--emit`.
    * `criterion_stale` — the answer held, but the judge prompt moved since the
      label was written. The human decided one question and the judge in this
      run answered a different one; comparing them measures the prompt edit.
      Skipped and reported, exactly as answer drift is.
    * `unbound` / `criterion_unbound` — a legacy label carrying neither binding.
      Still scored, but surfaced so the gap is visible.

    `criteria` maps judge name to criterion text and defaults to `prompts/` at
    HEAD. Pass a run's own recorded prompts when replaying an older run.
    """
    labels = labels if labels is not None else load_labels()
    criteria = head_criteria(lab.judge for lab in labels) if criteria is None else criteria
    criterion_hashes = {judge: criterion_hash(text) for judge, text in criteria.items()}
    by_id = {r["case_id"]: r for r in records}
    pairs: list[tuple[bool, bool]] = []
    unmatched: list[str] = []
    stale: list[str] = []
    unbound: list[str] = []
    criterion_stale: list[str] = []
    criterion_unbound: list[str] = []
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
        # The same test against the other half of the binding. Only applied when
        # the label recorded a criterion and this side knows what the criterion
        # now is; otherwise there is nothing to compare and it is reported.
        current = criterion_hashes.get(lab.judge)
        if lab.judge_prompt_sha256 and current:
            if lab.judge_prompt_sha256 != current:
                criterion_stale.append(f"{lab.case_id}/{lab.judge}")
                continue
        elif not lab.judge_prompt_sha256:
            criterion_unbound.append(f"{lab.case_id}/{lab.judge}")
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
        "n_criterion_stale": len(criterion_stale),
        "criterion_stale": criterion_stale,
        "n_criterion_unbound": len(criterion_unbound),
        "criterion_unbound": criterion_unbound,
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


# ── --review: the labeling surface ───────────────────────────────────────────

#: Recognized verdict tokens. There is deliberately no entry for the empty
#: string, and none of the four accepted words is reachable by pressing Enter:
#: every outcome, including leaving a row blank, has to be typed. A prompt that
#: resolves on Enter has a default, and a default is a proposed verdict.
_VERDICT_WORDS = {"pass": True, "p": True, "fail": False, "f": False}
_SKIP_WORDS = {"skip", "s"}
_QUIT_WORDS = {"quit", "q"}

#: A run directory named in the worksheet's comment header, e.g.
#: "evals/runs/20260712T050117Z". Lets `--review` find its own evidence.
_RUN_DIR_RE = re.compile(r"(evals/runs/[0-9TZ]+)")


@dataclass
class WorksheetEntry:
    """One line of a worksheet: either a comment/blank (`row is None`) or a row.

    Kept as the parsed dict rather than a typed record so unknown keys survive a
    write-back untouched — a worksheet is a human's file, and a tool that
    rewrites it must not quietly drop fields it did not expect.
    """

    raw: str
    row: dict | None = None


def load_worksheet(path: Path) -> list[WorksheetEntry]:
    """Parse a worksheet into ordered entries, preserving comments and blanks."""
    entries: list[WorksheetEntry] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            entries.append(WorksheetEntry(raw))
            continue
        entries.append(WorksheetEntry(raw, json.loads(line)))
    return entries


def render_worksheet(entries: Iterable[WorksheetEntry]) -> str:
    out = []
    for entry in entries:
        if entry.row is None:
            out.append(entry.raw)
        else:
            out.append(json.dumps(entry.row, ensure_ascii=False))
    return "\n".join(out) + "\n"


def write_worksheet(path: Path, entries: Iterable[WorksheetEntry]) -> None:
    """Rewrite `path` atomically, so an interrupted write cannot truncate it.

    `--review` calls this after every completed row rather than once at the end:
    a session that is closed, killed, or interrupted halfway keeps every verdict
    already given, and the file it leaves behind is a valid worksheet that can
    be reopened.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render_worksheet(entries), encoding="utf-8")
    os.replace(tmp, path)


def unlabeled(entries: Iterable[WorksheetEntry]) -> list[WorksheetEntry]:
    """Entries still awaiting a person: `human_passed` is not a boolean."""
    return [
        e for e in entries if e.row is not None and not isinstance(e.row.get("human_passed"), bool)
    ]


def load_run_records(run_dir: Path) -> dict[str, dict]:
    return {
        rec["case_id"]: rec
        for rec in (
            json.loads(line)
            for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


#: Keys a packet row may never carry: everything in a run record that states how
#: the case was scored. The reviewer's independence rests on not seeing a verdict
#: before giving one, and a committed evidence file is exactly where that would
#: quietly come back. `checks` is on the list with the judge fields because a
#: deterministic check reading "citation_present_and_resolvable: FAIL" tells a
#: reviewer the run's opinion of the answer just as plainly.
_WITHHELD_KEYS = ("judges", "judge_said", "judge_detail", "passed", "checks")

#: Fields `review_block` reads. A packet carries these and nothing else, so what
#: is committed is auditable against what is shown.
_EVIDENCE_KEYS = (
    "case_id",
    "suite",
    "language",
    "question",
    "turns",
    "history",
    "expected_behavior",
    "rationale",
    "answer",
    "kind",
    "refused",
    "passages",
    "passages_recorded",
)


def evidence_path_for(worksheet: Path) -> Path:
    """The packet that belongs to a worksheet, by naming convention.

    `judge_relabel_worksheet_<date>.jsonl` → `judge_relabel_evidence_<date>.jsonl`
    beside it. A convention rather than a pointer inside the file: the pair has
    to survive being copied, renamed by date, and reviewed in a diff.

    The result is never the worksheet itself. A name the convention does not
    match falls back to a suffix, because the alternative is a packet path equal
    to the worksheet path — which would have `--review` load the worksheet as
    its own evidence and read the `judge_said` column as a leaked verdict.
    """
    if "_worksheet_" in worksheet.name:
        return worksheet.with_name(worksheet.name.replace("_worksheet_", "_evidence_"))
    return worksheet.with_name(f"{worksheet.stem}_evidence{worksheet.suffix}")


def build_evidence_packet(records: Iterable[dict], case_ids: Iterable[str]) -> list[dict]:
    """Packet rows for `case_ids`, drawn from a run's records.

    Every judge field is dropped, not merely omitted from what is rendered: a
    file on disk is read by more than one program, and the next reader of this
    packet must not be able to find the verdict in it. Rows come out in
    `case_ids` order so a regenerated packet diffs cleanly against its
    predecessor.
    """
    by_id = {r["case_id"]: r for r in records}
    rows: list[dict] = []
    for case_id in dict.fromkeys(case_ids):
        rec = by_id.get(case_id)
        if rec is None:
            continue
        row = {k: rec[k] for k in _EVIDENCE_KEYS if k in rec}
        row["case_id"] = case_id
        row["passages_recorded"] = "passages" in rec
        for key in _WITHHELD_KEYS:
            row.pop(key, None)
        rows.append(row)
    return rows


@dataclass
class Evidence:
    """Everything `--review` needs to show a worksheet's rows, and where it came
    from. `criteria` maps judge name to the criterion text that judge was given
    for this run; empty means "read `prompts/` at HEAD"."""

    records: dict[str, dict]
    source: str
    header: list[str]
    criteria: dict[str, str]


def load_evidence_packet(path: Path) -> tuple[dict[str, dict], list[str], dict]:
    """Parse a packet into `{case_id: record}`, its comment header, its preamble.

    The header is returned rather than skipped because it is where a packet
    states where its rows came from and what its evidence does not include.
    `--review` prints it once at the start of a session: a reviewer working from
    reconstructed evidence is entitled to know that, before the first verdict
    rather than after the last.

    The preamble is the one line with no `case_id`. It carries the judge
    criteria, which is why a packet is not merely a copy of the run's records:
    `prompts/judge_*.txt` moves, and a reviewer handed today's criterion for an
    answer a previous criterion's judge already ruled on is not calibrating that
    judge — the difference between the two verdicts would be the prompt edit.
    """
    records: dict[str, dict] = {}
    header: list[str] = []
    preamble: dict = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            header.append(line.lstrip("#").strip())
            continue
        row = json.loads(line)
        if "case_id" not in row:
            preamble = row.get("packet", row)
            continue
        for key in _WITHHELD_KEYS:
            if key in row:
                raise ValueError(
                    f"{path.name}: case {row.get('case_id')!r} carries {key!r}. An evidence "
                    "packet must not hold the run's verdict on the case — the reviewer sees "
                    "it only after answering."
                )
        records[row["case_id"]] = row
    return records, header, preamble


def resolve_evidence(
    entries: Iterable[WorksheetEntry],
    worksheet: Path,
    run_dir: Path | None = None,
) -> Evidence:
    """Find the evidence for a worksheet.

    Order is explicit-run, then the run the header names *if it is still on
    disk*, then the committed packet. The middle step is why this function
    exists: `evals/runs/` is gitignored, so the header's run directory is a
    reference to something that may never have existed on this machine, and the
    fallback has to be automatic rather than a flag a reviewer has to know
    about. Raises FileNotFoundError when nothing resolves, naming both places
    it looked.
    """
    entries = list(entries)
    if run_dir is not None:
        return Evidence(load_run_records(run_dir), str(run_dir), [], {})
    header_run = run_dir_from_header(entries)
    if header_run is not None and (header_run / "results.jsonl").exists():
        return Evidence(load_run_records(header_run), str(header_run), [], {})
    packet = evidence_path_for(worksheet)
    if packet.exists():
        records, header, preamble = load_evidence_packet(packet)
        return Evidence(records, str(packet), header, preamble.get("judge_criteria") or {})
    raise FileNotFoundError(
        f"no evidence for {worksheet.name}: the run it names "
        f"({header_run if header_run else 'none'}) is not on disk — evals/runs/ is gitignored — "
        f"and there is no committed packet at {packet.name}. Generate one from a run with "
        "`python -m evals.calibration --pack <run_dir> --for <worksheet>`, or pass --run."
    )


def run_dir_from_header(entries: Iterable[WorksheetEntry]) -> Path | None:
    """The run directory the worksheet's comment header names, if it names one.

    `--worksheet` writes the generating command into the header, so a reviewer
    does not have to remember which run their evidence lives in. A worksheet
    with no such header simply requires `--run`.
    """
    for entry in entries:
        if entry.row is not None:
            continue
        m = _RUN_DIR_RE.search(entry.raw)
        if m:
            return config.REPO_ROOT / m.group(1)
    return None


def binding_problem(row: dict, record: dict | None) -> str | None:
    """Why this row cannot be labeled from this run, or None when it can.

    The `answer_sha256` binding is checked *before* the evidence is printed, not
    after the verdict is typed: a reviewer must never read one answer and have
    the verdict recorded against another.
    """
    if record is None:
        return f"case {row.get('case_id')!r} is not in this run"
    if "answer" not in record:
        return f"case {row.get('case_id')!r} has no recorded answer in this run"
    declared = row.get("answer_sha256") or ""
    if not declared:
        return "row carries no answer_sha256, so nothing binds a verdict to an answer"
    actual = answer_hash(record["answer"])
    if actual != declared:
        return (
            f"the recorded answer hashes to {actual[:12]}… but the row is bound to "
            f"{declared[:12]}… — the answer changed since this worksheet was generated; "
            "regenerate it against the current run"
        )
    return None


def _passages_block(record: dict) -> str:
    """Render exactly the provenance a reviewer needs to check a dated claim:
    doc id, agency, title, section, score, source URL and fetch date, then
    the text — the same fields `assistant.answer._format_passages` shows the
    answer model and `evals.judges._passages_block` shows the judge (issue
    #142). Before this, a reviewer saw `chunk_id`/`section`/`score`/`text`
    only, which is the same blind spot that made the groundedness judge fail
    fresh-001: asked to check a dated claim against passages the dates had
    been stripped from. Four of the worksheet's 37 rows are freshness cases;
    a human agreeing with a wrong verdict for the same missing-provenance
    reason would not calibrate anything.

    Two absences are rendered as absences, never as values. A record that never
    recorded its passages says so; it does not borrow "(none retrieved)", which
    is a claim about retrieval. A passage with no recorded score says "score not
    recorded"; it does not print `score None`, which reads as a score of zero.
    """
    passages = record.get("passages") or []
    if not passages:
        if record.get("passages_recorded") is False:
            return "  (not recorded in this evidence packet — see its header)"
        return "  (none retrieved)"
    out = []
    for i, p in enumerate(passages, start=1):
        score = p.get("score")
        score_text = "score not recorded" if score is None else f"score {score}"
        head = (
            f"  [{i}/{len(passages)}] [{p.get('chunk_id')}] {p.get('agency', '')} — "
            f"{p.get('doc_title', '')} — {p.get('section')} ({score_text})\n"
            f"  (source: {p.get('url', '')}, fetched {p.get('fetch_date', '')})"
        )
        body = "\n".join(f"    {line}" for line in str(p.get("text", "")).splitlines())
        out.append(f"{head}\n{body}")
    return "\n\n".join(out)


def _history_block(record: dict) -> str:
    parts = []
    for turn in record.get("history") or []:
        parts.append(f"  Rider: {turn.get('q')}\n  Assistant answered: {turn.get('a')}")
    for turn in (record.get("turns") or [])[:-1]:
        parts.append(f"  Rider: {turn}")
    return "\n\n".join(parts)


def review_block(
    row: dict, record: dict, position: str = "", criteria: dict[str, str] | None = None
) -> str:
    """The evidence for one row, with the judge's verdict deliberately absent.

    Everything here is read from the committed evidence and the committed judge
    prompt; nothing is inferred, and nothing hints at an answer. See the module
    docstring for why the judge's call is withheld until after the human has
    given theirs.

    `criteria` overrides `prompts/` with the criterion text this run's judge was
    actually given. It is not a nicety: a worksheet outlives the prompt it was
    generated under, and asking a human to apply v3's criterion to an answer v2's
    judge ruled on measures the edit between them, not the judge.
    """
    judge = row.get("judge", "")
    criteria = criteria or {}
    criterion = criteria.get(judge) or (config.load_prompt(f"judge_{judge}") if judge else "")
    where = (
        "as this run recorded it, from the evidence packet"
        if criteria.get(judge)
        else f"prompts/judge_{judge}.txt"
    )
    lines = [
        "=" * 78,
        f"{position}{row.get('case_id')} · judge: {judge} · suite: {row.get('suite')}",
        "=" * 78,
        "",
        f"-- what the {judge} judge was asked to decide ({where}) {'-' * 6}",
        criterion.rstrip(),
        "",
        f"-- question {'-' * 60}",
        f"  {record.get('question')}",
    ]
    history = _history_block(record)
    if history:
        lines += ["", f"-- prior turns the judge also saw {'-' * 39}", history]
    if judge == "helpfulness":
        lines += [
            "",
            f"-- expected behavior and case rationale {'-' * 33}",
            f"  expected_behavior: {record.get('expected_behavior')}",
            f"  rationale: {record.get('rationale')}",
        ]
    if judge == "groundedness":
        heading = f"-- retrieved passages the judge graded against {'-' * 26}"
    else:
        heading = (
            "-- retrieved passages (CONTEXT ONLY: the helpfulness judge was not shown these; "
            "it grades against the expected behavior, not the corpus) --"
        )
    lines += ["", heading, _passages_block(record)]
    kind = record.get("kind")
    if kind:
        answer_heading = f"-- the assistant's answer ({kind}) {'-' * 40}"
    elif record.get("refused"):
        answer_heading = f"-- the assistant's answer (it declined) {'-' * 33}"
    else:
        answer_heading = f"-- the assistant's answer {'-' * 47}"
    lines += [
        "",
        answer_heading,
        "\n".join(f"  {line}" for line in str(record.get("answer", "")).splitlines()),
        "",
    ]
    return "\n".join(lines)


def judge_reveal(row: dict, record: dict) -> str:
    """What the judge said — printed only after the human has answered.

    An evidence packet carries no judge fields, by design, so when the review is
    running off one the reveal falls back to the worksheet row's own
    `judge_said`. That is a verdict without its reasoning, and it says so: the
    reasoning lived in the run directory and a pruned run does not come back.
    """
    judge = row.get("judge")
    verdict = next((v for v in record.get("judges", []) if v.get("name") == judge), None)
    if verdict is None:
        said_in_row = row.get("judge_said")
        if isinstance(said_in_row, bool):
            said = "PASS" if said_in_row else "FAIL"
            return (
                f"  the {judge} judge said: {said}\n"
                "  its reasoning: not recorded — this row's evidence came from a packet, "
                "and the run that held the judge's reasoning is gone"
            )
        return f"  (no {judge} verdict recorded in this run)"
    said = "PASS" if verdict.get("passed") else "FAIL"
    return f"  the {judge} judge said: {said}\n  its reasoning: {verdict.get('detail', '')}"


def apply_label(row: dict, human_passed: bool, note: str, judge_prompt_sha256: str = "") -> dict:
    """Record a verdict on a row. Requires a written reason: the note replaces
    the TEMPLATE marker `load_labels` refuses to score, so a row cannot become
    scoreable without someone having said why.

    `judge_prompt_sha256` stamps the criterion the reviewer was actually shown,
    so the verdict is bound to the question it answered as well as to the answer
    it was about. Without it a later prompt bump silently re-points the label at
    a judge deciding something else.
    """
    if not isinstance(human_passed, bool):
        raise ValueError("human_passed must be True or False")
    if not note.strip():
        raise ValueError("a label needs a written reason; the note replaces the TEMPLATE marker")
    labeled = {**row, "human_passed": human_passed, "note": note.strip()}
    if judge_prompt_sha256:
        labeled["judge_prompt_sha256"] = judge_prompt_sha256
    return labeled


def _ask(prompt: str, input_fn: Callable[[str], str], out: Callable[[str], None]) -> str:
    try:
        return input_fn(prompt).strip()
    except EOFError:
        out("\n(end of input)")
        return "quit"


def _session_plan(todo: list[WorksheetEntry], limit: int | None) -> str:
    """What this sitting will actually cost, before it starts.

    The worksheet is generated failures-first, so the rows that carry the most
    information about the judge are the ones already at the top. Saying that out
    loud, with a count and an honest per-row estimate, is the difference between
    "37 rows of unspecified work" and a task somebody can decide to start.
    """
    failures = sum(1 for e in todo if e.row is not None and e.row.get("judge_said") is False)
    grounded = sum(1 for e in todo if e.row is not None and e.row.get("judge") == "groundedness")
    lines = [
        f"  {len(todo)} rows left: {grounded} groundedness, {len(todo) - grounded} helpfulness.",
    ]
    if failures:
        lines.append(
            f"  The first {failures} are pairs the judge FAILED. They are first on purpose — "
            "a\n  sample drawn where the judge never objects cannot disagree with it."
        )
    lines.append(
        "  A groundedness row means checking each claim against its passages, so budget a\n"
        "  few minutes; a helpfulness row is usually under a minute."
    )
    if limit:
        lines.append(f"  Stopping after {limit} this sitting.")
    lines.append("  Type quit after any row. Every verdict is written to disk as you give it.")
    return "\n".join(lines)


def review_worksheet(
    path: Path,
    run_dir: Path | None = None,
    *,
    limit: int | None = None,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Walk a worksheet's unlabeled rows, show the evidence, record verdicts.

    Returns a process exit code. Labeled rows are skipped, so a partly-labeled
    worksheet is safe to reopen; each verdict is flushed to disk as it is given.
    `limit` bounds a single sitting, which is the point: 37 rows is not an
    evening, and a tool that can only be run to completion is a tool that does
    not get run.
    """
    entries = load_worksheet(path)
    try:
        evidence = resolve_evidence(entries, path, run_dir)
    except FileNotFoundError as exc:
        out(str(exc))
        return 2
    records = evidence.records

    rows = [e for e in entries if e.row is not None]
    todo = unlabeled(entries)
    if limit is not None:
        todo = todo[:limit]
    out(
        f"{path.name}: {len(rows)} rows, {len(rows) - len(unlabeled(entries))} labeled, "
        f"{len(unlabeled(entries))} to go. Evidence from {evidence.source}.\n"
        "Verdicts are yours alone: nothing here is pre-filled, and what the judge\n"
        "decided is shown only after you answer.\n"
    )
    if evidence.header:
        out("About this evidence packet:")
        for line in evidence.header:
            out(f"  {line}")
        out("")
    out(_session_plan(todo, limit))
    out("")
    # Hashes of the criteria this session actually puts on screen, so each
    # recorded verdict is bound to the question it answered. Falls back to
    # HEAD's prompts when the evidence is a run directory rather than a packet.
    judges_here = [e.row["judge"] for e in todo if e.row is not None and "judge" in e.row]
    shown_criteria = {
        judge: criterion_hash(text)
        for judge, text in ({**head_criteria(judges_here), **evidence.criteria}).items()
    }
    labeled = skipped = refused = 0
    for i, entry in enumerate(todo, start=1):
        row = entry.row
        assert row is not None  # `unlabeled` only returns row entries
        record = records.get(row.get("case_id", ""))
        problem = binding_problem(row, record)
        if problem is not None:
            refused += 1
            out(f"[{i}/{len(todo)}] {row.get('case_id')}/{row.get('judge')}: SKIPPED — {problem}\n")
            continue
        assert record is not None  # binding_problem returns a string when it is None
        out(review_block(row, record, f"[{i}/{len(todo)}] ", evidence.criteria))

        verdict: bool | None = None
        while verdict is None:
            reply = _ask(
                "Does this answer satisfy the criterion above? [pass / fail / skip / quit] > ",
                input_fn,
                out,
            ).lower()
            if reply in _QUIT_WORDS:
                out(
                    f"\nstopping. {labeled} labeled this session; {path.name} is up to date, "
                    f"{len(unlabeled(entries))} of {len(rows)} rows still blank. "
                    "Reopen it to carry on."
                )
                return 0
            if reply in _SKIP_WORDS:
                break
            if reply in _VERDICT_WORDS:
                verdict = _VERDICT_WORDS[reply]
            else:
                out("  type pass, fail, skip, or quit. There is no default.")
        if verdict is None:
            skipped += 1
            out("  left blank.\n")
            continue

        out("")
        out(judge_reveal(row, record))
        note = ""
        while not note:
            note = _ask("  your reason (one line, required) > ", input_fn, out)
            if note.lower() in _QUIT_WORDS:
                out(
                    "\nstopping before recording this verdict — a label needs a written "
                    f"reason. {labeled} labeled this session."
                )
                return 0
            if not note:
                out("  a reason is required; it replaces the TEMPLATE marker.")
        entry.row = apply_label(row, verdict, note, shown_criteria.get(row.get("judge", ""), ""))
        write_worksheet(path, entries)
        labeled += 1
        out("  recorded.\n")

    remaining = len(unlabeled(entries))
    out(
        f"\n{labeled} labeled, {skipped} left blank, {refused} refused on a changed or "
        f"missing answer. {remaining} of {len(rows)} rows still blank."
    )
    if remaining:
        out("Reopen this worksheet to carry on; nothing already recorded is asked again.")
    return 0


def worksheet_header(run_dir: Path, size: int, failures: int) -> list[str]:
    """The comment header a generated worksheet ships with.

    `--worksheet` used to print rows and nothing else, so the run reference that
    `run_dir_from_header` looks for existed only because someone hand-wrote it
    into the 2026-08-05 file. A worksheet generated today would have had no way
    to name its own evidence at all. It is emitted now, along with the one
    command that has to be run next: a worksheet without a packet beside it is
    labelable exactly as long as its run directory survives.
    """
    rel = run_dir.name if run_dir.is_absolute() else str(run_dir)
    if failures:
        stratification = [
            f"# CLAUDE.md). The first {failures} are every pair the judge FAILED; the rest are "
            "drawn",
            "# round-robin across suites. Failures come first on purpose — a sample drawn from",
            "# the region where the judge never objects cannot disagree with it.",
        ]
    else:
        stratification = [
            "# CLAUDE.md), drawn round-robin across suites. The judge failed no pair in this",
            "# run, so this sample contains no case where a human is especially likely to",
            "# differ. Read a high agreement off it with that in mind.",
        ]
    return [
        f"# Judge-calibration relabeling worksheet, generated from {rel} by:",
        "#",
        f"#     uv run python -m evals.calibration --worksheet evals/runs/{rel}",
        "#",
        f"# {size} rows: the sample floor (10% of that run's judged (case, judge) pairs, per",
        *stratification,
        "#",
        "# NEXT, before the run directory is pruned (evals/runs/ is gitignored):",
        "#",
        "#     uv run python -m evals.calibration --pack evals/runs/"
        + f"{rel} --for <this file> \\",
        "#         > <this file with _worksheet_ replaced by _evidence_>",
        "#",
        "# Without that packet this worksheet stops being labelable the moment the run",
        "# directory goes, which is how the 2026-08-05 sheet reached 0 of 37 (#143).",
        "#",
        "# To label it:  make relabel WORKSHEET=<this file>",
        "#",
        "# That walks the unlabeled rows one at a time, printing each row's judge criterion,",
        "# the rider's question, the retrieved passages and the answer, then asks for your",
        "# verdict and a one-line reason. It never proposes a verdict, and it withholds",
        "# `judge_said` until after you have given yours. Add --limit N to bound a sitting.",
        "#",
        "# Rows still carrying the TEMPLATE marker or a null verdict are refused by",
        "# evals/calibration.py::load_labels: an unreviewed pair is not a human label.",
    ]


def _pack_header(run_dir: Path, worksheet: Path) -> list[str]:
    """The comment header a fresh packet ships with. It states the one thing a
    reader of a committed evidence file needs to be told immediately: the judge's
    verdict is not in here, and the run it came from will not survive."""
    return [
        f"# Evidence packet for {worksheet.name}, written from {run_dir.name}.",
        "#",
        "# evals/runs/ is gitignored, so this file is the only copy of that run's evidence",
        "# that survives a prune or a fresh clone. Without it `--review` has nothing to",
        "# show and the worksheet cannot be labeled by anyone.",
        "#",
        "# It holds no judge verdict and no judge reasoning. It does hold the judge",
        "# criteria as of packing, so a later prompt bump cannot change the question a",
        "# reviewer is asked about answers an earlier judge already ruled on.",
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
    parser.add_argument(
        "--review",
        metavar="WORKSHEET",
        help="label a worksheet interactively: show each row's evidence, record your verdict",
    )
    parser.add_argument(
        "--run",
        metavar="RUN_DIR",
        help="run directory holding the evidence for --review (default: the worksheet's "
        "comment header, then the committed evidence packet beside it)",
    )
    parser.add_argument(
        "--pack",
        metavar="RUN_DIR",
        help="write a committed evidence packet for --for's worksheet from this run, so the "
        "worksheet survives the run directory being pruned (evals/runs/ is gitignored)",
    )
    parser.add_argument(
        "--for",
        dest="for_worksheet",
        metavar="WORKSHEET",
        help="the worksheet --pack is building evidence for",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="stop after N rows this sitting (--review); the worksheet is resumable",
    )
    args = parser.parse_args()
    if args.emit:
        for row in emit_label_templates(Path(args.emit)):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.worksheet:
        run = Path(args.worksheet)
        rows = stratified_worksheet(run)
        failures = sum(1 for r in rows if r.get("judge_said") is False)
        for line in worksheet_header(run, len(rows), failures):
            print(line)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.pack:
        if not args.for_worksheet:
            parser.error("--pack needs --for <worksheet>: a packet is evidence for a worksheet")
        entries = load_worksheet(Path(args.for_worksheet))
        case_ids = [e.row["case_id"] for e in entries if e.row is not None]
        judges = sorted({e.row["judge"] for e in entries if e.row is not None})
        records = load_run_records(Path(args.pack)).values()
        for line in _pack_header(Path(args.pack), Path(args.for_worksheet)):
            print(line)
        # The criteria go in first, so a packet read top to bottom states what
        # the judge was asked before it shows anything the judge was asked about.
        print(
            json.dumps(
                {
                    "packet": {
                        "judge_criteria": {j: config.load_prompt(f"judge_{j}") for j in judges},
                        "judge_criteria_source": "prompts/judge_*.txt as of packing",
                    }
                },
                ensure_ascii=False,
            )
        )
        rows = build_evidence_packet(records, case_ids)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        # A packet short of the worksheet is a worksheet with unlabelable rows.
        # Said on stderr so it survives the redirect that writes the packet.
        missing = [c for c in dict.fromkeys(case_ids) if c not in {r["case_id"] for r in rows}]
        if missing:
            print(
                f"warning: {len(missing)} of {len(set(case_ids))} cases are not in {args.pack} "
                f"and have no evidence: {', '.join(missing)}",
                file=sys.stderr,
            )
        return 0
    if args.review:
        return review_worksheet(
            Path(args.review),
            Path(args.run) if args.run else None,
            limit=args.limit,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
