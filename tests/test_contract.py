"""EXP-04 — structured answer contract (docs/ideation/03-expansions.md)."""

from __future__ import annotations

from assistant.answer import AnswerResult, Citation
from assistant.contract import build_structured_answer, validate_answer_contract

_CITATION = Citation(
    doc_id="mst-fares",
    agency="MST",
    title="MST Fares and Passes",
    url="https://mst.org/fares/",
    fetch_date="2026-06-12",
)
_BENEFITS_CITATION = Citation(
    doc_id="mst-fares-benefits",
    agency="MST",
    title="MST Benefits",
    url="https://mst.org/benefits/",
    fetch_date="2026-06-12",
)


def _answered(text: str, citations=None) -> AnswerResult:
    return AnswerResult(
        question="q",
        answer=text,
        kind="answered",
        citations=citations if citations is not None else [_CITATION],
        as_of_date="2026-06-12",
    )


class TestBuildStructuredAnswer:
    def test_extracts_price_with_context(self):
        result = _answered(
            "The regular single ride fare is $2.00 [doc:mst-fares]. "
            "Based on policies published as of 2026-06-12."
        )
        structured = build_structured_answer(result)
        assert structured.structured_ok
        assert len(structured.prices) == 1
        assert structured.prices[0].amount == "2.00"
        assert structured.prices[0].currency == "USD"
        assert "$2.00" in structured.prices[0].context

    def test_extracts_multiple_prices(self):
        result = _answered(
            "The single ride fare is $2.00 [doc:mst-fares]. "
            "The monthly pass is $60 [doc:mst-fares]."
        )
        structured = build_structured_answer(result)
        amounts = sorted(p.amount for p in structured.prices)
        assert amounts == ["2.00", "60.00"]

    def test_extracts_proof_doc_from_sentence_naming_documentation(self):
        result = _answered(
            "Veterans need to show a DD Form 214 or veteran ID card [doc:mst-fares] "
            "to get the reduced fare."
        )
        structured = build_structured_answer(result)
        assert len(structured.proof_docs) == 1
        assert structured.proof_docs[0].doc_id == "mst-fares"
        assert "DD Form 214" in structured.proof_docs[0].context

    def test_extracts_next_step(self):
        result = _answered(
            "Seniors qualify with a Medicare card [doc:mst-fares]. "
            "Contact MST customer service to enroll."
        )
        structured = build_structured_answer(result)
        assert "Contact MST" in structured.next_step

    def test_no_next_step_when_answer_names_none(self):
        result = _answered("The fare is $2.00 [doc:mst-fares].")
        structured = build_structured_answer(result)
        assert structured.next_step == ""

    def test_decision_owner_is_the_cited_agency_not_the_assistant(self):
        result = _answered("The fare is $2.00 [doc:mst-fares].")
        structured = build_structured_answer(result)
        assert structured.decision_owner == "MST"

    def test_decision_owner_empty_when_no_citations(self):
        result = _answered("The fare is $2.00.", citations=[])
        structured = build_structured_answer(result)
        assert structured.decision_owner == ""

    def test_criterion_strips_citation_tags(self):
        result = _answered("The fare is $2.00 [doc:mst-fares].")
        structured = build_structured_answer(result)
        assert "[doc:" not in structured.criterion

    def test_combined_citation_is_stripped_and_all_proof_docs_are_extracted(self):
        result = _answered(
            "Bring an ID card [doc:mst-fares, doc:mst-fares-benefits] to apply.",
            citations=[_CITATION, _BENEFITS_CITATION],
        )
        structured = build_structured_answer(result)

        assert "[doc:" not in structured.criterion
        assert {proof.doc_id for proof in structured.proof_docs} == {
            "mst-fares",
            "mst-fares-benefits",
        }

    def test_refusal_kind_populates_criterion_from_decline_message(self):
        result = AnswerResult(
            question="q",
            answer="I don't have a published policy that answers this.",
            kind="refused_no_support",
        )
        structured = build_structured_answer(result)
        assert structured.structured_ok
        assert structured.kind == "refused_no_support"
        assert structured.criterion
        assert structured.prices == []
        assert structured.citations == []

    def test_parse_failure_falls_back_honestly_not_silently(self, monkeypatch):
        # The "never hidden" promise: if the heuristic parser blows up, the
        # caller gets structured_ok=False and a reason, not a crash and not a
        # silently-empty structured payload presented as if it were real.
        import assistant.contract as contract_module

        def boom(citations):
            raise RuntimeError("simulated parse failure")

        monkeypatch.setattr(contract_module, "_extract_decision_owner", boom)
        result = _answered("The fare is $2.00 [doc:mst-fares].")
        structured = build_structured_answer(result)
        assert structured.structured_ok is False
        assert "parse_error" in structured.fallback_reason

    def test_every_built_payload_validates_against_the_committed_schema(self):
        cases = [
            _answered("The fare is $2.00 [doc:mst-fares]. Contact MST to apply."),
            _answered("No dollar amount here, just published criteria [doc:mst-fares]."),
            AnswerResult(question="q", answer="Please contact the agency.", kind="refused_input"),
        ]
        for result in cases:
            structured = build_structured_answer(result)
            assert validate_answer_contract(structured.to_json_dict()) == []


class TestValidateAnswerContract:
    def test_valid_payload_has_no_errors(self):
        payload = {
            "kind": "answered",
            "criterion": "The fare is $2.00.",
            "prices": [{"amount": "2.00", "currency": "USD", "context": "The fare is $2.00."}],
            "proof_docs": [],
            "next_step": "",
            "decision_owner": "MST",
            "as_of_date": "2026-06-12",
            "citations": [],
        }
        assert validate_answer_contract(payload) == []

    def test_missing_required_field_is_an_error(self):
        payload = {
            "kind": "answered",
            "criterion": "x",
            "prices": [],
            "proof_docs": [],
            "next_step": "",
            "decision_owner": "",
            "as_of_date": "",
            # citations omitted
        }
        errors = validate_answer_contract(payload)
        assert errors
        assert any("citations" in e for e in errors)

    def test_unknown_kind_is_an_error(self):
        payload = {
            "kind": "made_up",
            "criterion": "",
            "prices": [],
            "proof_docs": [],
            "next_step": "",
            "decision_owner": "",
            "as_of_date": "",
            "citations": [],
        }
        errors = validate_answer_contract(payload)
        assert errors

    def test_malformed_price_amount_is_an_error(self):
        payload = {
            "kind": "answered",
            "criterion": "x",
            "prices": [{"amount": "two dollars", "currency": "USD", "context": "x"}],
            "proof_docs": [],
            "next_step": "",
            "decision_owner": "",
            "as_of_date": "",
            "citations": [],
        }
        errors = validate_answer_contract(payload)
        assert errors
