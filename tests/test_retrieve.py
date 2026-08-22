import pytest

from assistant import config
from assistant import retrieve as retrieve_module
from assistant.retrieve import (
    Retriever,
    _enumeration_topic,
    _expand_query,
    _is_application_passage,
    _is_child_fare_query,
    _is_eligibility_criterion_passage,
    _is_enumeration_query,
    _is_reduced_fare_query,
    detect_agencies,
    detect_agency,
)


class TestAgencyDetection:
    def test_acronym(self):
        assert detect_agency("How much is the MST senior fare?") == "MST"

    def test_multiple_agencies(self):
        found = detect_agencies("Is the senior discount age the same on MST and Yolobus?")
        assert set(found) == {"MST", "Yolobus"}

    def test_order_depends_on_text_not_alias_mapping_insertion(self):
        aliases = {"mst": "MST", "sbmtd": "SBMTD"}
        reversed_aliases = dict(reversed(list(aliases.items())))
        question = "Compare SBMTD with MST fares."

        assert detect_agencies(question, aliases=aliases) == ["SBMTD", "MST"]
        assert detect_agencies(question, aliases=reversed_aliases) == ["SBMTD", "MST"]

    def test_explicit_empty_alias_mapping_detects_nothing(self):
        assert detect_agencies("MST fare", aliases={}) == []

    def test_spanish_query_expansion(self):
        expanded = _expand_query(["pasaje", "reducido", "yolobus"])
        assert "fare" in expanded and "reduced" in expanded
        # Original tokens are preserved so same-language matching still works.
        assert "pasaje" in expanded

    def test_alias(self):
        assert detect_agency("senior discount in santa barbara") == "SBMTD"

    def test_spanish_question(self):
        assert detect_agency("¿Cuánto cuesta el pasaje en Yolobus?") == "Yolobus"

    def test_unknown_agency(self):
        assert detect_agency("How much is a senior fare on LA Metro?") is None

    def test_tagalog_query_expansion(self):
        # R2-3 retrieval half: a Tagalog fare query expands into the English
        # vocabulary the corpus uses.
        expanded = _expand_query(["magkano", "ang", "diskwento", "para", "nakatatanda"])
        assert "discount" in expanded and "senior" in expanded
        # Original tokens preserved.
        assert "diskwento" in expanded


class TestRetriever:
    def test_relevant_chunk_ranks_first(self, retriever):
        results = retriever.search("Do youth ride free on Yolobus?")
        assert results[0].chunk.doc_id == "yolobus-fares"

    def test_agency_filter(self, retriever):
        results = retriever.search("senior discount on MST")
        assert all(sc.chunk.agency == "MST" for sc in results)

    def test_low_confidence_on_offtopic(self, retriever):
        q = "weather forecast astronomy parliament"
        results = retriever.search(q)
        assert not retriever.confident(q, results)

    def test_multi_agency_retrieval_surfaces_each(self, retriever):
        # R3-2 retrieval half: a question naming two agencies retrieves passages
        # from each, rather than letting one agency take every slot.
        results = retriever.search("Compare senior fares on MST and Yolobus")
        agencies = {sc.chunk.agency for sc in results}
        assert {"MST", "Yolobus"} <= agencies

    def test_tagalog_query_retrieves_the_right_passage(self, retriever):
        # R2-3 retrieval half: a Tagalog query for the MST discount surfaces the
        # MST discount passage, via the lexicon expansion (no model involved).
        results = retriever.search("Magkano ang diskwento sa MST?")
        assert results[0].chunk.agency == "MST"
        assert results[0].chunk.chunk_id == "mst-fares#0"

    def test_cash_fare_query_keeps_single_ride_price_ahead_of_cash_handling(self, retriever):
        results = retriever.search("Magkano ang pamasahe sa MST kung babayad ako ng cash?")
        assert results[0].chunk.chunk_id == "mst-fares#0"


class TestCloseTheLoopTriggers:
    """R1-2 heuristics: the query trigger and the application-passage signal."""

    @pytest.mark.parametrize(
        "question",
        [
            "What is the senior discount fare on Yolobus?",
            "I have a disability — how do I get the reduced fare?",
            "Where do I apply for a Medicare discount ID card?",
            "youth fare on SacRT",
        ],
    )
    def test_reduced_fare_queries_trigger(self, question):
        assert _is_reduced_fare_query(question)

    def test_plain_fare_query_does_not_trigger(self):
        assert not _is_reduced_fare_query("What is the regular adult cash fare?")

    def test_application_passage_signal_matches_real_sections(self):
        from assistant.ingest import load_chunks

        by_id = {c.chunk_id: c for c in load_chunks()}
        # Real where-to-apply passages keyed from corpus/processed/chunks.jsonl.
        assert _is_application_passage(by_id["sbmtd-fares-passes#3"])  # Mobility Pass
        assert _is_application_passage(by_id["mst-fares#6"])  # Courtesy Cards
        assert _is_application_passage(by_id["yolobus-reduced-fare-id#0"])  # obtain ID
        # A plain fare table is not an application passage.
        assert not _is_application_passage(by_id["hta-fares#0"])


