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

    python -m evals.calibration --emit <run_dir>      # label templates from a run
    python -m evals.calibration --worksheet <run_dir> # floor-sized, failures first
    python -m evals.calibration --review <worksheet>  # label it, one row at a time

## `--review`: the labeling surface

`--emit` and `--worksheet` produce rows; neither shows the evidence a verdict
needs. Labeling a worksheet meant opening `results.jsonl` and hand-digging the
answer, its retrieved passages, and the case's expected behavior out of it,
once per row, 37 times. That friction is a fair share of why the committed
sample has sat at four labels against a floor of 37. `--review` puts the
evidence on the screen and writes the verdict back.

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
    """
    passages = record.get("passages") or []
    if not passages:
        return "  (none retrieved)"
    out = []
    for p in passages:
        head = (
            f"  [{p.get('chunk_id')}] {p.get('agency', '')} — "
            f"{p.get('doc_title', '')} — {p.get('section')} (score {p.get('score')})\n"
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


def review_block(row: dict, record: dict, position: str = "") -> str:
    """The evidence for one row, with the judge's verdict deliberately absent.

    Everything here is read from the committed run directory and the committed
    judge prompt; nothing is inferred, and nothing hints at an answer. See the
    module docstring for why the judge's call is withheld until after the human
    has given theirs.
    """
    judge = row.get("judge", "")
    criterion = config.load_prompt(f"judge_{judge}") if judge else ""
    lines = [
        "=" * 78,
        f"{position}{row.get('case_id')} · judge: {judge} · suite: {row.get('suite')}",
        "=" * 78,
        "",
        f"-- what the {judge} judge is asked to decide (prompts/judge_{judge}.txt) {'-' * 8}",
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
    lines += [
        "",
        f"-- the assistant's answer ({record.get('kind')}) {'-' * 40}",
        "\n".join(f"  {line}" for line in str(record.get("answer", "")).splitlines()),
        "",
    ]
    return "\n".join(lines)


def judge_reveal(row: dict, record: dict) -> str:
    """What the judge said — printed only after the human has answered."""
    judge = row.get("judge")
    verdict = next((v for v in record.get("judges", []) if v.get("name") == judge), None)
    if verdict is None:
        return f"  (no {judge} verdict recorded in this run)"
    said = "PASS" if verdict.get("passed") else "FAIL"
    return f"  the {judge} judge said: {said}\n  its reasoning: {verdict.get('detail', '')}"


def apply_label(row: dict, human_passed: bool, note: str) -> dict:
    """Record a verdict on a row. Requires a written reason: the note replaces
    the TEMPLATE marker `load_labels` refuses to score, so a row cannot become
    scoreable without someone having said why."""
    if not isinstance(human_passed, bool):
        raise ValueError("human_passed must be True or False")
    if not note.strip():
        raise ValueError("a label needs a written reason; the note replaces the TEMPLATE marker")
    return {**row, "human_passed": human_passed, "note": note.strip()}


def _ask(prompt: str, input_fn: Callable[[str], str], out: Callable[[str], None]) -> str:
    try:
        return input_fn(prompt).strip()
    except EOFError:
        out("\n(end of input)")
        return "quit"


def review_worksheet(
    path: Path,
    run_dir: Path | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Walk a worksheet's unlabeled rows, show the evidence, record verdicts.

    Returns a process exit code. Labeled rows are skipped, so a partly-labeled
    worksheet is safe to reopen; each verdict is flushed to disk as it is given.
    """
    entries = load_worksheet(path)
    run_dir = run_dir or run_dir_from_header(entries)
    if run_dir is None:
        out("cannot tell which run this worksheet was generated from; pass --run <run_dir>")
        return 2
    if not (run_dir / "results.jsonl").exists():
        out(f"no results.jsonl in {run_dir}")
        return 2
    records = load_run_records(run_dir)

    rows = [e for e in entries if e.row is not None]
    todo = unlabeled(entries)
    out(
        f"{path.name}: {len(rows)} rows, {len(rows) - len(todo)} labeled, "
        f"{len(todo)} to go. Evidence from {run_dir}.\n"
        "Verdicts are yours alone: nothing here is pre-filled, and what the judge\n"
        "decided is shown only after you answer.\n"
    )
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
        out(review_block(row, record, position=f"[{i}/{len(todo)}] "))

        verdict: bool | None = None
        while verdict is None:
            reply = _ask(
                "Does this answer satisfy the criterion above? [pass / fail / skip / quit] > ",
                input_fn,
                out,
            ).lower()
            if reply in _QUIT_WORDS:
                out(f"\nstopping. {labeled} labeled this session; {path.name} is up to date.")
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
        entry.row = apply_label(row, verdict, note)
        write_worksheet(path, entries)
        labeled += 1
        out("  recorded.\n")

    remaining = len(unlabeled(entries))
    out(
        f"\n{labeled} labeled, {skipped} left blank, {refused} refused on a changed or "
        f"missing answer. {remaining} of {len(rows)} rows still blank."
    )
    return 0


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
        help="run directory holding the evidence for --review (default: read from the "
        "worksheet's comment header)",
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
    if args.review:
        return review_worksheet(Path(args.review), Path(args.run) if args.run else None)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
