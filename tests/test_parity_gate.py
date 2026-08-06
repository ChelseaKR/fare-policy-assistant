"""The bilingual parity gate (M-1; audit P1-1; AIEV-10/11, I18N-22).

Three layers under test:
- the pure math (`parity_delta`, `parity_regressed`, `suites_below_macro`);
- the committed-report check (`check_parity_committed`), including the
  rendered-table fallback for reports generated before the payload carried
  `parity`;
- the real committed EVALS.md, which must hold the gate at HEAD.
"""

from __future__ import annotations

import pytest

from assistant import config
from evals import provenance, runner
from evals.check_report_regression import _parity_from_table, check_parity_committed
from evals.report import generate_markdown
from evals.runner import (
    check_mirrors,
    expected_below_macro,
    load_suites,
    mirror_problems,
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


# ── mirror integrity (the gate's own denominator) ─────────────────────────────
#
# The parity delta above is only an equity measurement if each pair really is
# one question asked in two languages. Until 2026-08-05 nothing checked that,
# and three of the 22 pairs in the promoted baseline were not pairs: one had no
# required facts at all while its mirror had to produce "DD Form 214", one had
# dropped its mirror's `65` fact, and one was scoped to MST while pointing at a
# Yolobus case. All three reported a 0.0-point gap. These tests exist so that
# class of defect fails a run instead of publishing as parity.


def _case(cid: str, **kw: object) -> dict:
    base = {
        "id": cid,
        "language": "en",
        "agency_scope": "MST",
        "expected_behavior": "answer",
        "required_facts": ["65"],
    }
    base.update(kw)
    return base


def _pair(es: dict, en: dict) -> dict[str, dict]:
    return {es["id"]: es, en["id"]: en}


def test_a_well_formed_mirror_pair_is_clean():
    cases = _pair(_case("ml-1", language="es", mirror_of="en-1"), _case("en-1"))
    assert mirror_problems(cases) == []


def test_mirror_pointing_at_a_case_that_does_not_exist_is_caught():
    cases = {"ml-1": _case("ml-1", language="es", mirror_of="ground-nope")}
    (problem,) = mirror_problems(cases)
    assert "is not a case" in problem


def test_same_language_pair_is_caught():
    # A pair that shares a language measures the model against itself; the
    # delta is structurally zero and tells a reader nothing about equity.
    cases = _pair(_case("ml-1", mirror_of="en-1"), _case("en-1"))
    assert any("measures no gap" in p for p in mirror_problems(cases))


def test_scope_mismatch_is_caught():
    # ml-022's defect: an MST question mirrored to a Yolobus case, so the pair
    # measured which corpus answered better, not which language did.
    cases = _pair(
        _case("ml-1", language="es", mirror_of="en-1"),
        _case("en-1", agency_scope="Yolobus"),
    )
    assert any("two corpora, not two languages" in p for p in mirror_problems(cases))


def test_expected_behavior_mismatch_is_caught():
    cases = _pair(
        _case("ml-1", language="es", mirror_of="en-1"),
        _case("en-1", expected_behavior="refuse_redirect"),
    )
    assert any("expects 'refuse_redirect'" in p for p in mirror_problems(cases))


def test_a_mirror_asked_to_prove_less_is_caught():
    # ml-008 and ml-011's defect. The Spanish case passes on citation,
    # language, and guard checks alone while its mirror must also produce a
    # fact — an easier case whose easier pass the parity gate reads as equity.
    cases = _pair(
        _case("ml-1", language="es", mirror_of="en-1", required_facts=[]),
        _case("en-1", required_facts=["DD Form 214"]),
    )
    assert any("asked to prove less" in p for p in mirror_problems(cases))


def test_a_mirror_asked_to_prove_more_is_allowed():
    # Only the weaker direction is a defect. Fact strings are language-specific
    # by design, so the counts are compared, never the strings themselves.
    cases = _pair(
        _case("ml-1", language="es", mirror_of="en-1", required_facts=["65", "$1.25"]),
        _case("en-1", required_facts=["65"]),
    )
    assert mirror_problems(cases) == []


def test_cases_without_a_mirror_are_not_examined():
    assert mirror_problems({"en-1": _case("en-1")}) == []


def test_every_committed_mirror_declaration_is_a_real_mirror():
    """The gate applied to the real suites. This is the merge-blocking half:
    `evals/runner.py::check_mirrors` runs the same check at the top of every
    eval run, so a pair that drifts cannot reach a published scoreboard."""
    cases = {c["id"]: c for s in load_suites() for c in s["cases"]}
    assert mirror_problems(cases) == []


def test_check_mirrors_passes_on_the_committed_suites():
    check_mirrors()  # no raise


def test_check_mirrors_refuses_to_run_an_eval_over_broken_pairs(monkeypatch, capsys):
    """The gate is wired into `_run_resolved`, before any model call. A broken
    mirror map must stop the run rather than produce a scoreboard whose parity
    row is meaningless."""
    broken = [
        {
            "cases": [
                _case("ml-1", language="es", mirror_of="en-1", required_facts=[]),
                _case("en-1", required_facts=["DD Form 214"]),
            ]
        }
    ]
    monkeypatch.setattr("evals.runner.load_suites", lambda *a, **k: broken)
    with pytest.raises(SystemExit):
        check_mirrors()
    assert "MIRROR GATE" in capsys.readouterr().err


# ── the escape hatch expires on its own ──────────────────────────────────────


def test_an_annotation_for_a_recovered_suite_is_flagged():
    """`expected_below_macro.json` says "delete the entry the moment the suite
    recovers", and nothing enforced it. A waiver left over a healthy suite is a
    live exemption sitting where the next real regression would land."""
    suites = {
        "conversation": {"pass_rate": 100.0, "passed": 10, "total": 10},
        "refusal": {"pass_rate": 100.0, "passed": 34, "total": 34},
    }
    (problem,) = runner.stale_annotations(suites, {"conversation": "an old rationale"})
    assert "no longer describes anything" in problem


def test_an_annotation_for_a_still_below_macro_suite_is_not_flagged():
    suites = {
        "conversation": {"pass_rate": 80.0, "passed": 8, "total": 10},
        "refusal": {"pass_rate": 100.0, "passed": 34, "total": 34},
        "edge_cases": {"pass_rate": 95.8, "passed": 46, "total": 48},
    }
    assert runner.stale_annotations(suites, {"conversation": "still true"}) == []


def test_an_annotation_for_a_suite_that_did_not_run_is_out_of_view_not_stale():
    """A `--suite` subset legitimately omits most suites; an annotation for one
    that did not run says nothing either way."""
    suites = {"refusal": {"pass_rate": 100.0, "passed": 34, "total": 34}}
    assert runner.stale_annotations(suites, {"conversation": "unrelated"}) == []


def test_the_committed_annotation_still_describes_the_committed_report():
    """The real repo state: `conversation` is annotated and is still below the
    macro floor, so the waiver is doing work rather than sitting idle."""
    import json

    payload = provenance.read_evals_md((config.REPO_ROOT / "EVALS.md").read_text(encoding="utf-8"))
    assert payload is not None
    notes = json.loads(
        (config.REPO_ROOT / "evals" / "expected_below_macro.json").read_text(encoding="utf-8")
    )
    notes = {k: v for k, v in notes.items() if not k.startswith("_")}
    assert runner.stale_annotations(payload["suites"], notes) == []
