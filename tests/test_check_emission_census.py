"""The grader's own two numbers: what each check examined, and what it could have.

`evals.checks.run_checks` mixes two conventions. Some checks apply to every
case and are appended unconditionally. Others are *case-conditional*: their
verdict is derived from a list the case declares, and over an empty list the
verdict is vacuously true. `not []` is `True`, and a `CheckResult(..., True)`
built that way is byte-identical to one produced by a check that read a real
list and found nothing wrong.

Until 2026-09-08 `forbidden_content_absent` was appended unconditionally while
`required_facts_present`, four lines further down the same function, was
omitted when the case declared no facts. Measured over the committed suites as
`evals.runner.load_suites` assembles them (385 cases, sensitivity pairs
flattened into their variants): `forbidden_content_absent` was recorded on
**385 of 385** cases while only **58** declare any forbidden content. The other
327 rows were a pass over an empty input.

This module pins the convention rather than the fix: a case-conditional check
is emitted exactly where the case declares its input, so its *absence* from a
case's check list is the legible record that there was nothing to examine. It
also pins both floors, because a conditional that is never taken and a
conditional that is always taken are both indistinguishable from no conditional
at all.
"""

from __future__ import annotations

import collections

import pytest

from assistant.answer import AnswerResult, Citation
from evals.checks import run_checks
from evals.runner import load_suites

DOC_IDS = {"mst-fares", "yolobus-fares"}

# Every check-conditional branch in `run_checks` is decided by the *case* and by
# `result.kind`, never by the wording of the answer, so one stub answer is
# enough to take the census. It is deliberately a plain grounded answer: an
# answer that failed a check would still be counted, since the census reads
# which checks were emitted and not how they voted.
_STUB_ANSWER = (
    "Based on policies published as of 2026-06-12, the Regular Fixed Route "
    "Single Ride fare is $2.00 [doc:mst-fares]."
)


def _stub(case: dict) -> AnswerResult:
    return AnswerResult(
        question=case.get("question", "q"),
        answer=_STUB_ANSWER,
        kind="answered",
        as_of_date="2026-06-12",
        citations=[
            Citation(
                doc_id="mst-fares",
                agency="MST",
                title="Fares",
                url="https://mst.org/fares/",
                fetch_date="2026-06-12",
            )
        ],
    )


@pytest.fixture(scope="module")
def census() -> tuple[list[dict], dict[str, list[str]]]:
    """(every committed case, check name -> the case ids that check was emitted on)."""
    cases = [case for suite in load_suites() for case in suite["cases"]]
    emitted: dict[str, list[str]] = collections.defaultdict(list)
    for case in cases:
        for result in run_checks(case, _stub(case), DOC_IDS):
            emitted[result.name].append(case["id"])
    return cases, dict(emitted)


# `check name -> the case field whose declaration is the check's whole input`.
# A check listed here must be emitted on exactly the cases that declare that
# field. Adding a case-conditional check without an entry here is not caught by
# this table; `test_no_check_is_recorded_over_an_empty_declared_list` below is
# the guard that does not need the table.
CASE_CONDITIONAL: dict[str, str] = {
    "forbidden_content_absent": "forbidden_content",
    "required_facts_present": "required_facts",
}


@pytest.mark.parametrize(("check", "field"), sorted(CASE_CONDITIONAL.items()))
def test_a_case_conditional_check_is_emitted_exactly_where_its_input_exists(
    census, check: str, field: str
) -> None:
    cases, emitted = census
    declared = {case["id"] for case in cases if case.get(field)}
    recorded = set(emitted.get(check, []))

    over = sorted(recorded - declared)
    assert not over, (
        f"{check} was recorded on {len(over)} case(s) declaring no `{field}`, "
        f"e.g. {over[:5]}. Over an empty list the verdict is vacuously true, so "
        "each of those rows is a pass having examined nothing. Omit the check "
        "instead, the way the other case-conditional check in run_checks does."
    )

    # The other direction is a real gap rather than a false pass, but it is the
    # same accounting: a declared input nothing read.
    under = sorted(
        case_id
        for case_id in declared - recorded
        # `required_facts_present` sits inside the answer/partial branch by
        # design; a refusal case declaring facts is out of that branch's scope.
        if _in_scope(cases, case_id, check)
    )
    assert not under, f"{check} skipped {len(under)} case(s) that declare `{field}`: {under[:5]}"


def _in_scope(cases: list[dict], case_id: str, check: str) -> bool:
    case = next(c for c in cases if c["id"] == case_id)
    if check == "required_facts_present":
        return case["expected_behavior"] in ("answer", "partial")
    return True


@pytest.mark.parametrize("check", sorted(CASE_CONDITIONAL))
def test_a_case_conditional_check_has_a_floor_and_a_ceiling(census, check: str) -> None:
    """Both bounds, because either one alone reads as a working conditional.

    Emitted on zero cases: the check is dead and its own self-test
    (`tests/test_selftest.py`) is the only thing that has ever run it. Emitted
    on every case: the condition is decorative and the check is back to
    recording a verdict over whatever the case happened not to declare.
    """
    cases, emitted = census
    count = len(emitted.get(check, []))
    assert count > 0, f"{check} was emitted on no case at all; it is examining nothing"
    assert count < len(cases), (
        f"{check} was emitted on all {len(cases)} cases. Either every case now "
        f"declares `{CASE_CONDITIONAL[check]}` — in which case delete it from "
        "CASE_CONDITIONAL and say so — or the conditional stopped applying."
    )


def test_no_check_is_recorded_over_an_empty_declared_list(census) -> None:
    """The table-free half: no emitted check may correspond to an empty case list.

    `CASE_CONDITIONAL` goes stale the moment someone adds a third check derived
    from a case-declared list. This walks the case fields instead: for every
    list-valued field a case declares, no check may be recorded on a case where
    that field is absent *and* recorded nowhere else. It is weaker than the
    table and it needs no maintenance.
    """
    cases, emitted = census
    list_fields = {
        key
        for case in cases
        for key, value in case.items()
        if isinstance(value, list) and value and key != "mirror_of"
    }
    for check, field in CASE_CONDITIONAL.items():
        assert field in list_fields, (
            f"CASE_CONDITIONAL names `{field}` for {check}, but no committed case "
            "declares it as a non-empty list. The entry is stale."
        )
        assert check in emitted, f"{check} is in CASE_CONDITIONAL but was never emitted"


def test_every_case_carries_at_least_the_three_unconditional_checks(census) -> None:
    """A case whose check list is empty passes vacuously.

    `evals.runner` scores a case with `all(c.passed for c in checks)`, and
    `all([])` is `True` — the same fold, one level up from the one this module
    exists for. Three checks apply to every case regardless of expected
    behaviour: determination language, structured-contract validity, and
    response language.
    """
    cases, emitted = census
    per_case: collections.Counter[str] = collections.Counter()
    for case_ids in emitted.values():
        for case_id in case_ids:
            per_case[case_id] += 1
    thin = sorted(case["id"] for case in cases if per_case[case["id"]] < 3)
    assert not thin, f"cases graded by fewer than three checks: {thin[:5]}"
