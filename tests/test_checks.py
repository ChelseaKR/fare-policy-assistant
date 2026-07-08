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

    def test_structured_contract_schema_valid_on_ordinary_answer(self):
        # EXP-04: every case gets a schema-conformance check on the derived
        # structured contract, alongside the existing prose checks.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["structured_contract_schema_valid"].passed

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

    def test_forbidden_content_match_is_case_insensitive(self):
        # A forbidden phrase must be caught regardless of casing; otherwise an
        # answer could slip a banned claim past the gate just by capitalizing it.
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "forbidden_content": ["express lane discount"],
        }
        result = _answered("The Express Lane Discount applies [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["forbidden_content_absent"].passed

    def test_required_fact_match_is_case_insensitive(self):
        # A required fact present in different casing must still count as present;
        # a case-sensitive match would fail a correct, grounded answer.
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "required_facts": ["DD Form 214"],
        }
        result = _answered("Veterans show a dd form 214 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["required_facts_present"].passed

    def test_agency_check_skipped_when_case_sets_no_scope(self):
        # correct_agency_cited must only be evaluated when the case names an
        # agency_scope; emitting it otherwise would fail answers that never
        # claimed a specific agency.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.")
        names = {c.name for c in run_checks(case, result, DOC_IDS)}
        assert "correct_agency_cited" not in names


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
