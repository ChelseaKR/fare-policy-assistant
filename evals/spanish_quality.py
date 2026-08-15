"""Native-Spanish answer quality: the half of bilingual equity nothing measures.

`docs/I18N.md` §7 asks for a disaggregated evaluation proving Spanish
answer-quality holds within 5 points of English on a native-ES benchmark. Half
of that shipped in 2026-07: the blocking parity gate
(`evals/runner.py::check_parity`) fails a run whose Spanish-vs-mirrored-English
pass delta exceeds 5 points, and since 2026-08-05 `mirror_problems` verifies the
pairs are pairs before the gate reads them.

**That gate cannot see whether the Spanish is good Spanish.** It compares
pass/fail on the two answers, and both verdicts come from the same deterministic
checks: a citation resolves, a required fact appears, the language classifier
says "es", a determination phrase is absent. Every one of those is satisfied by
Spanish that is stilted, wrongly registered, or uses terms no California agency
publishes. The current delta is 0.0 points over 22 verified pairs; that number
would not move if every Spanish answer read like a machine translation, because
nothing in it is looking.

## What this module measures, and why a person has to do it

Three properties, none of which a deterministic check or the existing language
classifier can decide:

* **fluent** — reads as Spanish written by a person, not as English word order
  with Spanish words in it.
* **register** — the formality a public agency uses with a rider (usted, plain
  and specific), neither bureaucratic nor familiar.
* **terminology** — the terms California agencies actually publish in Spanish
  (`tarifa con descuento`, `adulto mayor`, `comprobante de edad`), not
  anglicisms or invented equivalents. `corpus/processed/mst-fares-es.md` is the
  one agency-authored Spanish document in the corpus and is the reference.

A model cannot rate these without being the thing under test, and the language
classifier answers a different question entirely: whether the text is Spanish at
all, not whether it is Spanish worth reading. `language_match` already passes on
every Spanish case in the promoted run, which is the point — that check is
satisfied and these three questions are still open.

## The sheet

`--worksheet <run_dir>` emits a **census**, not a sample: every Spanish answer
the run produced, one row each, bound to the answer by `answer_sha256`. There is
no sampling error to argue about at this size, and 28 rows is a sitting that a
person can finish.

Seven of those 28 are `refused_input` — the fixed gettext refusal strings, which
`docs/I18N.md` records as pre-existing human translation carried over verbatim.
They are marked `fixed_string: true`: rating them tells you about the catalog,
not about the model, and the summary reports the two populations separately so
21 model-written answers are never diluted by 7 strings a person already wrote.

Every rating starts blank and stays blank until a person fills it in.
`load_ratings` refuses a row that still carries the TEMPLATE marker or a
non-boolean verdict, exactly as `evals.calibration.load_labels` does, and
`summarize` reports an unrated sheet as **unmeasured** — never as zero, and
never as a pass.

## What this still does not cover

§7 also asks for an **externally sourced** question set: Spanish questions as
riders actually write them, not this repo's own Spanish, which was authored
alongside the English mirrors. Every row's `question_source` records which it is,
so the shortfall is a field in the data rather than a caveat in prose. Sourcing
that set needs a person too, and nothing here should be read as having done it.

    python -m evals.spanish_quality --worksheet <run_dir>  # emit the blank census
    python -m evals.spanish_quality --review <sheet>       # rate it, one row at a time
    python -m evals.spanish_quality --status <sheet>       # what is measured so far
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from assistant import config
from evals.calibration import (
    TEMPLATE_NOTE,
    answer_hash,
    load_run_records,
    load_worksheet,
    run_dir_from_header,
    write_worksheet,
)

SHEET_PATH = config.REPO_ROOT / "evals" / "spanish" / "native_es_rubric_2026-08-05.jsonl"

#: The rated dimensions, with the question a rater is actually answering. These
#: are published rather than left to the rater's judgement of what "quality"
#: means, so two raters are answering the same question and a disagreement is
#: about the answer rather than about the rubric.
RUBRIC: dict[str, str] = {
    "fluent": (
        "Does this read as Spanish a person wrote, rather than English word order "
        "with Spanish words in it?"
    ),
    "register": (
        "Is this the formality a public agency uses with a rider — usted, plain and "
        "specific, neither bureaucratic nor familiar?"
    ),
    "terminology": (
        "Does it use the terms California agencies publish in Spanish (see "
        "corpus/processed/mst-fares-es.md), rather than anglicisms or invented "
        "equivalents?"
    ),
}

#: Where a row's question came from. Every row in the first census is
#: `repo_mirror`: this repo authored its Spanish questions alongside the English
#: ones. `external_native` is what §7's benchmark asks for and what nothing here
#: has yet supplied.
QUESTION_SOURCES = ("repo_mirror", "external_native")

SPANISH = "es"


def spanish_records(records: list[dict]) -> list[dict]:
    """Every Spanish-language record in a run, in run order."""
    return [r for r in records if r.get("language") == SPANISH]


def build_worksheet(run_dir: Path) -> list[dict]:
    """The blank census: one row per Spanish answer, bound to that answer.

    Emits null ratings. It has to: a template pre-filled with anything — the
    English mirror's verdict, the deterministic checks' verdict, a model's
    opinion — would be graded as a human's, which is the circularity closed on
    `evals.calibration --emit` and must not come back on a new sheet.
    """
    records = load_run_records(run_dir)
    rows = []
    for record in spanish_records(list(records.values())):
        rows.append(
            {
                "case_id": record["case_id"],
                "suite": record.get("suite", ""),
                "kind": record.get("kind", ""),
                # A refusal renders the committed gettext catalog, not model
                # output. Named so a rater knows what they are rating and the
                # summary can keep the two populations apart.
                "fixed_string": record.get("kind") == "refused_input",
                "question_source": "repo_mirror",
                "answer_sha256": answer_hash(record.get("answer", "")),
                **{dimension: None for dimension in RUBRIC},
                "note": f"{TEMPLATE_NOTE} — read the Spanish answer, rate each dimension, "
                "and replace this note",
            }
        )
    return rows


def load_ratings(path: Path | None = None) -> list[dict]:
    """Rated rows only, or raise. Blank and template rows are refused, never
    skipped: a sheet that stalls half-done should stop the measurement rather
    than quietly shrink its denominator to the rows someone got to."""
    path = path or SHEET_PATH
    rows = [e.row for e in load_worksheet(path) if e.row is not None]
    for row in rows:
        where = f"{path.name}: {row.get('case_id', '?')}"
        if str(row.get("note", "")).startswith(TEMPLATE_NOTE):
            raise ValueError(
                f"{where} still carries the {TEMPLATE_NOTE} marker — an unrated row is not a "
                "rating. Rate every dimension and replace the note, or delete the row."
            )
        for dimension in RUBRIC:
            if not isinstance(row.get(dimension), bool):
                raise ValueError(
                    f"{where}: {dimension} must be true or false, not "
                    f"{row.get(dimension)!r} — an unrated dimension is not a verdict"
                )
        if row.get("question_source") not in QUESTION_SOURCES:
            raise ValueError(
                f"{where}: question_source must be one of {QUESTION_SOURCES}, not "
                f"{row.get('question_source')!r}"
            )
    return rows


def summarize(rows: list[dict], floor: int) -> dict:
    """What the sheet establishes so far. Unrated is `None`, never `0.0`.

    `floor` is the census size — every Spanish answer in the run. Below it the
    result is `measured: False` and every rate is `None`, because a partial
    census read as a percentage is a claim about answers nobody looked at.
    """
    model_written = [r for r in rows if not r.get("fixed_string")]
    external = [r for r in rows if r.get("question_source") == "external_native"]
    measured = len(rows) >= floor and floor > 0
    rates: dict[str, float | None] = {d: None for d in RUBRIC}
    if measured and model_written:
        for dimension in RUBRIC:
            passed = sum(1 for r in model_written if r[dimension])
            rates[dimension] = round(100 * passed / len(model_written), 1)
    return {
        "rated": len(rows),
        "floor": floor,
        "measured": measured,
        "shortfall": max(0, floor - len(rows)),
        "model_written": len(model_written),
        "fixed_string": len(rows) - len(model_written),
        "external_native_questions": len(external),
        "rates": rates,
    }


def status_lines(summary: dict) -> list[str]:
    """Human-readable status. Says "not measured" in those words when it is not."""
    if not summary["measured"]:
        return [
            f"**Not measured.** {summary['rated']} of {summary['floor']} Spanish answers "
            f"rated; {summary['shortfall']} to go "
            "(`evals/spanish/native_es_rubric_2026-08-05.jsonl`, filled with "
            "`make spanish-quality`). The parity table above is a pass/fail comparison "
            "between a Spanish answer and its English mirror; both verdicts come from "
            "checks that ask whether a citation resolves and a required fact appears. "
            "Neither asks whether the Spanish reads as Spanish, so a 0.0-point parity "
            "delta is consistent with Spanish of any quality.",
            "No native-Spanish question set has been sourced either: "
            f"{summary['external_native_questions']} of {summary['floor']} rows carry an "
            "externally sourced question, so even once rated this describes the Spanish "
            "this repo wrote, not Spanish as riders write it.",
        ]
    lines = [
        f"Native-Spanish answer quality, over {summary['model_written']} model-written "
        f"Spanish answers ({summary['fixed_string']} committed catalog strings rated "
        "separately):"
    ]
    for dimension, question in RUBRIC.items():
        lines.append(f"- {dimension}: {summary['rates'][dimension]}% — {question}")
    if not summary["external_native_questions"]:
        lines.append(
            "Every rated row's question came from this repo, not from a native-Spanish "
            "source; §7's externally sourced benchmark is still outstanding."
        )
    return lines


# ── --review: rate a row at a time, blind to everything but the answer ───────

_YES = {"yes", "y"}
_NO = {"no", "n"}
_SKIP = {"skip", "s"}
_QUIT = {"quit", "q"}


def rating_block(row: dict, record: dict, position: str = "") -> str:
    """The Spanish answer and its question. Deliberately no English mirror and
    no check results: the rater is judging the Spanish on its own terms, and a
    mirror on the screen turns a language-quality rating into a translation
    comparison — which is what parity already measures and this does not."""
    lines = [
        "=" * 78,
        f"{position}{row.get('case_id')} · {row.get('suite')} · {record.get('kind')}"
        + (" · committed catalog string, not model output" if row.get("fixed_string") else ""),
        "=" * 78,
        "",
        f"-- pregunta {'-' * 60}",
        f"  {record.get('question')}",
        "",
        f"-- respuesta {'-' * 59}",
        "\n".join(f"  {line}" for line in str(record.get("answer", "")).splitlines()),
        "",
    ]
    return "\n".join(lines)


def _ask(prompt: str, input_fn: Callable[[str], str], out: Callable[[str], None]) -> str:
    try:
        return input_fn(prompt).strip().lower()
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
    """Rate the unrated rows. Same three refusals as the calibration reviewer:
    no default rating, a binding check before anything is shown, and an atomic
    write after each finished row so an interrupted sitting keeps its work."""
    entries = load_worksheet(path)
    run_dir = run_dir or run_dir_from_header(entries)
    if run_dir is None:
        out("cannot tell which run this sheet was generated from; pass --run <run_dir>")
        return 2
    if not (run_dir / "results.jsonl").exists():
        out(f"no results.jsonl in {run_dir}")
        return 2
    records = load_run_records(run_dir)

    rows = [e for e in entries if e.row is not None]
    todo = [e for e in rows if not all(isinstance(e.row.get(d), bool) for d in RUBRIC)]
    out(
        f"{path.name}: {len(rows)} Spanish answers, {len(rows) - len(todo)} rated, "
        f"{len(todo)} to go. Evidence from {run_dir}.\n"
        "Rate the Spanish on its own terms. Nothing is pre-filled and no English\n"
        "mirror is shown; parity already compares those.\n"
    )
    rated = 0
    for i, entry in enumerate(todo, start=1):
        row = entry.row
        assert row is not None
        record = records.get(row.get("case_id", ""))
        if record is None or answer_hash(record.get("answer", "")) != row.get("answer_sha256"):
            out(
                f"[{i}/{len(todo)}] {row.get('case_id')}: SKIPPED — the recorded answer no "
                "longer matches this row's answer_sha256; regenerate the sheet\n"
            )
            continue
        out(rating_block(row, record, position=f"[{i}/{len(todo)}] "))

        verdicts: dict[str, bool] = {}
        aborted = False
        for dimension, question in RUBRIC.items():
            while dimension not in verdicts:
                reply = _ask(f"  {question}\n  [yes / no / skip / quit] > ", input_fn, out)
                if reply in _QUIT:
                    out(f"\nstopping. {rated} rated this session; {path.name} is up to date.")
                    return 0
                if reply in _SKIP:
                    aborted = True
                    break
                if reply in _YES or reply in _NO:
                    verdicts[dimension] = reply in _YES
                else:
                    out("  type yes, no, skip, or quit. There is no default.")
            if aborted:
                break
        if aborted:
            out("  left blank.\n")
            continue

        note = ""
        while not note:
            note = _ask("  one line on what you saw (required) > ", input_fn, out)
            if note in _QUIT:
                out(f"\nstopping before recording this row. {rated} rated this session.")
                return 0
            if not note:
                out("  a note is required; it replaces the TEMPLATE marker.")
        entry.row = {**row, **verdicts, "note": note}
        write_worksheet(path, entries)
        rated += 1
        out("  recorded.\n")

    remaining = sum(
        1
        for e in rows
        if e.row is not None and not all(isinstance(e.row.get(d), bool) for d in RUBRIC)
    )
    out(f"\n{rated} rated. {remaining} of {len(rows)} Spanish answers still unrated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", metavar="RUN_DIR", help="emit the blank rating census")
    parser.add_argument("--review", metavar="SHEET", help="rate a sheet, one answer at a time")
    parser.add_argument("--status", metavar="SHEET", help="what the sheet establishes so far")
    parser.add_argument("--run", metavar="RUN_DIR", help="run directory holding the evidence")
    args = parser.parse_args()
    if args.worksheet:
        for row in build_worksheet(Path(args.worksheet)):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.review:
        return review_worksheet(Path(args.review), Path(args.run) if args.run else None)
    if args.status:
        path = Path(args.status)
        entries = [e.row for e in load_worksheet(path) if e.row is not None]
        try:
            rated = load_ratings(path)
        except ValueError:
            rated = []
        for line in status_lines(summarize(rated, floor=len(entries))):
            print(line)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
