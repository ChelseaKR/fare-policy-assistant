"""Native-Spanish answer quality (docs/I18N.md §7, the half parity cannot see).

The bilingual parity gate compares a Spanish answer's pass/fail against its
English mirror's, and both verdicts come from checks that ask whether a citation
resolves and a required fact appears. A 0.0-point delta is therefore consistent
with Spanish of any quality. These tests hold the line on the two ways that
could be papered over: rating rows nobody rated, and printing an unmeasured
property as a number.
"""

from __future__ import annotations

import json

import pytest

from evals import report, spanish_quality
from evals.calibration import answer_hash


def _run(tmp_path, records):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return run_dir


def _record(case_id, answer, *, kind="answered", language="es", suite="multilingual"):
    return {
        "case_id": case_id,
        "suite": suite,
        "kind": kind,
        "language": language,
        "question": "¿Cuánto cuesta?",
        "answer": answer,
    }


def _rated(**kw):
    row = {
        "case_id": "ml-001",
        "suite": "multilingual",
        "kind": "answered",
        "fixed_string": False,
        "question_source": "repo_mirror",
        "answer_sha256": answer_hash("x"),
        "fluent": True,
        "register": True,
        "terminology": True,
        "note": "reads naturally",
    }
    row.update(kw)
    return row


# ── the committed sheet ──────────────────────────────────────────────────────


