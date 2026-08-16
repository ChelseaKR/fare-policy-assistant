"""LLM-as-judge scoring tests.

The judges call a model, so these inject a scripted model that returns canned
JSON — no network, no cost. The load-bearing property: a judge that returns
unparseable or malformed output is recorded as `passed=None` (errored/skipped),
never as a silent pass. A judge that quietly passes on a parse failure would
inflate the scoreboard, so this is a safety-of-measurement guard.
"""

from __future__ import annotations

import pytest

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
        self.last_system = None
        self.last_user = None

    def complete(self, system, user, max_tokens, temperature):
        self.last_system = system
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


class TestTheJudgeSeesWhatTheAnswerModelSaw:
    """The two suites must not score the same sentence in opposite directions.

    `prompts/system.txt` rule 4 obliges every answer to disclose the date its
    corpus was fetched, and `evals/suites/freshness.yaml` fails an answer that
    does not. That date lives in the passage header the answer model is given
    (`assistant.answer._format_passages`), and the judge's own rendering used to
    drop it — so fresh-001 disclosed "documents fetched on June 12, 2026" and
    the groundedness judge failed it for a claim no passage stated. One suite
    demanded what the other punished.

    The repair is to show the judge the provenance rather than to excuse the
    claim from scrutiny. These tests pin both halves of that: the judge sees the
    dates, and it sees nothing else new.
    """

    def test_the_judge_sees_the_fetch_date_the_answer_must_disclose(self):
        block = judges._passages_block(_result())
        assert "fetched 2026-06-12" in block
        assert "https://mst.org/fares/" in block

    def test_the_judge_block_matches_the_answer_prompt_rendering(self):
        """Byte-identical to what the answer model was given.

        A judge scoring a different rendering of the same evidence is scoring a
        different question. If `_format_passages` grows a field, this fails
        until the judge is shown it too.
        """
        from assistant.answer import _format_passages

        result = _result()
        assert judges._passages_block(result) == _format_passages(result.passages)

    def test_provenance_carries_no_fare_policy(self):
        """Widening what the judge sees must not widen what counts as support.

        The header adds identity and dates only. A price, age, or document
        requirement still has to come from the passage text, so an answer cannot
        become grounded by quoting a URL.
        """
        chunk = _chunk()
        header_only = judges._passages_block(_result()).replace(chunk.text, "")
        for policy_token in ("$", "65", "Medicare", "proof"):
            assert policy_token not in header_only


class TestGroundedness:
    def test_grounded_verdict_passes_and_carries_tokens(self):
        judge = ScriptedJudge('{"grounded": true, "reasoning": "all claims cited"}')
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is True
        assert v.model == "judge-mock"
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

    @pytest.mark.parametrize("value", ['"false"', "0", "1", "null", "[]", "{}"])
    def test_non_boolean_grounded_value_is_errored(self, value):
        judge = ScriptedJudge(f'{{"grounded": {value}, "reasoning": "bad type"}}')
        v = judges.judge_groundedness(judge, _result(), _cfg())
        assert v.passed is None
        assert "malformed" in v.detail

    def test_prompt_carries_the_passages_and_answer_to_judge(self):
        # A groundedness verdict is only meaningful if the judge actually sees the
        # retrieved passages and the answer. If the prompt dropped either, the
        # judge would be scoring nothing — a silently corrupt measurement.
        judge = ScriptedJudge('{"grounded": true, "reasoning": "ok"}')
        judges.judge_groundedness(judge, _result(), _cfg())
        assert "Seniors 65+ pay $1.00." in judge.last_user
        assert "The senior fare is $1.00 [doc:mst-fares]." in judge.last_user

    def test_uses_an_explicitly_captured_prompt_without_reloading(self, monkeypatch):
        monkeypatch.setattr(
            config,
            "load_prompt",
            lambda _name: pytest.fail("captured prompt must not be reloaded"),
        )
        judge = ScriptedJudge('{"grounded": true, "reasoning": "ok"}')
        judges.judge_groundedness(
            judge,
            _result(),
            _cfg(),
            system_prompt="captured groundedness prompt",
        )
        assert judge.last_system == "captured groundedness prompt"


class TestHelpfulness:
    def test_helpful_verdict_passes_with_score(self):
        judge = ScriptedJudge('{"helpful": true, "score": 4, "reasoning": "answers it"}')
        v = judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert v.passed is True
        assert v.model == "judge-mock"
        assert "score=4" in v.detail

    def test_unparseable_helpfulness_is_errored(self):
        judge = ScriptedJudge("looks good")
        v = judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert v.passed is None

    @pytest.mark.parametrize("value", ['"false"', "0", "1", "null", "[]", "{}"])
    def test_non_boolean_helpful_value_is_errored(self, value):
        judge = ScriptedJudge(f'{{"helpful": {value}, "score": 4, "reasoning": "bad type"}}')
        v = judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert v.passed is None
        assert "malformed" in v.detail

    def test_uses_an_explicitly_captured_prompt_without_reloading(self, monkeypatch):
        monkeypatch.setattr(
            config,
            "load_prompt",
            lambda _name: pytest.fail("captured prompt must not be reloaded"),
        )
        judge = ScriptedJudge('{"helpful": true, "score": 4}')
        judges.judge_helpfulness(
            judge,
            _result(),
            "answer",
            _cfg(),
            system_prompt="captured helpfulness prompt",
        )
        assert judge.last_system == "captured helpfulness prompt"

    def test_expected_behavior_is_passed_into_the_prompt(self):
        judge = ScriptedJudge('{"helpful": true, "score": 3}')
        judges.judge_helpfulness(judge, _result(), "refuse_redirect", _cfg())
        assert "refuse_redirect" in judge.last_user

    def test_rationale_is_passed_into_the_prompt(self):
        judge = ScriptedJudge('{"helpful": true, "score": 3}')
        judges.judge_helpfulness(
            judge,
            _result(),
            "answer",
            _cfg(),
            rationale="Rider already stated their age; do not re-ask.",
        )
        assert "Case rationale: Rider already stated their age; do not re-ask." in judge.last_user

    def test_no_rationale_omits_the_rationale_line(self):
        judge = ScriptedJudge('{"helpful": true, "score": 3}')
        judges.judge_helpfulness(judge, _result(), "answer", _cfg())
        assert "Case rationale" not in judge.last_user


