"""The bilingual parity gate (M-1; audit P1-1; AIEV-10/11, I18N-22).

Three layers under test:
- the pure math (`parity_delta`, `parity_regressed`, `suites_below_macro`);
- the committed-report check (`check_parity_committed`), including the
  rendered-table fallback for reports generated before the payload carried
  `parity`;
- the real committed EVALS.md, which must hold the gate at HEAD.
"""

from __future__ import annotations

from assistant import config
from evals import provenance
from evals.check_report_regression import _parity_from_table, check_parity_committed
from evals.report import generate_markdown
from evals.runner import (
    expected_below_macro,
    parity_delta,
    parity_problems,
    parity_regressed,
    suites_below_macro,
)


def _rec(case_id: str, suite: str, passed: bool, mirror_of: str | None = None) -> dict:
    return {
        "case_id": case_id,
        "suite": suite,
        "passed": passed,
        "mirror_of": mirror_of,
        "question": "q",
        "rationale": "r",
        "answer": "a [doc:mst-fares]",
        "kind": "answered",
        "passages": [],
        "checks": [],
        "judges": [],
    }


def _records(es_pass: list[bool], en_pass: list[bool]) -> list[dict]:
    out = []
    for i, (es, en) in enumerate(zip(es_pass, en_pass, strict=True)):
        out.append(_rec(f"ground-{i:03d}", "groundedness", en))
        out.append(_rec(f"ml-{i:03d}", "multilingual", es, mirror_of=f"ground-{i:03d}"))
    return out


# ── parity_delta ─────────────────────────────────────────────────────────────


def test_parity_delta_counts_pairs_and_signs_the_gap_toward_spanish():
    records = _records(es_pass=[True, False, False, True], en_pass=[True, True, True, True])
    d = parity_delta(records)
    assert d == {
        "suite": "multilingual",
        "pairs": 4,
        "passed": 2,
        "mirror_passed": 4,
        "delta_pp": 50.0,
    }


def test_parity_delta_skips_pairs_whose_mirror_is_absent():
    records = _records(es_pass=[True], en_pass=[True])
    records.append(_rec("ml-999", "multilingual", False, mirror_of="ground-999"))  # no mirror
    d = parity_delta(records)
    assert d is not None and d["pairs"] == 1 and d["delta_pp"] == 0.0


def test_parity_delta_none_when_no_complete_pair():
    assert parity_delta([_rec("ground-001", "groundedness", True)]) is None
    assert parity_delta([]) is None


def test_parity_delta_negative_when_spanish_outperforms():
    records = _records(es_pass=[True, True, True, True], en_pass=[True, False, False, True])
    d = parity_delta(records)
    assert d is not None and d["delta_pp"] == -50.0


# ── parity_regressed ─────────────────────────────────────────────────────────


def test_one_flipped_pair_out_of_22_is_noise_not_regression():
    # 1 case = 4.5 pp on 22 pairs: under both the 5-point and the 2-case bar.
    d = parity_delta(_records(es_pass=[False] + [True] * 21, en_pass=[True] * 22))
    assert d is not None and d["delta_pp"] == 4.5
    assert not parity_regressed(d)


def test_two_case_gap_over_five_points_regresses():
    d = parity_delta(_records(es_pass=[False, False] + [True] * 20, en_pass=[True] * 22))
    assert d is not None and d["delta_pp"] == 9.1
    assert parity_regressed(d)


def test_wide_percentage_gap_on_one_case_respects_the_case_floor():
    # 1 of 4 pairs = 25 pp but only one diverging case: judge noise on a
    # small subset (e.g. smoke), same rationale as suite_regressed.
    d = parity_delta(_records(es_pass=[False, True, True, True], en_pass=[True] * 4))
    assert d is not None and d["delta_pp"] == 25.0
    assert not parity_regressed(d)


def test_spanish_outperforming_never_regresses():
    d = parity_delta(_records(es_pass=[True] * 4, en_pass=[False, False, True, True]))
    assert d is not None and not parity_regressed(d)


# ── suites_below_macro ───────────────────────────────────────────────────────


def _suite(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "pass_rate": round(100 * passed / total, 1)}


def test_suite_far_below_macro_is_an_offender():
    suites = {
        "refusal": _suite(30, 30),
        "groundedness": _suite(29, 30),
        "conversation": _suite(6, 10),  # 60% vs macro ~85.4%
    }
    offenders = suites_below_macro(suites)
    assert list(offenders) == ["conversation"]
    assert offenders["conversation"]["pass_rate"] == 60.0


def test_suites_within_five_points_of_macro_are_clean():
    suites = {"refusal": _suite(29, 30), "groundedness": _suite(28, 30)}
    assert suites_below_macro(suites) == {}


def test_stretch_suites_neither_gate_nor_shift_the_macro():
    # P3-3's promise: a stretch language's score never fails a build — and it
    # must not drag the macro down for the gated suites either.
    suites = {
        "refusal": _suite(30, 30),
        "groundedness": _suite(30, 30),
        "stretch_tagalog": _suite(3, 10),
    }
    assert suites_below_macro(suites) == {}


# ── parity_problems (the run-time gate's findings) ───────────────────────────


def test_clean_run_has_no_parity_problems():
    records = _records(es_pass=[True] * 3, en_pass=[True] * 3)
    suites = {"multilingual": _suite(3, 3), "groundedness": _suite(3, 3)}
    assert parity_problems(records, suites, annotations={}) == []


