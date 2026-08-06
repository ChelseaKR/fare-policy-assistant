import json

from evals.calibration import (
    LABELS_PATH,
    Label,
    _cohen_kappa,
    answer_hash,
    calibrate,
    emit_label_templates,
    load_labels,
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
