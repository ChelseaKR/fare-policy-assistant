from assistant.retrieve import detect_agency


class TestAgencyDetection:
    def test_acronym(self):
        assert detect_agency("How much is the MST senior fare?") == "MST"

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
