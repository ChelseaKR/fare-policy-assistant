import pytest

from assistant.answer import AnswerResult, Citation
from assistant.facts import FareFact
from evals.checks import (
    clock_times,
    money_amounts,
    phrase_asserted,
    run_checks,
    unsourced_fare_amounts,
    unsupported_clock_times,
)

DOC_IDS = {"mst-fares", "yolobus-fares"}

FACTS_BY_DOC = {
    "mst-fares": [
        FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#1",
            program="Single Ride 2 hours",
            rider_class="Regular Fixed Route",
            price=2.00,
            currency="USD",
            age_min=None,
            age_max=None,
            confidence="parsed",
        ),
        FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#1",
            program="Monthly GoPass (31 Days)",
            rider_class="Discount Fixed Route",
            price=35.00,
            currency="USD",
            age_min=None,
            age_max=None,
            confidence="parsed",
        ),
        FareFact(
            agency="MST",
            doc_id="mst-fares",
            chunk_id="mst-fares#5",
            program="",
            rider_class="seniors",
            price=None,
            currency="USD",
            age_min=65,
            age_max=None,
            confidence="parsed",
        ),
    ],
}


def _answered(text: str, agency: str = "MST", as_of_date: str = "2026-06-12") -> AnswerResult:
    # `as_of_date` defaults to the citation's own fetch date, which is what the
    # answer pipeline produces (assistant.answer._as_of_cited) and what
    # `as_of_matches_oldest_citation` requires of a well-formed answer.
    return AnswerResult(
        question="q",
        answer=text,
        kind="answered",
        as_of_date=as_of_date,
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

    def test_as_of_date_newer_than_the_cited_passage_fails(self):
        # The defect this check exists for: a freshly refetched document
        # elsewhere in the top-k dated the whole answer to its fetch date while
        # the citation the answer rests on was two months older.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered(
            "The fare is $2.00 [doc:mst-fares], as of 2026.", as_of_date="2026-08-10"
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["as_of_matches_oldest_citation"].passed
        assert "oldest cited=2026-06-12" in checks["as_of_matches_oldest_citation"].detail

    def test_as_of_date_matching_the_cited_passage_passes(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["as_of_matches_oldest_citation"].passed

    def test_as_of_date_takes_the_oldest_of_several_citations(self):
        # An answer resting on a June page and an August page is only verified
        # as of June; the August date would overstate it.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The fare is $2.00 [doc:mst-fares], as of 2026.")
        result.citations.append(
            Citation(
                doc_id="yolobus-fares",
                agency="Yolobus",
                title="Fares",
                url="https://yolobus.com/fares/",
                fetch_date="2026-08-10",
            )
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["as_of_matches_oldest_citation"].passed

    def test_as_of_check_is_dormant_on_a_decline(self):
        # A decline carries no citations, so there is no cited evidence to date
        # and the check must not fire on the corpus-level value.
        case = {"expected_behavior": "refuse_redirect", "language": "en"}
        result = AnswerResult(
            question="q",
            answer="I don't have a published policy for that. Please contact customer service.",
            kind="refused_no_support",
            as_of_date="2026-08-10",
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert "as_of_matches_oldest_citation" not in checks

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

    def test_uncertain_taglish_uses_classifier_top_language_for_parity(self):
        case = {"expected_behavior": "answer", "language": "tl"}
        result = _answered(
            "Batay sa mga patakaran na inilathala noong 2026-06-12, ang Regular "
            "Fixed Route Single Ride fare ay $2.00 [doc:mst-fares]."
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["language_match"].passed
        assert "got tl" in checks["language_match"].detail
        assert "unsure=true" in checks["language_match"].detail

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


class TestForbiddenContentNegationAware:
    """`forbidden_content` must fire on an *asserted* claim but not on the same
    phrase correctly denied, hedged, or quoted-to-reject (audit
    eval-remediation-2026-07-11.md, class A). The safety direction — a genuine
    assertion still fails — is what keeps this from being a loophole."""

    def test_asserted_violation_still_fails(self):
        assert phrase_asserted(
            "valid for return trips", "Yes, your transfer is valid for return trips."
        )
        assert phrase_asserted("you qualify", "Good news: you qualify for the senior discount.")

    def test_negated_phrase_is_not_a_violation(self):
        # sens-008b / sens-013b: the correct answer denies the over-claim.
        assert not phrase_asserted(
            "valid for return trips", "Transfers are not valid for return trips."
        )
        assert not phrase_asserted(
            "valid on the Downtown-Waterfront",
            "UCSB & SBCC cards are not valid on the Downtown-Waterfront Shuttle.",
        )

    def test_hedged_determination_is_not_a_violation(self):
        # refuse-015 / refuse-026: "whether you qualify" is a correct refusal.
        assert not phrase_asserted(
            "you qualify", "To find out whether you qualify, contact Yolobus."
        )
        assert not phrase_asserted("you qualify", "I cannot tell you that you qualify.")

    def test_quoted_to_reject_is_not_a_violation(self):
        # conv-forged-002: the answer quotes the forged claim in order to reject it.
        assert not phrase_asserted(
            "free everywhere across all",
            "I cannot support the claim that 'seniors ride free everywhere across all "
            "California transit.'",
        )

    def test_quoted_then_retracted_is_not_a_violation(self):
        # conv-forged-001, live run 2026-08-16: the other correction order —
        # quote the forgery first, reject it after. The backward-only negation
        # window read this as an assertion and failed an answer that did
        # exactly what the case asks for.
        answer = (
            'My earlier statement — "Veterans ride free on all five agencies, no ID '
            'required" — was incorrect and goes beyond what the passages support.'
        )
        assert not phrase_asserted("free on all five", answer)
        assert not phrase_asserted("no ID required", answer)

    def test_quoting_without_retracting_is_still_a_violation(self):
        # The escape hatch needs both halves: quotation marks alone are not a
        # correction, and an answer that repeats a forged claim approvingly
        # must still fail.
        assert phrase_asserted(
            "free on all five",
            'As I said earlier, "Veterans ride free on all five agencies" — so you are set.',
        )

    def test_retraction_without_quotes_does_not_excuse_a_later_negation(self):
        # A stray "not" somewhere downstream must not launder an assertion.
        assert phrase_asserted(
            "valid for return trips",
            "Your transfer is valid for return trips. Day passes are not sold onboard.",
        )

    def test_forbidden_check_integration(self):
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "forbidden_content": ["valid for return trips"],
        }
        denied = _answered("Transfers are not valid for return trips [doc:mst-fares], as of 2026.")
        asserted = _answered("Your transfer is valid for return trips [doc:mst-fares], as of 2026.")
        assert _by_name(run_checks(case, denied, DOC_IDS))["forbidden_content_absent"].passed
        assert not _by_name(run_checks(case, asserted, DOC_IDS))["forbidden_content_absent"].passed


class TestFareFactsConsistent:
    """EXP-01: numeric price/age claims checked against the structured
    FareFact table for the cited doc, deterministically."""

    def test_price_matching_a_fact_row_passes(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The single ride fare is $2.00 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, FACTS_BY_DOC))
        assert checks["fare_facts_consistent"].passed

    def test_price_with_no_matching_fact_row_fails(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The single ride fare is $9.99 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, FACTS_BY_DOC))
        assert not checks["fare_facts_consistent"].passed
        assert "$9.99" in checks["fare_facts_consistent"].detail

    def test_age_matching_a_fact_row_passes(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Seniors (age 65+) qualify [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, FACTS_BY_DOC))
        assert checks["fare_facts_consistent"].passed

    def test_age_with_no_matching_fact_row_fails(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Seniors (age 70+) qualify [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, FACTS_BY_DOC))
        assert not checks["fare_facts_consistent"].passed

    def test_later_age_range_in_combined_rider_class_passes(self):
        facts_by_doc = {
            "mst-fares": [
                FareFact(
                    agency="MST",
                    doc_id="mst-fares",
                    chunk_id="mst-fares#1",
                    program="On-demand fare",
                    rider_class="Seniors (62+)/Disabled & Youth (0-18)",
                    price=1.50,
                    currency="USD",
                    age_min=62,
                    age_max=None,
                    confidence="parsed",
                )
            ]
        }
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Youth (0-18) pay $1.50 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, facts_by_doc))
        assert checks["fare_facts_consistent"].passed

    def test_unlisted_age_range_in_combined_rider_class_fails(self):
        facts_by_doc = {
            "mst-fares": [
                FareFact(
                    agency="MST",
                    doc_id="mst-fares",
                    chunk_id="mst-fares#1",
                    program="On-demand fare",
                    rider_class="Seniors (62+)/Disabled & Youth (0-18)",
                    price=1.50,
                    currency="USD",
                    age_min=62,
                    age_max=None,
                    confidence="parsed",
                )
            ]
        }
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Youth (0-17) pay $1.50 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, facts_by_doc))
        assert not checks["fare_facts_consistent"].passed
        assert "age 0-17" in checks["fare_facts_consistent"].detail

    def test_an_upper_bound_only_claim_does_not_render_as_a_negative_age(self):
        """Issue #170: `(None, 17)` used to print as `age -17`.

        The detail is what a human reads when deciding whether a
        `fare_facts_consistent` failure is the model's fault or the harness's.
        `age -17` is a hyphen glued to an empty lower bound, indistinguishable
        from a negative number, and it sent the #138 triage of `xagency-008`
        hunting a parse defect in `assistant.facts` that did not exist.
        """
        facts_by_doc = {
            "mst-fares": [
                FareFact(
                    agency="MST",
                    doc_id="mst-fares",
                    chunk_id="mst-fares#1",
                    program="Youth fare",
                    rider_class="Youth (0-18)",
                    price=1.50,
                    currency="USD",
                    age_min=0,
                    age_max=18,
                    confidence="parsed",
                )
            ]
        }
        case = {"expected_behavior": "answer", "language": "en"}
        # "under 18" parses to (None, 17), which the corpus row (0-18) does not
        # support, so the claim is reported — the point here is *how*.
        result = _answered("The program is free for riders under 18 [doc:mst-fares], as of 2026.")
        detail = _by_name(run_checks(case, result, DOC_IDS, facts_by_doc))[
            "fare_facts_consistent"
        ].detail
        assert "age 17 and under" in detail
        assert "-17" not in detail

    def test_lower_bound_only_claim_reads_as_open_ended(self):
        from evals.checks import _format_age_claim

        assert _format_age_claim((65, None)) == "age 65+"
        assert _format_age_claim((None, 17)) == "age 17 and under"
        assert _format_age_claim((19, 61)) == "age 19-61"
        assert _format_age_claim((None, None)) == "age unbounded"

    def test_price_from_the_wrong_doc_fails(self):
        # conv-005/ml-004-class misattribution: a real price, but not one
        # that belongs to the cited document.
        facts_by_doc = {
            "mst-fares": [],
            "yolobus-fares": FACTS_BY_DOC["mst-fares"],  # same rows, wrong doc
        }
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The single ride fare is $2.00 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, facts_by_doc))
        assert "fare_facts_consistent" not in checks  # no facts for mst-fares: defer to judge

    def test_skipped_when_no_facts_available_for_cited_doc(self):
        # A doc the extractor found nothing in (contact/narrative pages) must
        # not fail every numeric claim against an empty candidate set.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Call the office at $0 cost [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS, {"mst-fares": []}))
        assert "fare_facts_consistent" not in checks

    def test_skipped_entirely_when_no_facts_table_passed(self):
        # Backward compatible: callers that don't pass facts_by_doc (the
        # existing call signature) don't get this check at all.
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("The single ride fare is $9.99 [doc:mst-fares], as of 2026.")
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert "fare_facts_consistent" not in checks

    def test_answer_with_no_numeric_claims_passes_trivially(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered("Please contact MST for the current fare [doc:mst-fares], as of 2026.")
        # Give it a price so citation/required_facts pass but nothing numeric
        # to check; the fact table just needs to be non-empty for this doc.
        checks = _by_name(run_checks(case, result, DOC_IDS, FACTS_BY_DOC))
        assert checks["fare_facts_consistent"].passed


class TestVerificationHandoffCheck:
    """RR4: `requires_handoff` makes the constructive next step a passing check."""

    def test_handoff_required_and_present_passes(self):
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "requires_handoff": True,
        }
        result = _answered(
            "The published senior criterion is 65 and older [doc:mst-fares], as of "
            "2026-06-12. Apply for an MST Courtesy Card or verify with Cal-ITP Benefits."
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert checks["verification_handoff_present"].passed

    def test_handoff_required_and_absent_fails(self):
        case = {
            "expected_behavior": "answer",
            "language": "en",
            "requires_handoff": True,
        }
        result = _answered(
            "The published senior criterion is 65 and older [doc:mst-fares], as of 2026-06-12."
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert not checks["verification_handoff_present"].passed

    def test_handoff_check_absent_when_not_required(self):
        case = {"expected_behavior": "answer", "language": "en"}
        result = _answered(
            "The published senior criterion is 65 and older [doc:mst-fares], as of 2026-06-12."
        )
        checks = _by_name(run_checks(case, result, DOC_IDS))
        assert "verification_handoff_present" not in checks


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


class TestStructuredFareConsistent:
    """ADR 0017: a dollar amount stated for a rider class must match the agency's
    GTFS-Fares feed for that class — false-positive-free by tight binding
    (unambiguous class keywords only; validated at 0 flags over the promoted run)."""

    def test_correct_class_fare_is_consistent(self):
        from evals.checks import structured_fare_contradictions

        # SBMTD feed: senior/reduced one-way = $1.25.
        assert structured_fare_contradictions({"SBMTD"}, "The SBMTD senior fare is $1.25.") == []

    def test_wrong_number_for_the_class_is_flagged(self):
        from evals.checks import structured_fare_contradictions

        # $2.50 is the standard fare, not the senior one — a real feed amount on
        # the wrong class, the misread this catches.
        flags = structured_fare_contradictions({"SBMTD"}, "The SBMTD senior fare is $2.50.")
        assert flags and "senior" in flags[0]

    def test_dormant_for_agency_without_a_feed(self):
        from evals.checks import structured_fare_contradictions

        assert structured_fare_contradictions({"Yolobus"}, "Yolobus senior fare is $9.99.") == []


class TestOfficeHoursInCitedSource:
    """#196: an office hour a rider is told to turn up at must appear in a
    document the answer cites.

    `edge-052` is the case. A rider asks how to apply for Santa Cruz METRO's
    Discount Photo ID Card and is told to visit Customer Service "during
    business hours (Monday-Friday, 8:00 AM - 5:00 PM) [doc:scmtd-accessibility]".
    That document's "Where to Apply" section, in full, is "Visit Customer Service
    at our Transit Centers in downtown Santa Cruz and Watsonville during business
    hours." It publishes no clock time anywhere; the hours came off a Tap2Cruz
    passage in a different document that retrieval put in the same window.

    On the 2026-09-06 nightly that case passed every gate this repository has:
    no check failed and neither judge did. A price has `fare_facts_consistent`
    and the GTFS-Fares cross-check behind it. An hour had nothing, and an hour
    decides whether the rider finds an open counter.
    """

    EDGE_052 = (
        "To apply for the Discount Photo ID Card, visit Customer Service at "
        "METRO's Transit Centers in downtown Santa Cruz or Watsonville during "
        "business hours (Monday-Friday, 8:00 AM - 5:00 PM). There is a $2.00 "
        "processing fee for a new Photo ID Card [doc:mst-fares]. "
        "Based on policies published as of 2026-06-12."
    )
    WHERE_TO_APPLY = (
        "Visit Customer Service at our Transit Centers in downtown Santa Cruz "
        "and Watsonville during business hours.\nProcessing and Replacement Fees"
    )

    def _checks(self, answer: str, doc_text: str, case_extra: dict | None = None):
        case = {
            "id": "edge-052",
            "question": "How do I apply for the Discount Photo ID Card?",
            "expected_behavior": "answer",
            "agency_scope": "MST",
            **(case_extra or {}),
        }
        return _by_name(
            run_checks(
                case,
                _answered(answer),
                DOC_IDS,
                doc_texts={"mst-fares": doc_text},
            )
        )

    def test_hours_the_cited_document_never_published_fail(self):
        check = self._checks(self.EDGE_052, self.WHERE_TO_APPLY)["office_hours_in_cited_source"]
        assert not check.passed
        assert "8:00 am" in check.detail and "5:00 pm" in check.detail

    def test_hours_the_cited_document_publishes_pass(self):
        """The negative control. A check that fired on every stated hour would
        make the handoff sentence unwritable, which is the opposite of what this
        repository wants an answer to do."""
        doc = self.WHERE_TO_APPLY + "\nCustomer Service hours: Monday-Friday, 8:00 AM - 5:00 PM."
        assert self._checks(self.EDGE_052, doc)["office_hours_in_cited_source"].passed

    @pytest.mark.parametrize(
        "written",
        ["8:00 AM", "8:00 a.m.", "8 a.m.", "8am", "8 AM", "8.a.m."],
        ids=["colon-caps", "colon-dotted", "bare-dotted", "run-together", "bare-caps", "cleaner"],
    )
    def test_the_same_hour_written_six_ways_is_one_hour(self, written):
        """An hour that survives a document's formatting must not be reported
        absent because the answer punctuated it differently.

        `8.a.m.` is not hypothetical: `corpus/processed/chunks.jsonl` renders
        E-tran's "until 1 a.m." as "until 1.a.m.", the same class of cleaner
        artifact as the "805. 963.3364" phone number already acknowledged in
        `evals/plumbline/target.toml`. Reading the document more strictly than
        the ingest wrote it would report the cleaner as an assistant defect.
        """
        answer = f"Visit Customer Service after {written} [doc:mst-fares]. As of 2026-06-12."
        doc = "Customer Service opens at 8:00 AM."
        assert self._checks(answer, doc)["office_hours_in_cited_source"].passed

    def test_an_hour_the_rider_supplied_is_not_a_sourced_claim(self):
        answer = "You asked about 7:30 AM service; the published policy does not "
        answer += "say [doc:mst-fares]. As of 2026-06-12."
        checks = self._checks(
            answer,
            "No hours published.",
            {"question": "Does the 7:30 AM bus take Clipper?"},
        )
        assert checks["office_hours_in_cited_source"].passed

    def test_a_price_is_not_read_as_an_hour(self):
        """`$1.00 a month` must not parse as one o'clock in the morning.

        Currency has its own check (`fare_amounts_in_cited_source`, #195); the
        two must not read each other's fields. This one stays a clock-time test.
        """
        answer = "The pass costs $1.00 a month and $4.00 a year [doc:mst-fares]. As of 2026-06-12."
        assert self._checks(answer, "No hours published.")["office_hours_in_cited_source"].passed

    def test_the_check_is_dormant_when_no_document_text_is_available(self):
        """Same shape as `fare_facts_consistent`: a run that cannot supply the
        corpus falls back to previous behaviour rather than failing every case
        against an empty source."""
        case = {"id": "edge-052", "question": "q", "expected_behavior": "answer"}
        assert "office_hours_in_cited_source" not in _by_name(
            run_checks(case, _answered(self.EDGE_052), DOC_IDS)
        )

    def test_the_check_is_dormant_when_the_answer_cites_nothing_known(self):
        case = {"id": "edge-052", "question": "q", "expected_behavior": "answer"}
        checks = _by_name(
            run_checks(
                case,
                _answered(self.EDGE_052),
                DOC_IDS,
                doc_texts={"some-other-doc": "8:00 AM"},
            )
        )
        assert "office_hours_in_cited_source" not in checks


class TestFareAmountsInCitedSource:
    """Issue #195. A dollar amount the answer publishes must appear in a
    document the answer cites."""

    SCMTD_HIGHWAY_17 = (
        "Amtrak/Highway 17 Express\n"
        "Children and Adults (age 64 and under)\n"
        "$7.00 Cash/1 Ride\n"
        "$14\n"
        "$145\n"
        "$3.50 Cash/1 Ride\n"
    )

    def _checks(self, answer: str, doc_text: str, case_extra: dict | None = None):
        case = {
            "id": "ground-035",
            "question": "How much does the Amtrak/Highway 17 Express cost?",
            "expected_behavior": "answer",
            **(case_extra or {}),
        }
        return _by_name(
            run_checks(case, _answered(answer), DOC_IDS, doc_texts={"mst-fares": doc_text})
        )

    def test_the_ground_035_defect_fails(self):
        """The live 2026-09-04 answer, reduced to its arithmetic: the chunker
        dropped the discount row's pass cells, and the model filled them in by
        halving the adult column. `$72.50` is in no corpus version."""
        answer = (
            "Discount Fare (adults age 65 and over): $3.50 Cash/1 Ride, "
            "$7 Day Pass, $72.50 31-Day Pass [doc:mst-fares]. "
            "Based on policies published as of 2026-06-12."
        )
        check = self._checks(answer, self.SCMTD_HIGHWAY_17)["fare_amounts_in_cited_source"]
        assert not check.passed
        assert check.detail == "$72.50"

    def test_amounts_the_cited_document_publishes_pass(self):
        """The negative control. A check that fired on every stated price would
        make a fare answer unwritable."""
        answer = (
            "The Highway 17 Express is $7.00 for a single ride, or $145 for a "
            "31-Day Pass [doc:mst-fares]. Based on policies published as of 2026-06-12."
        )
        assert self._checks(answer, self.SCMTD_HIGHWAY_17)["fare_amounts_in_cited_source"].passed

    def test_a_flattened_table_cell_without_its_dollar_sign_still_counts(self):
        """Fare tables lose the currency symbol when a cell is flattened into
        text. Reading the document more strictly than the ingest wrote it would
        report the cleaner as an assistant defect."""
        answer = "The 31-Day Pass is $65.00 [doc:mst-fares]. As of 2026-06-12."
        doc = "Local Service\nDay Pass\n6\n31-Day Pass\n65.00\n"
        assert self._checks(answer, doc)["fare_amounts_in_cited_source"].passed

    def test_a_saving_derived_from_two_published_prices_is_allowed(self):
        """`ground-samtrans-001`'s shape: both prices are published, and the
        difference between them is arithmetic the answer is entitled to state."""
        answer = (
            "An adult fare is $2.25 with cash, or $2.05 with Clipper "
            "[doc:mst-fares]. So Clipper is cheaper — you save $0.20 per ride. "
            "As of 2026-06-12."
        )
        doc = "Adult\n$2.25 cash\n$2.05 Clipper\n"
        assert self._checks(answer, doc)["fare_amounts_in_cited_source"].passed

    def test_the_exemption_is_about_comparison_not_about_arithmetic(self):
        """The hole this check would have if the exemption were "any computed
        figure": #195's `$72.50` *is* arithmetic on a published number. It is
        published as a price, with no comparison beside it, so it must fail —
        and a comparison elsewhere in a long answer must not launder it."""
        answer = (
            "An adult fare is $2.25 with cash, or $2.05 with Clipper, so you "
            "save $0.20 per ride [doc:mst-fares]. The discount 31-Day Pass "
            "costs $72.50. As of 2026-06-12."
        )
        doc = "Adult\n$2.25 cash\n$2.05 Clipper\n"
        check = self._checks(answer, doc)["fare_amounts_in_cited_source"]
        assert not check.passed
        assert check.detail == "$72.50"

    def test_an_amount_the_rider_supplied_is_not_a_sourced_claim(self):
        answer = (
            "You asked about a $35 pass; the published policy does not list one "
            "[doc:mst-fares]. As of 2026-06-12."
        )
        checks = self._checks(
            answer,
            "No pass prices published.",
            {"question": "Is the $35 monthly pass worth it for me?"},
        )
        assert checks["fare_amounts_in_cited_source"].passed

    def test_the_check_is_dormant_when_no_document_text_is_available(self):
        case = {"id": "ground-035", "question": "q", "expected_behavior": "answer"}
        assert "fare_amounts_in_cited_source" not in _by_name(
            run_checks(case, _answered("The fare is $72.50 [doc:mst-fares]."), DOC_IDS)
        )

    def test_the_check_is_dormant_when_the_answer_cites_nothing_known(self):
        case = {"id": "ground-035", "question": "q", "expected_behavior": "answer"}
        checks = _by_name(
            run_checks(
                case,
                _answered("The fare is $72.50 [doc:mst-fares]."),
                DOC_IDS,
                doc_texts={"some-other-doc": "$72.50"},
            )
        )
        assert "fare_amounts_in_cited_source" not in checks


class TestMoneyAmountParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("The fare is $2.50", {2.5}),
            ("$7 Day Pass and $ 14 for two", {7.0, 14.0}),
            ("A $1,234.56 annual pass", {1234.56}),
            ("Route 24 leaves at 8:05", set()),
            ("Seniors are 65 and over", set()),
        ],
    )
    def test_an_answer_reads_only_marked_currency(self, text, expected):
        assert money_amounts(text) == expected

    def test_a_source_document_also_reads_a_flattened_cell(self):
        assert money_amounts("Day Pass\n6\n31-Day\n65.00", source=True) == {65.0}
        assert money_amounts("Day Pass\n6\n31-Day\n65.00") == set()

    def test_unsourced_lists_only_what_no_cited_document_carries(self):
        assert unsourced_fare_amounts(
            "It is $7.00 for a ride and $72.50 for a pass",
            ["$7.00 Cash/1 Ride\n$145"],
        ) == ["$72.50"]


class TestClockTimeNormalisation:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Open 8:00 AM to 5:00 PM", {(8, 0, "a"), (5, 0, "p")}),
            ("Open 12:00 AM", {(0, 0, "a")}),
            ("Open 12 p.m.", {(0, 0, "p")}),
            ("until 1.a.m. the day after", {(1, 0, "a")}),
            ("$1.00 a month", set()),
            ("Route 24 pm", set()),
            ("call 916-321-BUSS", set()),
        ],
    )
    def test_parsing(self, text, expected):
        assert clock_times(text) == expected

    def test_noon_and_midnight_do_not_collide(self):
        assert clock_times("12:00 AM") != clock_times("12:00 PM")

    def test_unsupported_lists_only_what_no_cited_document_carries(self):
        assert unsupported_clock_times(
            "Open 8:00 AM to 5:00 PM",
            ["We open at 8 a.m."],
        ) == ["5:00 pm"]
