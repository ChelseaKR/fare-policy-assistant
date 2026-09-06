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
        "checks": [{"name": "citation_present_and_resolvable", "passed": False, "detail": "none"}],
        "judges": [],
    },
]


def test_a_passage_shorter_than_the_excerpt_is_not_marked_as_continuing():
    """The ellipsis was unconditional, so a passage that had already ended read
    as one with more to come. A reader checking whether a document carries a
    figure could not tell the end of the evidence from the end of the excerpt."""
    records = [
        {
            **RECORDS[1],
            "passages": [
                {"chunk_id": "mst-fares#1", "section": "Fares", "score": 9.1, "text": "short"}
            ],
        }
    ]
    md = generate_markdown(SUMMARY, records)
    assert "score 9.1" in md
    assert ": short\n" in md or ": short" in md.replace("…", "!ELLIPSIS!")


def test_a_passage_the_recorder_truncated_is_marked_as_continuing():
    records = [
        {
            **RECORDS[1],
            "passages": [
                {
                    "chunk_id": "mst-fares#1",
                    "section": "Fares",
                    "score": 9.1,
                    "text": "short",
                    "text_truncated": True,
                    "text_chars": 1200,
                }
            ],
        }
    ]
    assert "short…" in generate_markdown(SUMMARY, records)


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


def test_failure_trace_shows_passage_provenance():
    """Issue #142: an outside reader checking a dated claim in a failure trace
    needs the same source/fetch-date provenance the answer model and judge
    were shown, not just chunk id and score."""
    records = [
        {
            "case_id": "fresh-999",
            "suite": "groundedness",
            "mirror_of": None,
            "passed": False,
            "question": "how much is the pass?",
            "rationale": "fare table",
            "answer": "no idea",
            "kind": "answered",
            "passages": [
                {
                    "chunk_id": "mst-fares#1",
                    "doc_id": "mst-fares",
                    "agency": "MST",
                    "doc_title": "Fares",
                    "url": "https://mst.org/fares/",
                    "fetch_date": "2026-06-12",
                    "section": "Fares",
                    "score": 9.1,
                    "text": "x" * 300,
                }
            ],
            "checks": [],
            "judges": [],
        }
    ]
    md = generate_markdown(SUMMARY, records)
    assert "Fares — Fares, score 9.1, fetched 2026-06-12" in md
    assert "--replicates" in md
    assert "evals.compare" in md


def test_scoreboard_renders_wilson_interval_when_replicated():
    summary = {
        **SUMMARY,
        "replicates": 3,
        "suites": {
            "groundedness": {
                "passed": 1,
                "total": 2,
                "pass_rate": 50.0,
                "ci_low": 23.7,
                "ci_high": 76.3,
                "replicates": 3,
            }
        },
    }
    md = generate_markdown(summary, RECORDS)
    assert "| groundedness | 1 | 2 | 50.0% (23.7–76.3) |" in md
    assert "mean over 3 replicate runs" in md
