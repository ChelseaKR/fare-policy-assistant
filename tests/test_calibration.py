import json

from evals.calibration import (
    Label,
    _cohen_kappa,
    answer_hash,
    calibrate,
    emit_label_templates,
    load_labels,
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