class TestCloseTheLoopRetrieval:
    """R1-2: a reduced-fare query must deliver the where-to-apply passage to the
    answer prompt, even when the fare/eligibility passages win the top slots."""

    @pytest.fixture
    def corpus_retriever(self):
        # Real corpus with a tight top_k so the fare passages fill the ranked
        # slots and the application passage would fall out without the companion
        # step — the exact condition R1-2 fixes.
        return Retriever(cfg=config.RetrievalConfig(use_dense=False, top_k=3))

    def test_companion_application_chunk_appears_for_reduced_fare_query(self, corpus_retriever):
        results = corpus_retriever.search("I am 65 and disabled, what do I pay on Yolobus?")
        ids = [sc.chunk.chunk_id for sc in results]
        # The where-to-apply passage (Woodland office, hours) is appended.
        assert "yolobus-reduced-fare-id#0" in ids
        assert any(_is_application_passage(sc.chunk) for sc in results)

    def test_companion_respects_agency_scope(self, corpus_retriever):
        results = corpus_retriever.search("How do I get the SBMTD reduced-fare Mobility Pass ID?")
        assert any(sc.chunk.chunk_id == "sbmtd-fares-passes#3" for sc in results)
        # No cross-agency application passage leaks in.
        assert all(sc.chunk.agency == "SBMTD" for sc in results)

    def test_no_companion_appended_for_plain_fare_query(self, corpus_retriever):
        results = corpus_retriever.search("What is the regular adult fare on Yolobus?")
        assert "yolobus-reduced-fare-id#0" not in [sc.chunk.chunk_id for sc in results]

    def test_companion_not_duplicated_when_already_present(self, corpus_retriever):
        # When the application passage already ranks in top_k, it is not added twice.
        results = corpus_retriever.search("How do I get a reduced-fare photo ID for Yolobus?")
        ids = [sc.chunk.chunk_id for sc in results]
        assert ids.count("yolobus-reduced-fare-id#0") == 1

    def test_veteran_query_excludes_disabled_courtesy_card_process(self, corpus_retriever):
        results = corpus_retriever.search(
            "¿Qué prueba de servicio necesito para la tarifa de veterano en MST?"
        )
        ids = [sc.chunk.chunk_id for sc in results]
        assert "mst-fares-es#6" not in ids
        assert "mst-fares-es#2" in ids

    def test_senior_query_excludes_disabled_courtesy_card_process(self, corpus_retriever):
        results = corpus_retriever.search("How do I use the MST senior discount?")
        assert "mst-fares#6" not in [sc.chunk.chunk_id for sc in results]


class TestChildFareCompanion:
    """sens-010a: a child free-fare query must reach the provision passage even
    when the child's age shares almost no vocabulary with the fare table (SBMTD
    publishes it as "Children under 45 inches tall")."""

    @pytest.fixture
    def corpus_retriever(self):
        return Retriever(cfg=config.RetrievalConfig(use_dense=False))

    @pytest.mark.parametrize(
        "question",
        [
            "Does my 3-year-old ride free on Santa Barbara MTD?",
            "Is my toddler free on SBMTD?",
            "does my baby ride free on SBMTD",
        ],
    )
    def test_child_fare_queries_trigger(self, question):
        assert _is_child_fare_query(question)

    def test_senior_and_veteran_queries_do_not_trigger(self):
        assert not _is_child_fare_query("How much is the senior fare on MST?")
        assert not _is_child_fare_query("What proof do I need for the veteran fare on MST?")

    def test_provision_passage_appended_for_child_query(self, corpus_retriever):
        results = corpus_retriever.search("Does my 3-year-old ride free on Santa Barbara MTD?")
        ids = [sc.chunk.chunk_id for sc in results]
        # sbmtd-fares-passes#1 carries "FREE Children under 45 inches tall" and
        # ranks ~#37 on this query without the companion step.
        assert "sbmtd-fares-passes#1" in ids

    def test_no_provision_appended_for_plain_adult_query(self, corpus_retriever):
        results = corpus_retriever.search("What is the regular adult fare on SBMTD?")
        # The plain fare query is not a child-fare query, so nothing is forced in
        # beyond ordinary ranking; the companion step stays silent.
        assert not _is_child_fare_query("What is the regular adult fare on SBMTD?")
        assert results  # sanity: ordinary retrieval still returns passages


