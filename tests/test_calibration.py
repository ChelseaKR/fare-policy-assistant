import json

from evals.calibration import (
    LABELS_PATH,
    Label,
    _cohen_kappa,
    answer_hash,
    apply_label,
    binding_problem,
    calibrate,
    emit_label_templates,
    judge_reveal,
    load_labels,
    load_worksheet,
    review_block,
    review_worksheet,
    run_dir_from_header,
    stratified_worksheet,
)


def test_labels_load_and_cover_both_judges():
    labels = load_labels()
    assert len(labels) >= 10
    assert {lab.judge for lab in labels} == {"groundedness", "helpfulness"}


def test_committed_labels_are_bound_to_an_answer_hash():
    # Every committed label must carry the answer_sha256 that binds it to the
    # answer it graded; an unbound label can silently reuse a stale verdict.
    labels = load_labels()
    unbound = [f"{lab.case_id}/{lab.judge}" for lab in labels if not lab.answer_sha256]
    assert not unbound, f"labels missing answer_sha256: {unbound}"


def test_cohen_kappa_perfect_and_chance():
    assert _cohen_kappa([(True, True), (False, False)]) == 1.0
    # All-agree but one rater constant → kappa undefined-ish collapses to 0 or 1;
    # a mixed disagreement gives a value strictly below 1.
    assert _cohen_kappa([(True, True)] * 9 + [(False, True)]) < 1.0


def test_calibrate_matches_against_run_records():
    records = [
        {"case_id": "ground-001", "judges": [{"name": "groundedness", "passed": True}]},
        {"case_id": "ground-024", "judges": [{"name": "groundedness", "passed": False}]},
    ]
    labels = [lab for lab in load_labels() if lab.case_id in {"ground-001", "ground-024"}]
    out = calibrate(records, labels)
    assert out["n_matched"] == 2
    assert out["agreement"] == 1.0  # human agrees: 001 grounded, 024 contradicted


def test_stale_label_is_skipped_not_scored():
    # The label was written against "old answer"; the run now shows a different
    # answer (a prompt bump changed it). The label must be treated as stale —
    # skipped and counted — never scored against the new answer.
    answer = "The senior fare is $1.00 [doc:mst-fares]. As of 2026-06-12."
    labels = [Label("ground-050", "groundedness", True, answer_hash("old, different answer"))]
    records = [
        {
            "case_id": "ground-050",
            "answer": answer,
            "judges": [{"name": "groundedness", "passed": True}],
        }
    ]
    out = calibrate(records, labels)
    assert out["n_matched"] == 0
    assert out["n_stale"] == 1
    assert out["stale"] == ["ground-050/groundedness"]
    assert out["cohen_kappa"] is None  # nothing left to score


def test_bound_label_is_scored_when_answer_unchanged():
    answer = "The senior fare is $1.00 [doc:mst-fares]. As of 2026-06-12."
    labels = [Label("ground-050", "groundedness", True, answer_hash(answer))]
    records = [
        {
            "case_id": "ground-050",
            "answer": answer,
            "judges": [{"name": "groundedness", "passed": True}],
        }
    ]
    out = calibrate(records, labels)
    assert out["n_matched"] == 1
    assert out["n_stale"] == 0
    assert out["agreement"] == 1.0


def test_unbound_legacy_label_is_scored_but_reported():
    # A label with no answer_sha256 cannot be checked for staleness; it is still
    # scored (backward compatible) but surfaced so the gap is visible.
    labels = [Label("ground-050", "groundedness", True, "")]
    records = [
        {
            "case_id": "ground-050",
            "answer": "whatever",
            "judges": [{"name": "groundedness", "passed": True}],
        }
    ]
    out = calibrate(records, labels)
    assert out["n_matched"] == 1
    assert out["n_unbound"] == 1
    assert out["unbound"] == ["ground-050/groundedness"]


