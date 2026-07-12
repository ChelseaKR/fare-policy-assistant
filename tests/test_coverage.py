import evals.coverage as cov


def test_matrix_and_corpus_are_populated():
    matrix = cov.build_matrix()
    corpus = cov.corpus_programs_by_agency()
    assert matrix, "coverage matrix is empty"
    # Every agency in the profile should appear in the corpus program map.
    for agency in cov.agencies():
        assert agency in corpus
    # A well-known cell: MST publishes and is tested for the senior program.
    assert matrix.get(("MST", "senior"), 0) > 0


def test_case_agencies_and_programs():
    case = {
        "agency_scope": "SBMTD",
        "question": "Does my 3-year-old ride free?",
        "required_facts": ["45 inches"],
        "rationale": "children under 45 inches",
    }
    assert cov.case_agencies(case) == ["SBMTD"]
    assert "child free" in cov.case_programs(case)


def test_case_agencies_falls_back_to_detection():
    case = {"question": "What is the senior fare on MST?", "rationale": ""}
    assert cov.case_agencies(case) == ["MST"]


def test_blind_spots_flags_untested_corpus_program():
    corpus = {"MST": {"senior", "veteran"}}
    # senior is tested, veteran is not -> veteran is the blind spot.
    matrix = {("MST", "senior"): 3}
    spots = cov.blind_spots(matrix, corpus)
    assert ("MST", "veteran") in spots
    assert ("MST", "senior") not in spots


def test_render_markdown_has_matrix_and_all_agencies():
    md = cov.render_markdown()
    assert "# Eval coverage map" in md
    assert "Blind spots" in md
    for agency in cov.agencies():
        assert agency in md


def test_main_prints_without_writing(capsys):
    assert cov.main() == 0
    assert "Eval coverage map" in capsys.readouterr().out


def test_main_write_regenerates_doc(monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(cov.config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cov.sys, "argv", ["coverage", "--write"])
    assert cov.main() == 0
    written = (tmp_path / "docs" / "eval-coverage.md").read_text()
    assert "Eval coverage map" in written