def test_committed_sheet_is_a_census_of_every_spanish_answer_and_entirely_unrated():
    """Queued human work, not evidence. If a row ever reads as rated, something
    filled in a judgement nobody made."""
    rows = [
        json.loads(line)
        for line in spanish_quality.SHEET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(rows) == 28
    for dimension in spanish_quality.RUBRIC:
        assert all(r[dimension] is None for r in rows), f"{dimension} has a filled-in rating"
    assert all(r["note"].startswith("TEMPLATE") for r in rows)
    # The seven gettext refusal strings are named as such: rating them describes
    # the committed catalog, not the model.
    assert sum(1 for r in rows if r["fixed_string"]) == 7
    # And §7's externally sourced question set is still absent, as a field.
    assert {r["question_source"] for r in rows} == {"repo_mirror"}


def test_the_committed_sheet_refuses_to_load_as_ratings():
    with pytest.raises(ValueError) as exc:
        spanish_quality.load_ratings()
    assert "TEMPLATE" in str(exc.value)


# ── the sheet cannot be pre-filled ───────────────────────────────────────────


def test_generated_rows_carry_no_rating_and_are_bound_to_the_answer(tmp_path):
    run_dir = _run(tmp_path, [_record("ml-001", "El pase cuesta $35.00.")])
    (row,) = spanish_quality.build_worksheet(run_dir)
    for dimension in spanish_quality.RUBRIC:
        assert row[dimension] is None
    assert row["answer_sha256"] == answer_hash("El pase cuesta $35.00.")


def test_english_answers_are_not_in_the_census(tmp_path):
    run_dir = _run(
        tmp_path,
        [
            _record("ml-001", "El pase cuesta $35.00."),
            _record("g-1", "The pass is $35.00.", language="en"),
        ],
    )
    assert [r["case_id"] for r in spanish_quality.build_worksheet(run_dir)] == ["ml-001"]


def test_an_unrated_dimension_is_refused_rather_than_skipped(tmp_path):
    sheet = tmp_path / "s.jsonl"
    sheet.write_text(json.dumps(_rated(fluent=None, note="looked at it")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        spanish_quality.load_ratings(sheet)
    assert "not a verdict" in str(exc.value)


def test_an_unknown_question_source_is_refused(tmp_path):
    sheet = tmp_path / "s.jsonl"
    sheet.write_text(json.dumps(_rated(question_source="made up")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        spanish_quality.load_ratings(sheet)


# ── an absence is never rendered as a zero ───────────────────────────────────


def test_an_unrated_sheet_reports_unmeasured_with_no_rates():
    summary = spanish_quality.summarize([], floor=28)
    assert summary["measured"] is False
    assert summary["shortfall"] == 28
    assert all(v is None for v in summary["rates"].values())
    text = " ".join(spanish_quality.status_lines(summary))
    assert "Not measured" in text
    assert "0%" not in text and "0.0%" not in text


def test_a_partial_census_is_unmeasured_rather_than_a_percentage():
    """Half a census read as a percentage is a claim about answers nobody looked
    at. Below the floor there is no rate, in either direction."""
    summary = spanish_quality.summarize([_rated(case_id=f"ml-{i}") for i in range(14)], floor=28)
    assert summary["measured"] is False
    assert all(v is None for v in summary["rates"].values())


def test_a_complete_census_reports_rates_over_the_model_written_answers_only():
    rows = [_rated(case_id=f"ml-{i}") for i in range(3)]
    rows.append(_rated(case_id="parity-pii-dob-es", fixed_string=True, fluent=False))
    summary = spanish_quality.summarize(rows, floor=4)
    assert summary["measured"] is True
    assert summary["model_written"] == 3 and summary["fixed_string"] == 1
    # The catalog string's `fluent: false` must not move the model's number.
    assert summary["rates"]["fluent"] == 100.0
    assert "externally sourced benchmark is still outstanding" in " ".join(
        spanish_quality.status_lines(summary)
    )


def test_evals_md_carries_the_unmeasured_section():
    """The gap is on the published page, not only in a doc nobody opens."""
    from assistant import config

    text = (config.REPO_ROOT / "EVALS.md").read_text(encoding="utf-8")
    assert "## Native-Spanish answer quality" in text
    assert "**Not measured.**" in text


def test_report_section_reads_the_committed_sheet_as_unmeasured():
    section = report._spanish_quality_section()
    assert section is not None
    assert "Not measured" in section
    assert "0 of 28 Spanish answers rated" in section


# ── the reviewer proposes nothing ────────────────────────────────────────────


def _scripted(replies):
    it = iter(replies)

    def ask(_prompt):
        return next(it)

    return ask


def _sheet_for(tmp_path, run_dir, rows):
    sheet = tmp_path / "sheet.jsonl"
    sheet.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return sheet


def test_review_has_no_default_rating(tmp_path):
    run_dir = _run(tmp_path, [_record("ml-001", "El pase cuesta $35.00.")])
    sheet = _sheet_for(tmp_path, run_dir, spanish_quality.build_worksheet(run_dir))
    printed: list[str] = []
    spanish_quality.review_worksheet(
        sheet, run_dir, input_fn=_scripted(["", "sí", "quit"]), out=printed.append
    )
    rows = [json.loads(line) for line in sheet.read_text(encoding="utf-8").splitlines()]
    assert all(r["fluent"] is None for r in rows)
    assert sum("There is no default" in line for line in printed) == 2


def test_review_records_every_dimension_and_a_note(tmp_path):
    run_dir = _run(tmp_path, [_record("ml-001", "El pase cuesta $35.00.")])
    sheet = _sheet_for(tmp_path, run_dir, spanish_quality.build_worksheet(run_dir))
    spanish_quality.review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["yes", "yes", "no", "anglicismo: dice 'pass' en vez de 'pase'"]),
        out=lambda _line: None,
    )
    (row,) = [json.loads(line) for line in sheet.read_text(encoding="utf-8").splitlines()]
    assert (row["fluent"], row["register"], row["terminology"]) == (True, True, False)
    assert row["note"] == "anglicismo: dice 'pass' en vez de 'pase'"


def test_review_refuses_a_row_whose_answer_moved(tmp_path):
    run_dir = _run(tmp_path, [_record("ml-001", "El pase cuesta $35.00.")])
    rows = spanish_quality.build_worksheet(run_dir)
    rows[0]["answer_sha256"] = "0" * 64
    sheet = _sheet_for(tmp_path, run_dir, rows)
    printed: list[str] = []
    spanish_quality.review_worksheet(sheet, run_dir, input_fn=_scripted([]), out=printed.append)
    assert any("SKIPPED" in line for line in printed)
    assert json.loads(sheet.read_text(encoding="utf-8").splitlines()[0])["fluent"] is None


def test_review_reopens_where_it_stopped(tmp_path):
    run_dir = _run(
        tmp_path,
        [_record("ml-001", "El pase cuesta $35.00."), _record("ml-002", "La tarifa es $2.00.")],
    )
    sheet = _sheet_for(tmp_path, run_dir, spanish_quality.build_worksheet(run_dir))
    spanish_quality.review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["yes", "yes", "yes", "bien", "quit"]),
        out=lambda _l: None,
    )
    printed: list[str] = []
    spanish_quality.review_worksheet(
        sheet, run_dir, input_fn=_scripted(["quit"]), out=printed.append
    )
    assert "2 Spanish answers, 1 rated, 1 to go" in printed[0]