class TestEligibilityCriterionCompanion:
    """Issue #150/#138: an agency's own age/eligibility-criterion passage must
    survive even when the same agency's payment-method chunks ("Ways to Pay",
    "Token Transit") outrank it on BM25. Reproduced against the real corpus:
    actransit-discounts#1 ("Riders aged 65 and older ... eligible for
    Senior/Disabled fares") ranks outside AC Transit's own top 8 on a plain
    single-agency query, and outside its multi-agency quota on a two-agency
    comparison — in both cases behind chunks about passes, Clipper START, and
    payment methods that share more surface vocabulary with "senior discount"
    and "pay" than the short criterion sentence does."""

    @pytest.fixture
    def corpus_retriever(self):
        return Retriever(cfg=config.RetrievalConfig(use_dense=False))

    def test_signal_matches_real_criterion_sections(self):
        from assistant.ingest import load_chunks

        by_id = {c.chunk_id: c for c in load_chunks()}
        # "aged 65 and older"
        assert _is_eligibility_criterion_passage(by_id["actransit-discounts#1"])
        assert _is_eligibility_criterion_passage(by_id["soltrans-fare-table#2"])  # "65+" tier
        # A payment-method passage that never states an age/eligibility cutoff
        # is not a criterion passage, even though it mentions "senior" cards.
        # "Ways to Pay on Tempo"
        assert not _is_eligibility_criterion_passage(by_id["actransit-fares#7"])

    def test_criterion_passage_appended_single_agency(self, corpus_retriever):
        # Without the companion step this ranks outside AC Transit's own
        # top_k=8: six payment/pass chunks outrank it on this exact wording.
        results = corpus_retriever.search(
            "I'm 70 years old. What senior discount do I get on AC Transit, "
            "and how do I pay for it?",
            agency="AC Transit",
        )
        ids = [sc.chunk.chunk_id for sc in results]
        assert "actransit-discounts#1" in ids
        assert any("65" in sc.chunk.text for sc in results)

    def test_criterion_passage_appended_per_agency_in_comparison(self, corpus_retriever):
        # xagency-actransit-001: the per-agency quota on a two-agency
        # comparison compounds the same crowding.
        results = corpus_retriever.search(
            "What age counts as a senior on AC Transit, and is it the same on SolTrans?"
        )
        ac_ids = [sc.chunk.chunk_id for sc in results if sc.chunk.agency == "AC Transit"]
        assert "actransit-discounts#1" in ac_ids

    def test_no_companion_appended_for_plain_fare_query(self, corpus_retriever):
        results = corpus_retriever.search(
            "What is the regular adult cash fare on AC Transit?", agency="AC Transit"
        )
        assert "actransit-discounts#1" not in [sc.chunk.chunk_id for sc in results]

    def test_companion_not_duplicated_when_already_present(self, corpus_retriever):
        results = corpus_retriever.search(
            "Riders aged 65 and older discount on AC Transit", agency="AC Transit"
        )
        ids = [sc.chunk.chunk_id for sc in results]
        assert ids.count("actransit-discounts#1") <= 1

    def test_does_not_pull_in_a_different_agencys_criterion(self, corpus_retriever):
        # The companion is per-agency, scoped the same way _close_the_loop is:
        # a Yolobus-only question must not gain an AC Transit chunk.
        results = corpus_retriever.search("What age counts as a senior on Yolobus?")
        assert all(sc.chunk.agency == "Yolobus" for sc in results)


