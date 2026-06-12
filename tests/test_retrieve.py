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


class TestRetriever:
    def test_relevant_chunk_ranks_first(self, retriever):
        results = retriever.search("Do youth ride free on Yolobus?")
        assert results[0].chunk.doc_id == "yolobus-fares"

    def test_agency_filter(self, retriever):
        results = retriever.search("senior discount on MST")
        assert all(sc.chunk.agency == "MST" for sc in results)

    def test_low_confidence_on_offtopic(self, retriever):
        results = retriever.search("weather forecast astronomy parliament")
        assert not retriever.confident(results)