def test_emit_label_templates_hashes_the_run_answers(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    records = [
        {
            "case_id": "ground-001",
            "answer": "answer A",
            "judges": [
                {"name": "groundedness", "passed": True},
                {"name": "helpfulness", "passed": None},  # errored → not emitted
            ],
        },
        {
            "case_id": "refuse-001",
            "answer": "answer B",
            "judges": [{"name": "helpfulness", "passed": False}],
        },
    ]
    (run_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    rows = emit_label_templates(run_dir)
    # The errored verdict is dropped; the two scored pairs are emitted.
    assert {(r["case_id"], r["judge"]) for r in rows} == {
        ("ground-001", "groundedness"),
        ("refuse-001", "helpfulness"),
    }
    by_case = {r["case_id"]: r for r in rows}
    assert by_case["ground-001"]["answer_sha256"] == answer_hash("answer A")
    assert by_case["refuse-001"]["answer_sha256"] == answer_hash("answer B")


def test_emitted_templates_bind_to_the_run_they_came_from(tmp_path):
    # Round trip: templates emitted from a run are non-stale against that run.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    records = [
        {
            "case_id": "ground-001",
            "answer": "answer A",
            "judges": [{"name": "groundedness", "passed": True}],
        }
    ]
    (run_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    rows = emit_label_templates(run_dir)
    labels = [Label(r["case_id"], r["judge"], r["human_passed"], r["answer_sha256"]) for r in rows]
    out = calibrate(records, labels)
    assert out["n_stale"] == 0
    assert out["n_matched"] == 1


def test_validate_accepts_multiturn_and_rejects_too_short():
    import pytest

    from evals.runner import validate_cases

    ok = [
        {
            "cases": [
                {"id": "c1", "turns": ["a?", "b?"], "expected_behavior": "answer", "rationale": "x"}
            ]
        }
    ]
    validate_cases(ok)  # no raise
    bad = [
        {
            "cases": [
                {
                    "id": "c2",
                    "turns": ["only one?"],
                    "expected_behavior": "answer",
                    "rationale": "x",
                }
            ]
        }
    ]
    with pytest.raises(SystemExit):
        validate_cases(bad)
    missing = [{"cases": [{"id": "c3", "expected_behavior": "answer", "rationale": "x"}]}]
    with pytest.raises(SystemExit):
        validate_cases(missing)


# ── the sample must be able to say something ─────────────────────────────────
#
# On 2026-07-12 EVALS.md published "Cohen's κ: 1.000" from four labels that all
# agreed. Both labels in the set that had recorded a human/judge *disagreement*
# (ml-004, ground-024) had gone stale when a prompt bump changed their answers,
# so the surviving sample was the agreeing half and κ was 1.0 by the pe==1
# special case — a definition, not a measurement. These tests keep that number
# from being publishable again without the reader being told.


def test_kappa_is_undefined_when_no_scored_label_disagrees():
    assert _cohen_kappa([(True, True)] * 4) is None
    assert _cohen_kappa([(False, False)] * 4) is None


def test_kappa_is_still_a_number_when_the_sample_can_disagree():
    # Guard against over-correcting: a sample with both verdicts present and
    # full agreement is a real κ of 1.0, not a degenerate one.
    assert _cohen_kappa([(True, True), (False, False)]) == 1.0


def test_calibrate_reports_the_floor_and_the_shortfall(tmp_path):
    records = [
        {
            "case_id": f"c-{i}",
            "answer": f"a-{i}",
            "judges": [{"name": "groundedness", "passed": True}],
        }
        for i in range(200)
    ]
    labels = [Label("c-0", "groundedness", True, answer_hash("a-0"))]
    out = calibrate(records, labels)
    assert out["n_judged"] == 200
    assert out["floor"] == 20  # CLAUDE.md's 10% sample, over the judged pairs
    assert out["meets_floor"] is False
    assert out["n_disagreements"] == 0


def test_calibrate_counts_the_disagreements_the_sample_actually_contains():
    records = [
        {"case_id": "c-0", "answer": "a", "judges": [{"name": "groundedness", "passed": True}]},
        {"case_id": "c-1", "answer": "b", "judges": [{"name": "groundedness", "passed": True}]},
    ]
    labels = [
        Label("c-0", "groundedness", True, answer_hash("a")),
        Label("c-1", "groundedness", False, answer_hash("b")),
    ]
    out = calibrate(records, labels)
    assert out["n_disagreements"] == 1
    assert out["kappa_defined"] is True


# ── a template is not a human verdict ────────────────────────────────────────


def test_emitted_templates_carry_no_verdict(tmp_path):
    """`--emit` used to pre-fill `human_passed` with the judge's own call. A
    relabeling pass that accepted the defaults would then grade the judge
    against itself and report perfect agreement while measuring nothing."""
    (tmp_path / "results.jsonl").write_text(
        json.dumps(
            {
                "case_id": "c-1",
                "answer": "answer text",
                "judges": [{"name": "groundedness", "passed": True}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (row,) = emit_label_templates(tmp_path)
    assert row["human_passed"] is None
    assert row["note"].startswith("TEMPLATE")


def test_an_unedited_template_row_cannot_be_loaded_as_a_label(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "c-1",
                "judge": "groundedness",
                "human_passed": None,
                "answer_sha256": "x",
                "note": "TEMPLATE — read the answer and its passages",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        load_labels(path)
    except ValueError as exc:
        assert "TEMPLATE" in str(exc)
    else:  # pragma: no cover - the assertion below is the failure message
        raise AssertionError("an unfilled template must not load as a human label")


def test_a_row_with_a_non_boolean_verdict_is_refused(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        json.dumps(
            {"case_id": "c-1", "judge": "groundedness", "human_passed": None, "note": "reviewed"}
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        load_labels(path)
    except ValueError as exc:
        assert "not a verdict" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unreviewed pair must not load as a human label")


# ── the worksheet must be able to disagree ───────────────────────────────────


def _run_with(tmp_path, rows):
    (tmp_path / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_worksheet_takes_every_judge_failure_first(tmp_path):
    """The region where the judge objected is where a human is most likely to
    differ. The committed sample missed it: 14 of 16 labels sat on pairs the
    judge had passed, and the four that survived staleness were all
    agreements."""
    rows = [
        {
            "case_id": f"c-{i}",
            "suite": "groundedness",
            "answer": f"a-{i}",
            "judges": [{"name": "groundedness", "passed": i != 7}],
        }
        for i in range(20)
    ]
    sheet = stratified_worksheet(_run_with(tmp_path, rows), size=3)
    assert sheet[0]["case_id"] == "c-7" and sheet[0]["judge_said"] is False


def test_worksheet_spreads_across_suites_rather_than_draining_the_largest(tmp_path):
    rows = [
        {
            "case_id": f"big-{i}",
            "suite": "edge_cases",
            "answer": f"a{i}",
            "judges": [{"name": "groundedness", "passed": True}],
        }
        for i in range(20)
    ] + [
        {
            "case_id": "small-0",
            "suite": "refusal",
            "answer": "b",
            "judges": [{"name": "helpfulness", "passed": True}],
        }
    ]
    sheet = stratified_worksheet(_run_with(tmp_path, rows), size=2)
    assert {r["suite"] for r in sheet} == {"edge_cases", "refusal"}


def test_worksheet_rows_carry_no_verdict_and_are_bound_to_the_answer(tmp_path):
    rows = [
        {
            "case_id": "c-0",
            "suite": "groundedness",
            "answer": "the answer",
            "judges": [{"name": "groundedness", "passed": True}],
        }
    ]
    (row,) = stratified_worksheet(_run_with(tmp_path, rows), size=1)
    assert row["human_passed"] is None
    assert row["answer_sha256"] == answer_hash("the answer")


def test_committed_worksheet_is_floor_sized_and_entirely_unlabeled():
    """The worksheet is queued human work, not evidence. If a row in it ever
    reads as labeled, something has filled in verdicts nobody made."""
    path = LABELS_PATH.parent / "judge_relabel_worksheet_2026-08-05.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(rows) == 37
    assert all(r["human_passed"] is None for r in rows)
    assert sum(1 for r in rows if r["judge_said"] is False) == 9


# ── --review: the labeling surface ───────────────────────────────────────────
#
# Every test below is about something the reviewer tool must refuse to do. The
# worksheet exists because the previous sample could not disagree with the
# judge; a labeling surface that nudges, defaults, or mislabels would rebuild
# that problem behind a nicer interface.


JUDGE_REASONING = "the passage says $3.00, the answer says $2.00"


def _review_fixture(tmp_path, *, answer="Woodland is $2.00 [doc:yolobus-fares].", rows=None):
    """A one-case run directory and a two-row worksheet bound to its answer."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = {
        "case_id": "ground-024",
        "suite": "groundedness",
        "question": "How much does a BeeLine ride in Woodland cost?",
        "rationale": "yolobus-fares BeeLine table: Woodland regular $3.00.",
        "expected_behavior": "answer",
        "kind": "answered",
        "answer": answer,
        "passages": [
            {
                "chunk_id": "yolobus-fares#2",
                "section": "BeeLine On-Demand Transit Fares",
                "score": 19.41,
                "text": "Woodland | $3.00 | $1.50",
            }
        ],
        "judges": [
            {"name": "groundedness", "passed": False, "detail": JUDGE_REASONING},
            {"name": "helpfulness", "passed": True, "detail": "score=4 — clear enough"},
        ],
    }
    (run_dir / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    sheet = tmp_path / "worksheet.jsonl"
    rows = (
        rows
        if rows is not None
        else [
            {
                "case_id": "ground-024",
                "judge": judge,
                "human_passed": None,
                "answer_sha256": answer_hash(answer),
                "judge_said": judge == "helpfulness",
                "suite": "groundedness",
                "note": "TEMPLATE — read the answer and its passages, then set human_passed",
            }
            for judge in ("groundedness", "helpfulness")
        ]
    )
    sheet.write_text(
        "# generated from evals/runs/20260712T050117Z\n"
        + "".join(json.dumps(r) + "\n" for r in rows),
        encoding="utf-8",
    )
    return sheet, run_dir, record


def _scripted(replies):
    """An input() stand-in that raises once the script runs out, so a test can
    never accidentally pass by the tool asking one fewer question than expected."""
    it = iter(replies)

    def ask(_prompt):
        return next(it)

    return ask


def _rows_of(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_review_has_no_default_verdict_and_enter_does_not_resolve_one(tmp_path):
    """Pressing Enter must re-ask, not resolve. A prompt with a default is a
    proposed verdict, which is the circularity `--emit` used to have."""
    sheet, run_dir, _ = _review_fixture(tmp_path)
    printed = []
    code = review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["", "  ", "yes", "quit"]),
        out=printed.append,
    )
    assert code == 0
    assert all(r["human_passed"] is None for r in _rows_of(sheet))
    assert sum("There is no default" in line for line in printed) == 3


def test_review_withholds_the_judge_verdict_until_the_human_has_answered(tmp_path):
    """The evidence block must not leak what the judge decided; the reveal comes
    after. Order is the whole design: a reviewer shown the verdict first
    confirms it, and a sample that only confirms cannot calibrate anything."""
    sheet, run_dir, record = _review_fixture(tmp_path)
    row = _rows_of(sheet)[0]
    block = review_block(row, record)
    assert JUDGE_REASONING not in block
    assert "FAIL" not in block and "judge said" not in block
    assert "Woodland | $3.00" in block  # the passages the judge graded against
    assert "How much does a BeeLine ride" in block
    assert "auditing a transit fare-policy assistant for groundedness" in block
    reveal = judge_reveal(row, record)
    assert "FAIL" in reveal and JUDGE_REASONING in reveal


def test_review_reveal_order_holds_end_to_end(tmp_path):
    sheet, run_dir, _ = _review_fixture(tmp_path)
    printed = []
    review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["fail", "the $2.00 figure is not in the passages", "quit"]),
        out=printed.append,
    )
    transcript = "\n".join(printed)
    assert transcript.index("the assistant's answer") < transcript.index(JUDGE_REASONING)


def test_review_refuses_a_row_whose_answer_moved(tmp_path):
    """A verdict pinned to a different answer than the reviewer read is worse
    than a blank, so the binding is checked before any evidence is shown."""
    sheet, run_dir, record = _review_fixture(tmp_path)
    rows = _rows_of(sheet)
    rows[0]["answer_sha256"] = "0" * 64
    sheet.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    assert binding_problem(rows[0], record) is not None
    assert binding_problem(rows[1], record) is None
    assert "not in this run" in binding_problem({"case_id": "nope"}, None)

    printed = []
    review_worksheet(sheet, run_dir, input_fn=_scripted(["quit"]), out=printed.append)
    assert any("SKIPPED" in line and "answer changed" in line for line in printed)
    assert _rows_of(sheet)[0]["human_passed"] is None


def test_review_records_the_verdict_with_a_reason_and_reopens_where_it_stopped(tmp_path):
    sheet, run_dir, _ = _review_fixture(tmp_path)
    review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["fail", "the $2.00 figure is not in the passages", "quit"]),
        out=lambda _line: None,
    )
    first, second = _rows_of(sheet)
    assert first["human_passed"] is False
    assert first["note"] == "the $2.00 figure is not in the passages"
    assert not first["note"].startswith("TEMPLATE")
    assert second["human_passed"] is None  # untouched

    printed = []
    review_worksheet(sheet, run_dir, input_fn=_scripted(["quit"]), out=printed.append)
    assert "2 rows, 1 labeled, 1 to go" in printed[0]


def test_review_will_not_record_a_verdict_without_a_written_reason(tmp_path):
    sheet, run_dir, _ = _review_fixture(tmp_path)
    review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["fail", "", "  ", "quit"]),
        out=lambda _line: None,
    )
    # Quitting at the reason prompt discards the verdict rather than storing a
    # labeled row nobody justified.
    assert all(r["human_passed"] is None for r in _rows_of(sheet))


def test_apply_label_requires_a_bool_and_a_reason():
    row = {"case_id": "c", "judge": "groundedness", "human_passed": None, "note": "TEMPLATE — x"}
    assert apply_label(row, True, "reads fine against the passage")["human_passed"] is True
    for bad in ("", "   "):
        try:
            apply_label(row, True, bad)
        except ValueError as exc:
            assert "written reason" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("a label without a reason must not be recorded")


def test_write_back_preserves_comments_unknown_fields_and_row_order(tmp_path):
    sheet, run_dir, _ = _review_fixture(tmp_path)
    rows = _rows_of(sheet)
    rows[1]["reviewer_only_field"] = "keep me"
    sheet.write_text(
        "# header line one\n# header line two\n" + "".join(json.dumps(r) + "\n" for r in rows),
        encoding="utf-8",
    )
    review_worksheet(
        sheet,
        run_dir,
        input_fn=_scripted(["pass", "the price matches the passage", "quit"]),
        out=lambda _line: None,
    )
    text = sheet.read_text(encoding="utf-8")
    assert text.startswith("# header line one\n# header line two\n")
    after = _rows_of(sheet)
    assert [r["judge"] for r in after] == ["groundedness", "helpfulness"]
    assert after[1]["reviewer_only_field"] == "keep me"


def test_review_finds_its_run_directory_from_the_worksheet_header(tmp_path):
    sheet, _run_dir, _ = _review_fixture(tmp_path)
    entries = load_worksheet(sheet)
    found = run_dir_from_header(entries)
    assert found is not None and found.name == "20260712T050117Z"
    assert run_dir_from_header([e for e in entries if e.row is not None]) is None


def test_review_labels_a_helpfulness_row_and_marks_passages_as_context(tmp_path):
    """The helpfulness judge never receives the passages. They are still shown,
    flagged as context, so the asymmetry is on the page instead of implied."""
    sheet, run_dir, record = _review_fixture(tmp_path)
    row = [r for r in _rows_of(sheet) if r["judge"] == "helpfulness"][0]
    block = review_block(row, record)
    assert "CONTEXT ONLY" in block
    assert "expected_behavior: answer" in block
    assert "score=4" not in block
