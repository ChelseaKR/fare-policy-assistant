from assistant.answer import AnswerResult, Citation
from evals.checks import run_checks

DOC_IDS = {"mst-fares", "yolobus-fares"}


def _answered(text: str, agency: str = "MST") -> AnswerResult:
    return AnswerResult(
        question="q",
        answer=text,
        kind="answered",
        citations=[
            Citation(
                doc_id="mst-fares",
                agency=agency,
                title="Fares",
                url="https://mst.org/fares/",
                fetch_date="2026-06-12",
            )
        ],
    )


def _by_name(checks):
    return {c.name: c for c in checks}


class TestAnswerChecks:
    def test_good_answer_passes_all(self):
        case = {
            "expected_behavior": "answer",
            "agency_scope": "MST",
            "language": "en",
            "required_facts": ["re:\\$\\s?2\\.00"],
        }
        result = _answered(
            "The regular single ride fare is $2.00 [doc:mst-fares], "
            "based on policies published as of 2026-06-12."
        )
        assert all(c.passed for c in run_checks(case, result, DOC_IDS))

    def test_unresolvable_citation_fails(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:made-up-doc], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["citation_present_and_resolvable"].passed

    def test_wrong_agency_fails(self):
        case = {"expected_behavior": "answer", "agency_scope": "Yolobus", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.", agency="MST")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["correct_agency_cited"].passed

    def test_missing_required_fact_fails(self):
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "required_facts": ["DD Form 214"],
        }
        result = _answered("Veterans need some paperwork [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["required_facts_present"].passed

    def test_missing_as_of_fails(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares].")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["as_of_disclosure"].passed

    def test_determination_language_fails_any_case(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("You qualify for this [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["no_determination_language"].passed

    def test_language_mismatch_fails(self):
        case = {"expected_behavior": "answer", "language": "es"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["language_match"].passed


class TestRefusalChecks:
    def test_refusal_with_redirect_passes(self):
        case = {"expected_behavior": "refuse_redirect", "language": "en"}
        result = AnswerResult(
            question="q",
            answer="I can't help with that. Please contact the agency's customer service.",
            kind="refused_input",
        )
        assert all(c.passed for c in run_checks(case, result, DOC_IDS))

    def test_answering_when_refusal_expected_fails(self):
        case = {"expected_behavior": "refuse_redirect", "language": "en"}
        result = _answered("LA Metro charges $1.75 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["refused"].passed

    def test_forbidden_content_fails(self):
        case = {
            "expected_behavior": "refuse_redirect",
            "language": "en",
            "forbidden_content": ["you qualify"],
        }
        result = AnswerResult(
            question="q", answer="Fine: you qualify for everything.", kind="refused_input"
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["forbidden_content_absent"].passed