# A conversation whose earlier turns carry the facts the final answer resolves
# against. Both judges must see these turns or they grade the final answer blind.
_HISTORY = [
    ("I'm 66. Do I get a discount on MST?", "Yes, seniors 65+ pay $1.00 [doc:mst-fares]."),
    ("What about my spouse?", "The senior fare applies per rider aged 65+ [doc:mst-fares]."),
]


class TestJudgeHistory:
    def test_helpfulness_prompt_carries_prior_turns(self):
        judge = ScriptedJudge('{"helpful": true, "score": 4}')
        judges.judge_helpfulness(judge, _result(), "answer", _cfg(), history=_HISTORY)
        assert "Prior conversation turns:" in judge.last_user
        assert "I'm 66. Do I get a discount on MST?" in judge.last_user
        assert "What about my spouse?" in judge.last_user

    def test_groundedness_prompt_carries_prior_turns(self):
        judge = ScriptedJudge('{"grounded": true, "reasoning": "ok"}')
        judges.judge_groundedness(judge, _result(), _cfg(), history=_HISTORY)
        assert "Prior conversation turns:" in judge.last_user
        assert "I'm 66. Do I get a discount on MST?" in judge.last_user

    def test_no_history_leaves_prompts_without_the_header(self):
        gj = ScriptedJudge('{"grounded": true, "reasoning": "ok"}')
        judges.judge_groundedness(gj, _result(), _cfg())
        assert "Prior conversation" not in gj.last_user
        assert gj.last_user.startswith("Question:")

        hj = ScriptedJudge('{"helpful": true, "score": 3}')
        judges.judge_helpfulness(hj, _result(), "answer", _cfg())
        assert "Prior conversation" not in hj.last_user
        assert hj.last_user.startswith("Question:")


class TestJudgeVerdictParsing:
    """A judge that reasons in prose and then states a verdict must be read at
    its verdict. On the 2026-08-16 full run eight groundedness judges were
    recorded as "unparseable" — four because the greedy `{.*}` span joined two
    JSON objects into invalid JSON, four because the 512-token ceiling cut the
    object off mid-string. The first class is a parsing bug and is fixed here;
    the second is a budget and is fixed by JUDGE_MAX_TOKENS."""

    def test_last_object_wins_when_the_judge_revises_itself(self):
        # The exact shape of edge-015 / fresh-005 / ground-002 / ground-026:
        # a first verdict, more thinking, then a corrected verdict below it.
        text = (
            '```json\n{"grounded": true, "unsupported_claims": [], "reasoning": "first pass"}\n'
            "```\n\nWait — the provenance line says 2026-06-12, not 2026-08-14.\n\n"
            '```json\n{"grounded": false, "unsupported_claims": ["published as of 2026-08-14"], '
            '"reasoning": "the stated fetch date is not in any provenance line"}\n```'
        )
        verdict = judges.judge_groundedness(ScriptedJudge(text), _result(), _cfg())
        assert verdict.passed is False
        assert "not in any provenance line" in verdict.detail

    def test_prose_containing_braces_does_not_break_parsing(self):
        text = (
            "The answer cites {doc:mst-fares} loosely.\n\n"
            '{"grounded": true, "unsupported_claims": [], "reasoning": "all supported"}'
        )
        verdict = judges.judge_groundedness(ScriptedJudge(text), _result(), _cfg())
        assert verdict.passed is True

    def test_braces_inside_strings_do_not_confuse_the_scanner(self):
        text = '{"grounded": false, "unsupported_claims": ["a {literal} brace"], "reasoning": "x"}'
        verdict = judges.judge_groundedness(ScriptedJudge(text), _result(), _cfg())
        assert verdict.passed is False

    def test_object_without_the_required_key_is_not_preferred(self):
        # A trailing token-usage object must not shadow the real verdict.
        text = (
            '{"grounded": true, "unsupported_claims": [], "reasoning": "ok"}\n'
            '{"note": "scoring complete"}'
        )
        verdict = judges.judge_groundedness(ScriptedJudge(text), _result(), _cfg())
        assert verdict.passed is True

    def test_truncated_object_is_still_an_error_not_a_pass(self):
        # The safety property the module docstring promises: no silent pass.
        text = 'Reasoning at length...\n```json\n{"grounded": false, "unsupported_claims": ["the'
        verdict = judges.judge_groundedness(ScriptedJudge(text), _result(), _cfg())
        assert verdict.passed is None
        assert "unparseable" in verdict.detail

    def test_helpfulness_reads_its_own_final_object(self):
        text = (
            '{"helpful": true, "score": 4, "reasoning": "first"}\n'
            '{"helpful": false, "score": 2, "reasoning": "second, on reflection"}'
        )
        verdict = judges.judge_helpfulness(ScriptedJudge(text), _result(), "answer", _cfg())
        assert verdict.passed is False
        assert "on reflection" in verdict.detail

    def test_judge_budget_allows_prose_before_the_verdict(self):
        # The judges reason before answering; 512 truncated four of them.
        assert config.JUDGE_MAX_TOKENS >= 1024
