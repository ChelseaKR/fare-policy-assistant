"""LLM-as-judge scoring.

The judge model must differ from the answer model (config enforces nothing;
the runner asserts it). Judge prompts live in prompts/ and are versioned like
any other prompt. Judge outputs are JSON; parsing failures count as judge
errors, never as silent passes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from assistant import config
from assistant.answer import AnswerResult
from assistant.models import Model

_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class JudgeVerdict:
    name: str
    passed: bool | None  # None → judge errored or was skipped
    detail: str
    raw: str = ""


def _parse_json(text: str) -> dict | None:
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _passages_block(result: AnswerResult) -> str:
    return "\n\n".join(
        f"[doc:{sc.chunk.doc_id}] {sc.chunk.section}\n{sc.chunk.text}" for sc in result.passages
    )


def judge_groundedness(model: Model, result: AnswerResult, cfg: config.Config) -> JudgeVerdict:
    user = (
        f"Question: {result.question}\n\n"
        f"Retrieved passages:\n{_passages_block(result)}\n\n"
        f"Assistant answer:\n{result.answer}"
    )
    completion = model.complete(
        system=config.load_prompt("judge_groundedness"),
        user=user,
        max_tokens=512,
        temperature=0.0,
    )
    data = _parse_json(completion.text)
    if data is None or "grounded" not in data:
        return JudgeVerdict("groundedness", None, "judge returned unparseable output",
                            raw=completion.text)
    detail = data.get("reasoning", "")
    if data.get("unsupported_claims"):
        detail += " | unsupported: " + "; ".join(data["unsupported_claims"])
    return JudgeVerdict("groundedness", bool(data["grounded"]), detail, raw=completion.text)


def judge_helpfulness(
    model: Model, result: AnswerResult, expected_behavior: str, cfg: config.Config
) -> JudgeVerdict:
    user = (
        f"Question: {result.question}\n\n"
        f"Expected behavior: {expected_behavior}\n\n"
        f"Assistant answer:\n{result.answer}"
    )
    completion = model.complete(
        system=config.load_prompt("judge_helpfulness"),
        user=user,
        max_tokens=512,
        temperature=0.0,
    )
    data = _parse_json(completion.text)
    if data is None or "helpful" not in data:
        return JudgeVerdict("helpfulness", None, "judge returned unparseable output",
                            raw=completion.text)
    detail = f"score={data.get('score')} — {data.get('reasoning', '')}"
    return JudgeVerdict("helpfulness", bool(data["helpful"]), detail, raw=completion.text)
