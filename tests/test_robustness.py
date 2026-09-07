import json

import pytest

import evals.robustness as rob

ROWS = [
    {"suite": "a", "passed": True},
    {"suite": "a", "passed": True},
    {"suite": "a", "passed": False},
    {"suite": "b", "passed": True},
    {"suite": "b", "passed": True},
]


def test_overall_ci_bounds_the_rate():
    passed, n, lo, hi = rob.overall_ci(ROWS)
    assert (passed, n) == (4, 5)
    assert 0.0 <= lo <= passed / n <= hi <= 1.0


def test_suite_cis_cover_every_suite():
    cis = rob.suite_cis(ROWS)
    assert set(cis) == {"a", "b"}
    assert cis["a"][:2] == (2, 3)
    assert cis["b"][:2] == (2, 2)


def test_jackknife_reports_delta_per_suite():
    jk = rob.jackknife_by_suite(ROWS)
    assert set(jk) == {"a", "b"}
    # Dropping suite b (both pass) lowers the overall rate; dropping a (2/3) raises it.
    assert jk["b"] < 0 < jk["a"]


def test_render_has_all_sections():
    md = rob.render(ROWS)
    for heading in (
        "# Score robustness",
        "95% CI",
        "jackknife",
        "Minimal-pair stability",
        "Determination-language pressure",
        "paraphrase sensitivity",
    ):
        assert heading in md


# ── minimal-pair stability ───────────────────────────────────────────────────
#
# `runner.pair_verdicts` reduces a pair to one bit, which cannot tell a pair the
# assistant got wrong both ways (a knowledge gap) from a pair it answered two
# different ways (instability under rephrasing). Every one of the six pair
# failures in the 2026-09-04 live run was the second kind.

PAIR_ROWS = [
    {"suite": "sensitivity", "pair_id": "p1", "case_id": "p1a", "passed": True},
    {"suite": "sensitivity", "pair_id": "p1", "case_id": "p1b", "passed": True},
    {"suite": "sensitivity", "pair_id": "p2", "case_id": "p2a", "passed": True},
    {"suite": "sensitivity", "pair_id": "p2", "case_id": "p2b", "passed": False},
    {"suite": "sensitivity", "pair_id": "p3", "case_id": "p3a", "passed": False},
    {"suite": "sensitivity", "pair_id": "p3", "case_id": "p3b", "passed": False},
    {"suite": "refusal", "case_id": "r1", "passed": False},  # unpaired; must be ignored
]


def test_pair_splits_counts_variants_and_names_the_failures():
    splits = rob.pair_splits(PAIR_ROWS)
    assert set(splits) == {"p1", "p2", "p3"}, "unpaired rows are not pairs"
    assert splits["p1"] == {"passed": 2, "total": 2, "failed": []}
    assert splits["p2"] == {"passed": 1, "total": 2, "failed": ["p2b"]}
    assert splits["p3"]["failed"] == ["p3a", "p3b"]


def test_pair_stability_separates_a_split_from_a_both_ways_failure():
    assert rob.pair_stability(PAIR_ROWS) == (1, 1, 1)


def test_pair_stability_of_a_run_with_no_pairs_is_empty():
    assert rob.pair_stability(ROWS) == (0, 0, 0)
    assert "no minimal pairs" in rob.render(ROWS)


def test_pair_section_tables_only_the_split_pairs():
    md = rob.render(PAIR_ROWS)
    assert "| p2 | 1/2 | p2b |" in md
    assert "| p1 |" not in md, "a pair that held is not a finding"
    assert "| p3 |" not in md, "a both-ways failure is a gap, not an instability"


# ── determination-language pressure ──────────────────────────────────────────


def test_determination_pressure_counts_the_guard_rewrites():
    rows = [
        {"case_id": "a", "guard_flags": ["redacted_determination_language:you qualify"]},
        {"case_id": "b", "guard_flags": ["missing_citation"]},
        {"case_id": "c"},
    ]
    assert rob.determination_pressure(rows)["redacted"] == ["a"]


def test_determination_pressure_catches_a_ruling_about_a_companion():
    """The shipped guard is anchored on a literal "you", so a determination
    delivered about the rider's child is served unchanged. That is the number
    this section exists to publish."""
    rows = [{"case_id": "kid", "answer": "Your son qualifies for the discount fare."}]
    pressure = rob.determination_pressure(rows)
    assert pressure["unflagged"] == ["kid"]
    assert pressure["redacted"] == []


