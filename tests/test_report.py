from evals.report import generate_markdown

SUMMARY = {
    "run_at": "2026-06-12T01:00:00+00:00",
    "mode": "full",
    "offline": True,
    "judges_ran": False,
    "answer_model": "mock",
    "judge_model": "mock",
    "prompt_versions": {"system": "v1 2026-06-11"},
    "duration_seconds": 1.0,
    "suites": {"groundedness": {"passed": 1, "total": 2, "pass_rate": 50.0}},
    "total": {"passed": 1, "total": 2},
}

RECORDS = [
    {
        "case_id": "ground-001",
        "suite": "groundedness",
        "mirror_of": None,
        "passed": True,
        "question": "ok?",
        "rationale": "r",
        "answer": "fine [doc:mst-fares]",
        "kind": "answered",
        "passages": [],
        "checks": [],
        "judges": [],
    },
    {
        "case_id": "ground-002",
        "suite": "groundedness",
        "mirror_of": None,
        "passed": False,
        "question": "how much is the pass?",
        "rationale": "fare table",
        "answer": "no idea",
        "kind": "answered",
        "passages": [
            {"chunk_id": "mst-fares#1", "section": "Fares", "score": 9.1, "text": "x" * 300}
        ],
        "checks": [
            {"name": "citation_present_and_resolvable", "passed": False, "detail": "none"}
        ],
        "judges": [],
    },
]


def test_scoreboard_and_failures_present():
    md = generate_markdown(SUMMARY, RECORDS)
    assert "| groundedness | 1 | 2 | 50.0% |" in md
    assert "ground-002" in md
    assert "citation_present_and_resolvable: none" in md
    # Passing cases are not dumped as failures.
    assert "### ground-001" not in md


def test_offline_run_is_labeled():
    md = generate_markdown(SUMMARY, RECORDS)
    assert "deterministic checks only" in md
    assert "skipped, not passed" in md


def test_variance_section_always_documents_the_tooling():
    md = generate_markdown(SUMMARY, RECORDS)
    assert "## Measuring variance" in md
    assert "--replicates" in md
    assert "evals.compare" in md


def test_scoreboard_renders_wilson_interval_when_replicated():
    summary = {
        **SUMMARY,
        "replicates": 3,
        "suites": {
            "groundedness": {
                "passed": 1, "total": 2, "pass_rate": 50.0,
                "ci_low": 23.7, "ci_high": 76.3, "replicates": 3,
            }
        },
    }
    md = generate_markdown(summary, RECORDS)
    assert "| groundedness | 1 | 2 | 50.0% (23.7–76.3) |" in md
    assert "mean over 3 replicate runs" in md
