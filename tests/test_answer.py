from assistant import config
from assistant.answer import answer_question
from assistant.models import Completion, MockModel


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

    def test_pii_refused_before_retrieval(self, retriever):
        result = answer_question(
            "My SSN is 123-45-6789, what's my fare?",
            model=MockModel(),
            retriever=retriever,
            cfg=_cfg(),
        )
        assert result.kind == "refused_input"
        assert not result.passages

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
