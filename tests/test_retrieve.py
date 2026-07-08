from assistant.retrieve import _expand_query, detect_agencies, detect_agency


class TestAgencyDetection:
    def test_acronym(self):
        assert detect_agency("How much is the MST senior fare?") == "MST"

    def test_multiple_agencies(self):
        found = detect_agencies("Is the senior discount age the same on MST and Yolobus?")
        assert set(found) == {"MST", "Yolobus"}

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