class TestEnumerationRetrieval:
    """xagency-010 / issue #150: a corpus-wide enumeration question ("which
    agencies take Clipper?") names no agency, so the plain global top_k hands
    the answer model eight chunks from whichever agency matched densest —
    which structurally cannot support an answer that enumerates across
    agencies. `search()` answers these with one best chunk per agency instead.
    See ADR 0027."""

    @pytest.fixture
    def corpus_retriever(self):
        return Retriever(cfg=config.RetrievalConfig(use_dense=False))

    @pytest.mark.parametrize(
        "question",
        [
            "Which agencies in your corpus take Clipper?",
            "Which agencies offer free youth fares?",
            "What agencies accept Clipper?",
            "List all agencies that sell day passes",
            "How many agencies offer a senior discount?",
            "¿Cuáles agencias aceptan Clipper?",
            "¿Qué agencias ofrecen tarifas gratuitas para jóvenes?",
        ],
    )
    def test_enumeration_questions_trigger(self, question):
        assert _is_enumeration_query(question)

    @pytest.mark.parametrize(
        "question",
        [
            "What is the regular adult fare on Yolobus?",
            "What proof do I need for the veteran fare on MST?",
            "Is the senior discount age the same on MST and Yolobus?",
            "¿Cuánto cuesta el pasaje reducido en Yolobus?",
            "Which bus goes to the airport?",
        ],
    )
    def test_ordinary_questions_do_not_trigger(self, question):
        assert not _is_enumeration_query(question)

    def test_enumeration_spans_the_corpus_one_chunk_per_agency(self, corpus_retriever):
        results = corpus_retriever.search("Which agencies in your corpus take Clipper?")
        agencies = [sc.chunk.agency for sc in results]
        # Breadth, not depth: every agency contributes at most one passage...
        assert len(agencies) == len(set(agencies))
        # ...and the answer model sees far more of the corpus than a plain
        # top_k of 8 chunks drawn from one or two dense-matching agencies.
        assert len(agencies) > 2

    def test_enumeration_reaches_agencies_a_plain_top_k_never_shows(self, corpus_retriever):
        """The negative statement is the point: SCMTD's best Clipper passage
        says Clipper is NOT honored, and an enumerating answer needs it."""
        results = corpus_retriever.search("Which agencies in your corpus take Clipper?")
        picked = {sc.chunk.agency: sc.chunk for sc in results}
        assert "SCMTD" in picked
        assert "not honored on METRO buses" in picked["SCMTD"].text

    def test_representative_chunk_ignores_the_enumeration_scaffolding(self, corpus_retriever):
        """Regression: with the scaffolding tokens left in the ranking query,
        the rare generic verb "take" (high IDF, few chunks) decided the pick —
        WestCAT's representative chunk became a pass-purchasing page on the
        strength of "take" alone, ahead of every dedicated Clipper chunk."""
        results = corpus_retriever.search("Which agencies in your corpus take Clipper?")
        picked = {sc.chunk.agency: sc.chunk for sc in results}
        assert "WestCAT" in picked
        westcat = picked["WestCAT"]
        assert "clipper" in f"{westcat.section} {westcat.text}".lower()

    def test_scaffolding_strip_leaves_the_rider_topic(self):
        assert _enumeration_topic("Which agencies in your corpus take Clipper?") == "clipper"
        assert _enumeration_topic("¿Cuáles agencias aceptan Clipper?") == "clipper"

    def test_reported_scores_stay_on_the_original_question_scale(self, corpus_retriever):
        """The per-agency pick is ranked on the topic, but the score handed
        downstream must be the original-question score, or `confidence_signals`
        compares a topic-scale score against an original-scale background."""
        question = "Which agencies in your corpus take Clipper?"
        results = corpus_retriever.search(question)
        original = {sc.chunk.chunk_id: sc.score for sc in corpus_retriever._rank_all(question)}
        for sc in results:
            assert sc.score == pytest.approx(original[sc.chunk.chunk_id])

    def test_named_agency_still_wins_over_the_enumeration_branch(self, corpus_retriever):
        """Containment: the branch fires only when no agency is detected. A
        question that names one keeps the agency-scoped path."""
        results = corpus_retriever.search("Which agencies does MST let me transfer to?")
        assert results
        assert all(sc.chunk.agency == "MST" for sc in results)

    def test_enumeration_branch_changes_nothing_it_does_not_claim_to(self, corpus_retriever):
        """The containment assertion this repo's history demands: prove the
        branch leaves ordinary questions byte-identical, AND prove the check is
        able to fail by showing it does detect the questions it does change."""
        changed = [
            "Which agencies in your corpus take Clipper?",
            "Which transit agency in California has the best CEO?",
        ]
        unchanged = [
            "What is the regular adult fare on Yolobus?",
            "What proof do I need for the veteran fare on MST?",
            "Is the senior discount age the same on MST and Yolobus?",
            "¿Cuánto cuesta el pasaje reducido en Yolobus?",
            "I am 65 and disabled, what do I pay on Yolobus?",
        ]

        def snapshot(question):
            return [(sc.chunk.chunk_id, sc.score) for sc in corpus_retriever.search(question)]

        with_branch = {q: snapshot(q) for q in changed + unchanged}
        original = retrieve_module._is_enumeration_query
        retrieve_module._is_enumeration_query = lambda question: False
        try:
            without_branch = {q: snapshot(q) for q in changed + unchanged}
        finally:
            retrieve_module._is_enumeration_query = original

        # The check is not inert: disabling the branch really does move these.
        for question in changed:
            assert with_branch[question] != without_branch[question], question
        # And it moves nothing else.
        for question in unchanged:
            assert with_branch[question] == without_branch[question], question
