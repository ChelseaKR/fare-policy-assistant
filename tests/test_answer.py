from dataclasses import replace

from assistant import config
from assistant.answer import _safe_url, answer_question
from assistant.models import Completion, MockModel


def test_safe_url_drops_non_http_schemes():
    # Defence in depth: a citation link href only ever carries http(s).
    assert _safe_url("https://mst.org/fares/") == "https://mst.org/fares/"
    assert _safe_url("http://example.org") == "http://example.org"
    assert _safe_url("javascript:alert(1)") == ""
    assert _safe_url("data:text/html,<script>") == ""
    assert _safe_url("  https://x.test") == ""  # no leading junk allowed


class ScriptedModel:
    """Returns a fixed completion; lets tests exercise the output guard."""

    def __init__(self, text: str):
        self.text = text

    def complete(self, system, user, max_tokens, temperature):
        return Completion(text=self.text, model="scripted")


def _cfg():
    return config.Config(
        models=config.ModelConfig(provider="mock", answer_model="mock", judge_model="mock")
    )


class TestAnswerPipeline:
    def test_grounded_answer_carries_citation(self, retriever):
        result = answer_question(
            "Do youth ride free on Yolobus?",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered"
        assert result.citations
        assert result.citations[0].url.startswith("https://")
        assert result.as_of_date == "2026-06-12"

    def test_answered_response_reports_confidence_band(self, retriever):
        result = answer_question(
            "Do youth ride free on Yolobus?",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.confidence in {"medium", "high"}
        assert result.retrieval_score > 0

    def test_unsupported_question_reports_low_confidence(self, chunks):
        # A retriever that declines anything below a high bar: the band on a
        # declined answer is "low".
        from assistant.retrieve import Retriever

        strict = Retriever(chunks, config.RetrievalConfig(top_k=3, decline_z_threshold=50.0))
        result = answer_question(
            "Do youth ride free on Yolobus?",
            model=MockModel(),
            retriever=strict,
            cfg=_cfg(),
        )
        assert result.kind == "refused_no_support"
        assert result.confidence == "low"

    def test_pii_refused_before_retrieval(self, retriever):
        result = answer_question(
            "My SSN is 123-45-6789, what's my fare?",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "refused_input"
        assert not result.passages

    def test_operator_disabled_source_fails_closed_before_model(self, retriever, monkeypatch):
        monkeypatch.setenv("FPA_DISABLED_DOC_IDS", "yolobus-fares")

        class ModelMustNotRun:
            def complete(self, system, user, max_tokens, temperature):
                raise AssertionError("disabled source must be contained before the model runs")

        result = answer_question(
            "How much is the local fare on Yolobus?",
            model=ModelMustNotRun(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "refused_no_support"
        assert result.confidence == "low"
        assert "source_disabled:yolobus-fares" in result.guard_flags
        assert all(sc.chunk.doc_id != "yolobus-fares" for sc in result.passages)

    def test_offtopic_refused_with_redirect(self, retriever):
        result = answer_question(
            "weather forecast astronomy parliament",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "refused_no_support"
        assert "511" in result.answer or "agency" in result.answer

    def test_spanish_offtopic_refused_in_spanish(self, retriever):
        result = answer_question(
            "¿Va a llover mañana en Salinas? Quiero saber el pronóstico del clima.",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "refused_no_support"
        assert "agencia" in result.answer

    def test_determination_sentence_redacted_content_kept(self, retriever):
        result = answer_question(
            "Do I qualify for the Yolobus senior fare discount?",
            model=ScriptedModel(
                "Yes, you qualify for the discount. "
                "The published criteria are 62 and older [doc:yolobus-fares]."
            ),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered"
        assert "you qualify" not in result.answer
        assert "62 and older" in result.answer
        assert any(f.startswith("redacted_determination") for f in result.guard_flags)
        assert "you qualify" in result.raw_model_answer

    def test_fully_offending_answer_blocked_by_guard(self, retriever):
        result = answer_question(
            "Do I qualify for the Yolobus senior fare discount?",
            model=ScriptedModel("Yes, you qualify for the discount, trust me."),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered_guarded"
        assert "you qualify" not in result.answer

    def test_uncited_answer_blocked_by_guard(self, retriever):
        result = answer_question(
            "How much is the Yolobus local fare discount?",
            model=ScriptedModel("The fare is $1.00 for seniors."),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered_guarded"
        assert "missing_citation" in result.guard_flags

    def test_unknown_only_citation_fails_closed(self, retriever):
        result = answer_question(
            "How much is the Yolobus local fare discount?",
            model=ScriptedModel(
                "The senior fare is $1.00 [doc:not-in-the-corpus], as of 2026-06-12."
            ),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered_guarded"
        assert result.citations == []
        assert "unretrieved_citation:not-in-the-corpus" in result.guard_flags
        assert "not-in-the-corpus" not in result.answer

    def test_mixed_valid_and_unknown_citations_fail_closed(self, retriever):
        result = answer_question(
            "How much is the Yolobus local fare discount?",
            model=ScriptedModel(
                "The senior fare is $1.00 [doc:yolobus-fares, doc:not-retrieved], as of 2026-06-12."
            ),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered_guarded"
        assert result.citations == []
        assert "unretrieved_citation:not-retrieved" in result.guard_flags

    def test_valid_but_nonretrieved_citation_fails_closed(self, retriever):
        result = answer_question(
            "How much is the Yolobus local fare discount?",
            model=ScriptedModel("The fare is $2.00 [doc:mst-fares], as of 2026-06-12."),
            retriever=retriever,
            cfg=_cfg(),
        )
        retrieved_ids = {sc.chunk.doc_id for sc in result.passages}
        assert "mst-fares" not in retrieved_ids
        assert result.kind == "answered_guarded"
        assert "unretrieved_citation:mst-fares" in result.guard_flags


class TestAsOfDate:
    """The rider-facing snapshot date must describe the cited evidence.

    `as_of_date` is rendered as "Based on policies published as of <date>"
    directly beneath the answer (web/index.html, web/embed.py) and is the one
    claim this assistant makes about its own limits. It used to be
    `max(fetch_date)` over the *retrieved* top-k, so a single recently
    refetched document — HTA was refetched 2026-08-10 while every other agency
    still sat at 2026-06-12 — dated an answer that stood on a two-month-older
    citation to the fresh date. Documents are refetched one at a time, so a
    mixed-age top-k is routine rather than exotic.
    """

    @staticmethod
    def _mixed_freshness_retriever(chunks):
        """The corpus fixture plus one Yolobus document refetched two months
        later than the Yolobus fare page. Snapshots are taken per document, so
        one agency's pages routinely carry different fetch dates."""
        from assistant.retrieve import Retriever

        fresh = replace(
            chunks[1],
            chunk_id="yolobus-fare-notice#0",
            doc_id="yolobus-fare-notice",
            doc_title="Fare Notices",
            url="https://yolobus.com/notices/",
            fetch_date="2026-08-10",
            section="Fare Notices",
            text=(
                "Fare notice: the day pass price is unchanged at $6.00 and paper "
                "tickets remain valid on every fixed route."
            ),
        )
        # top_k covers the whole fixture, so the fresh document is in the
        # retrieved set for any Yolobus query and the test cannot go vacuous.
        return Retriever([*chunks, fresh], config.RetrievalConfig(top_k=len(chunks) + 1))

    def test_headline_date_is_the_cited_passage_not_the_freshest_retrieved(self, chunks):
        retriever = self._mixed_freshness_retriever(chunks)
        result = answer_question(
            "Do youth ride free on Yolobus?",
            model=ScriptedModel(
                "Youth ages 0-18 ride free [doc:yolobus-fares], based on policies "
                "published as of 2026-06-12."
            ),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered"
        # Preconditions: the fresh document really was retrieved, and really was
        # not cited. Without both, this test would pass for the wrong reason.
        retrieved_dates = {sc.chunk.fetch_date for sc in result.passages}
        assert "2026-08-10" in retrieved_dates
        assert [c.doc_id for c in result.citations] == ["yolobus-fares"]
        # The rider is told the date of the page the answer stands on, not the
        # date of the page that merely turned up beside it.
        assert result.as_of_date == "2026-06-12"
        assert result.as_of_date != max(sc.chunk.fetch_date for sc in result.passages)

    def test_headline_date_is_the_oldest_of_several_cited_passages(self, chunks):
        # Weakest link: an answer resting on a June page and an August page is
        # only verified as of June, because the June page could have changed
        # since without anyone looking.
        retriever = self._mixed_freshness_retriever(chunks)
        result = answer_question(
            "Do youth ride free on Yolobus and is the Yolobus day pass price unchanged?",
            model=ScriptedModel(
                "Youth ages 0-18 ride free on Yolobus [doc:yolobus-fares] and the day pass "
                "price is unchanged [doc:yolobus-fare-notice], based on policies published "
                "as of 2026-06-12."
            ),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "answered"
        assert {c.fetch_date for c in result.citations} == {"2026-06-12", "2026-08-10"}
        assert result.as_of_date == "2026-06-12"

    def test_declined_answer_still_dates_the_corpus_it_consulted(self, chunks):
        # No citations exist on a decline, so there is no cited evidence to
        # date; the field keeps describing the corpus that was consulted.
        from assistant.retrieve import Retriever

        strict = Retriever(chunks, config.RetrievalConfig(top_k=3, decline_z_threshold=50.0))
        result = answer_question(
            "Do youth ride free on Yolobus?",
            model=MockModel(),
            retriever=strict,
            cfg=_cfg(),
        )
        assert result.kind == "refused_no_support"
        assert result.citations == []
        assert result.as_of_date == "2026-06-12"


class TestMultiTurn:
    def test_retrieval_query_inherits_prior_turn(self):
        from assistant.answer import _retrieval_query

        q = _retrieval_query("what about my spouse?", [("MST veteran discount", "...")])
        assert q == "MST veteran discount what about my spouse?"

    def test_retrieval_query_unchanged_without_history(self):
        from assistant.answer import _retrieval_query

        assert _retrieval_query("how much is the fare?", None) == "how much is the fare?"

    def test_history_block_empty_without_history(self):
        from assistant.answer import _history_block

        assert _history_block(None) == ""
        assert _history_block([]) == ""

    def test_history_block_includes_prior_turns(self):
        from assistant.answer import _history_block

        block = _history_block([("how much on MST?", "Single ride is $2.00.")])
        assert "how much on MST?" in block and "$2.00" in block
        assert "context only" in block  # framed as context, must re-ground
