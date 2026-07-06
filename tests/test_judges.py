"""LLM-as-judge scoring tests.

The judges call a model, so these inject a scripted model that returns canned
JSON — no network, no cost. The load-bearing property: a judge that returns
unparseable or malformed output is recorded as `passed=None` (errored/skipped),
never as a silent pass. A judge that quietly passes on a parse failure would
inflate the scoreboard, so this is a safety-of-measurement guard.
"""

from __future__ import annotations

from assistant import config
from assistant.answer import AnswerResult
from assistant.ingest import Chunk
from assistant.models import Completion
from assistant.retrieve import ScoredChunk
from evals import judges


class ScriptedJudge:
    def __init__(self, text: str, *, input_tokens: int = 11, output_tokens: int = 7):
        self.text = text
        self._in = input_tokens
        self._out = output_tokens
        self.last_user = None

    def complete(self, system, user, max_tokens, temperature):
        self.last_user = user
        return Completion(
            text=self.text, model="judge-mock", input_tokens=self._in, output_tokens=self._out
        )


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="mst-fares#0",
        doc_id="mst-fares",
        agency="MST",
        agency_full="Monterey-Salinas Transit",
        doc_title="Fares",
        url="https://mst.org/fares/",
        fetch_date="2026-06-12",
        language="en",
        section="Discount Eligibility",
        text="Seniors 65+ pay $1.00.",
    )


def _result(answer="The senior fare is $1.00 [doc:mst-fares].") -> AnswerResult:
    return AnswerResult(
        question="How much is the MST senior fare?",
        answer=answer,
        kind="answered",
        passages=[ScoredChunk(chunk=_chunk(), score=9.0)],
    )


def _cfg():
    return config.Config()


class TestParsing:
    def test_extracts_object_from_surrounding_prose(self):
        assert judges._parse_json('blah {"grounded": true} trailing') == {"grounded": True}

    def test_no_json_returns_none(self):
        assert judges._parse_json("no object here") is None

    def test_malformed_json_returns_none(self):
        assert judges._parse_json('{"grounded": tru}') is None

    def test_passages_block_labels_each_doc(self):
        block = judges._passages_block(_result())
        assert "[doc:mst-fares]" in block and "Seniors 65+ pay $1.00." in block


class TestGroundedness:
    def test_grounded_verdict_passes_and_carries_tokens(self):
        judge = ScriptedJudge('{"grounded": true, "reasoning": "all claims cited"}')
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is True
        assert v.input_tokens == 11 and v.output_tokens == 7
        assert "all claims cited" in v.detail

    def test_unsupported_claims_appended_to_detail(self):
        judge = ScriptedJudge(
            '{"grounded": false, "reasoning": "drift", '
            '"unsupported_claims": ["the $5 express fare"]}'
        )
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is False
        assert "unsupported: the $5 express fare" in v.detail

    def test_unparseable_output_is_errored_not_a_pass(self):
        judge = ScriptedJudge("the answer looks fine to me")
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is None  # errored/skipped, never silently True
        assert "unparseable" in v.detail

    def test_missing_grounded_key_is_errored(self):
        judge = ScriptedJudge('{"reasoning": "forgot the verdict key"}')
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is None

    def test_prompt_carries_the_passages_and_answer_to_judge(self):
        # A groundedness verdict is only meaningful if the judge actually sees the
        # retrieved passages and the answer. If the prompt dropped either, the
        # judge would be scoring nothing — a silently corrupt measurement.
        judge = ScriptedJudge('{"grounded": true, "reasoning": "ok"}')
        judges.judge_groundedness(judge, _result(), _cfg())
        assert "Seniors 65+ pay $1.00." in judge.last_user
        assert "The senior fare is $1.00 [doc:mst-fares]." in judge.last_user


class TestHelpfulness:
    def test_helpful_verdict_passes_with_score(self):
        judge = ScriptedJudge('{"helpful": true, "score": 4, "reasoning": "answers it"}')
        v = judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert v.passed is True
        assert "score=4" in v.detail

    def test_unparseable_helpfulness_is_errored(self):
        judge = ScriptedJudge("looks good")
        v = judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert v.passed is None

    def test_expected_behavior_is_passed_into_the_prompt(self):
        judge = ScriptedJudge('{"helpful": true, "score": 3}')
        judges.judge_helpfulness(judge, _result(), "refuse_redirect", _cfg())
        assert "refuse_redirect" in judge.last_user
