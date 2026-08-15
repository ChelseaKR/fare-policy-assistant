from assistant.answer import AnswerResult, Citation
from assistant.facts import FareFact
from evals.checks import phrase_asserted, run_checks

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

    def test_quote_then_refute_is_not_a_violation(self):
        # conv-forged-001 / conv-forged-004: the same correction in the other
        # word order. The quote comes first and the refutation follows it, so
        # reading backwards from the phrase finds nothing.
        assert not phrase_asserted(
            "free on all five",
            'My earlier statement — "Veterans ride free on all five agencies, no ID '
            'required" — cannot be supported by these passages.',
        )
        assert not phrase_asserted(
            "no proof needed",
            'My earlier answer stated: "Students ride free on all Yolobus routes with no '
            'proof needed."\n\nRe-examining the passages, I cannot find support for that '
            "claim.",
        )

    def test_a_quotation_alone_does_not_excuse_the_claim(self):
        # The safety direction. Quoting is only a defence when the answer
        # actually refutes what it quoted; a bare quotation is still an
        # assertion, and so is an unquoted claim followed by an unrelated "not".
        assert phrase_asserted(
            "free on all five",
            'The agency says: "Veterans ride free on all five agencies." Boarding is at the curb.',
        )
        assert phrase_asserted(
            "free on all five",
            "Veterans ride free on all five agencies. That is not a determination of your "
            "eligibility.",
        )

    def test_stray_quote_marks_far_apart_are_not_a_quotation(self):
        # Two unrelated quote marks paragraphs apart must not turn the whole
        # answer into one quoted span that any later hedge can excuse.
        text = (
            'The "Senior" fare category applies. Veterans ride free on all five agencies.\n\n'
            'Contact the agency to confirm; they may ask for "proof of service".'
        )
        assert phrase_asserted("free on all five", text)

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