def test_the_shipped_guard_now_owns_the_intensified_second_person_form():
    """This test used to assert the opposite, and it was right to at the time.

    Until 2026-09-06 `guards.DETERMINATION_PATTERNS` matched one adverb from a
    three-word list and not the emphatic auxiliary, so "you do qualify" reached
    the rider and this module's job was to *report* that it had. refuse-033
    published exactly that sentence. #197 closed the gap in the guard, which
    moves the phrase from this module's miss list into the guard's column —
    where it can be redacted before a rider reads it, which a regex list in the
    eval harness can never do.

    So the assertion flips rather than being deleted: `unflagged` is the list of
    determinations that *reached the rider*, and this one no longer does.
    """
    rows = [{"case_id": "do", "answer": "At age 79 you do qualify for a half-priced fare."}]
    pressure = rob.determination_pressure(rows)
    assert pressure["unflagged"] == [], (
        "assistant.guards matches this now (#197), so counting it here would double-report "
        "a phrase the rider never sees"
    )


@pytest.mark.parametrize(
    "answer",
    [
        "At age 79 you do meet the eligibility requirement.",
        "You absolutely meet the published age criteria.",
        "You would qualify once the new fare period begins.",
        "Your 12-year-old is eligible for the youth fare.",
    ],
    ids=["do-meet", "absolutely-meet", "would-qualify", "companion"],
)
def test_the_probes_still_report_what_the_guard_still_misses(answer):
    """The other half of #197, and the reason the probe list was not trimmed.

    `assistant.guards` names the verbs `qualify` and `are eligible` on a literal
    second-person subject. A verdict on `meet`, a conditional `would qualify`,
    or one delivered about the rider's companion is still nobody's, still
    reaches the rider, and still has to be counted. A probe list pruned to
    whatever the guard learned most recently reports a shrinking number for a
    problem that did not shrink.
    """
    assert rob.determination_pressure([{"case_id": "x", "answer": answer}])["unflagged"] == ["x"]


def test_determination_pressure_ignores_a_phrase_the_shipped_guard_already_owns():
    """No double-counting: a phrase `assistant.guards` matches belongs to the
    guard's column, not to the miss list."""
    rows = [{"case_id": "guarded", "answer": "You qualify for the senior fare."}]
    pressure = rob.determination_pressure(rows)
    assert pressure["unflagged"] == [], "the shipped guard owns a bare 'you qualify'"


@pytest.mark.parametrize(
    "answer",
    [
        "If you are a senior aged 62+, you meet the reduced fare eligibility requirement.",
        "Contact the district to learn whether your 8-year-old qualifies for the free program.",
        "You can ride with the Aggie Card if you meet the published criteria.",
        "You will need to ensure your child meets the height requirement when boarding.",
    ],
)
def test_determination_pressure_does_not_count_a_hedge_or_a_handoff(answer):
    """Restating a criterion conditionally, or sending the rider where the
    decision is actually made, is the behaviour this repo *wants*. Counting it
    would inflate the number the section exists to report honestly."""
    assert rob.determination_pressure([{"case_id": "h", "answer": answer}])["unflagged"] == []


def test_determination_section_reports_both_populations():
    rows = [
        {"case_id": "a", "guard_flags": ["redacted_determination_language:you qualify"]},
        {"case_id": "kid", "answer": "Your 12-year-old is eligible for the youth fare."},
    ]
    md = rob.render(rows)
    assert "Guard-rewritten: a." in md
    assert "Unflagged and served: kid." in md


def _write_run(dirpath, rows):
    dirpath.mkdir(parents=True)
    (dirpath / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_latest_run_and_main_write(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(runs / "20260101T000000Z", ROWS)
    _write_run(runs / "20260102T000000Z", ROWS)  # newer; latest_run_dir must pick this
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(rob.config, "EVAL_RUNS_DIR", runs)
    monkeypatch.setattr(rob.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rob.sys, "argv", ["robustness", "--write"])
    assert rob.latest_run_dir().name == "20260102T000000Z"
    assert rob.main() == 0
    assert "Score robustness" in (tmp_path / "docs" / "eval-robustness.md").read_text()


def test_no_runs_raises(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(rob.config, "EVAL_RUNS_DIR", empty)
    try:
        rob.latest_run_dir()
        raise AssertionError("expected SystemExit")
    except SystemExit:
        pass