def test_parity_gap_is_reported():
    records = _records(es_pass=[False, False] + [True] * 20, en_pass=[True] * 22)
    problems = parity_problems(records, {"multilingual": _suite(20, 22)}, annotations={})
    assert len(problems) == 1
    assert "Spanish parity" in problems[0] and "9.1 pp" in problems[0]


def test_below_macro_suite_needs_a_written_annotation():
    records = _records(es_pass=[True], en_pass=[True])
    suites = {
        "refusal": _suite(30, 30),
        "groundedness": _suite(29, 30),
        "conversation": _suite(6, 10),
    }
    flagged = parity_problems(records, suites, annotations={})
    assert len(flagged) == 1 and "conversation" in flagged[0]
    annotated = parity_problems(
        records, suites, annotations={"conversation": "documented honest failures"}
    )
    assert annotated == []


# ── committed-report check: payload path and table fallback ─────────────────


def _md_with_payload(payload: dict) -> str:
    return "# report\n" + provenance.render_evals_md_block(payload)


def test_committed_payload_parity_gap_is_flagged():
    md = _md_with_payload(
        {
            "suites": {"multilingual": _suite(20, 22)},
            "parity": {
                "suite": "multilingual",
                "pairs": 22,
                "passed": 20,
                "mirror_passed": 22,
                "delta_pp": 9.1,
            },
        }
    )
    problems = check_parity_committed(md, annotations={})
    assert len(problems) == 1 and "gap 9.1 pp" in problems[0]


def test_committed_report_without_pairs_skips_delta_but_keeps_macro_form():
    md = _md_with_payload(
        {
            "suites": {
                "refusal": _suite(30, 30),
                "groundedness": _suite(29, 30),
                "conversation": _suite(6, 10),
            }
        }
    )
    problems = check_parity_committed(md, annotations={})
    assert len(problems) == 1 and "conversation" in problems[0]
    assert check_parity_committed(md, annotations={"conversation": "rationale"}) == []


def test_table_fallback_parses_the_rendered_spanish_parity_section():
    md = "\n".join(
        [
            "## Spanish parity",
            "",
            "| Spanish case | passed | English mirror | passed |",
            "|---|---|---|---|",
            "| ml-001 | ✓ | ground-001 | ✓ |",
            "| ml-002 | ✗ | ground-002 | ✓ |",
            "| ml-003 | ✗ | ground-003 | ✓ |",
            "| ml-004 | ✗ | — | — |",
            "",
            "## Next section",
        ]
    )
    d = _parity_from_table(md)
    assert d == {
        "suite": "multilingual",
        "pairs": 3,
        "passed": 1,
        "mirror_passed": 3,
        "delta_pp": 66.7,
    }


def test_table_fallback_none_without_a_parity_section():
    assert _parity_from_table("# report\nno tables here") is None


def test_pre_payload_report_with_table_gap_is_flagged_via_fallback():
    rows = ["| Spanish case | passed | English mirror | passed |", "|---|---|---|---|"]
    rows += [f"| ml-{i:03d} | {'✗' if i < 2 else '✓'} | ground-{i:03d} | ✓ |" for i in range(22)]
    md = "## Spanish parity\n\n" + "\n".join(rows) + "\n"
    problems = check_parity_committed(md, annotations={})
    assert len(problems) == 1 and "20/22" in problems[0]


# ── the real committed artifacts hold the gate at HEAD ───────────────────────


def test_committed_evals_md_holds_the_parity_gate():
    md = (config.REPO_ROOT / "EVALS.md").read_text(encoding="utf-8")
    assert check_parity_committed(md) == []


def test_committed_annotations_file_is_wellformed_and_rationales_are_written():
    notes = expected_below_macro()
    for suite, rationale in notes.items():
        assert isinstance(rationale, str) and len(rationale) > 40, (
            f"{suite}: an annotation is a written rationale, not a flag"
        )


# ── report rendering ─────────────────────────────────────────────────────────


def test_report_renders_the_delta_line_and_embeds_parity():
    records = _records(es_pass=[True, True], en_pass=[True, True])
    summary = {
        "run_at": "2026-07-17T00:00:00+00:00",
        "mode": "full",
        "offline": True,
        "judges_ran": False,
        "answer_model": "mock",
        "judge_model": "mock",
        "prompt_versions": {"system": "v1 2026-06-11"},
        "duration_seconds": 1.0,
        "suites": {
            "multilingual": _suite(2, 2),
            "groundedness": _suite(2, 2),
        },
        "total": {"passed": 4, "total": 4},
    }
    md = generate_markdown(summary, records)
    assert "Parity delta: Spanish 2/2 vs mirrored English 2/2 → 0.0 pp." in md
    payload = provenance.read_evals_md(md)
    assert payload is not None and payload["parity"]["pairs"] == 2


def test_report_prints_the_below_macro_annotation():
    records = _records(es_pass=[True], en_pass=[True])
    summary = {
        "run_at": "2026-07-17T00:00:00+00:00",
        "mode": "full",
        "offline": True,
        "judges_ran": False,
        "answer_model": "mock",
        "judge_model": "mock",
        "prompt_versions": {"system": "v1 2026-06-11"},
        "duration_seconds": 1.0,
        "suites": {
            "refusal": _suite(30, 30),
            "groundedness": _suite(29, 30),
            "conversation": _suite(6, 10),
        },
        "total": {"passed": 65, "total": 70},
    }
    md = generate_markdown(summary, records)
    assert "**Below-macro suite:** conversation at 60.0%" in md
    # conversation is annotated in the committed evals/expected_below_macro.json,
    # so the rendered line carries the rationale, not the UNANNOTATED flag.
    assert "annotated expected-below-macro" in md
