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


def _json_objects(text: str) -> list[dict]:
    """Every balanced, parseable JSON object in `text`, in order.

    The judge writes prose and then a fenced JSON verdict, and it does not
    always write exactly one: on the 2026-08-16 full run four judges reasoned
    to `{"grounded": true …}`, kept thinking, and emitted a corrected
    `{"grounded": false …}` below it. A greedy `\\{.*\\}` spans from the first
    brace to the last, which across two objects is not JSON at all, so a
    verdict the judge did state was recorded as "unparseable" and failed the
    case for a harness reason. Scanning for balanced objects and keeping the
    last one takes the judge's final answer, which is the one it meant.
    """
    objects: list[dict] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        candidate = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        pass
                    else:
                        if isinstance(candidate, dict):
                            objects.append(candidate)
                    start = -1
    return objects


@dataclass
class JudgeVerdict:
    name: str
    passed: bool | None  # None → judge errored or was skipped
    detail: str
    raw: str = ""
    # Provider-reported served model identity. This can differ from the
    # requested alias/profile, so eval evidence must retain both.
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def _parse_json(text: str, *, required_key: str | None = None) -> dict | None:
    """The judge's verdict object, or None when it did not state one.

    Prefers the last balanced object that carries `required_key`, so a judge
    that revises itself is read at its conclusion rather than its first draft.
    Falls back to the last object of any shape, then to the original greedy
    span, so nothing that parsed before stops parsing now.
    """
    objects = _json_objects(text)
    if required_key is not None:
        keyed = [obj for obj in objects if required_key in obj]
        if keyed:
            return keyed[-1]
    if objects:
        return objects[-1]
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _passages_block(result: AnswerResult) -> str:
    """Render the retrieved passages for the judge exactly as the answer model
    saw them, provenance header included.

    Until 2026-08-16 this dropped the `(source: …, fetched …)` line that
    `assistant.answer._format_passages` puts above every passage. The two
    suites then contradicted each other. `prompts/system.txt` rule 4 *requires*
    every answer to disclose the snapshot date, and `evals/suites/freshness.yaml`
    scores the disclosure; but the groundedness judge was shown passage text
    with the dates cut out, so the one claim the assistant is obliged to make
    about its own limits was the one claim it could never support. fresh-001
    ("How current is your MST fare information?") answered "documents fetched on
    June 12, 2026" and the judge failed it for a claim no passage stated —
    correctly, given what it was shown.

    Exempting date claims from the judge would have been the wrong repair: it
    turns an unverifiable claim into an unchecked one, and an invented fetch
    date is exactly the sort of false reassurance this assistant exists not to
    give. Showing the judge the provenance instead makes the claim checkable in
    the same way every other claim is. Nothing is relaxed: the block carries
    only doc id, section, source URL and fetch date — no price, age, document,
    or program — so a fare-policy fact still has to come from the passage text.
    """
    return "\n\n".join(
        f"[doc:{sc.chunk.doc_id}] {sc.chunk.agency_full} — {sc.chunk.doc_title} — "
        f"{sc.chunk.section}\n(source: {sc.chunk.url}, fetched {sc.chunk.fetch_date})\n"
        f"{sc.chunk.text}"
        for sc in result.passages
    )


def _history_block(history: list[tuple[str, str]] | None) -> str:
    """Render prior (user, assistant) turns for the judge. Mirrors the format
    src/assistant/answer.py::_history_block feeds the answer model. Empty string
    when there is no history, so single-turn cases are byte-identical to before."""
    if not history:
        return ""
    turns = [f"Rider: {user_q}\nAssistant answered: {answer_a}" for user_q, answer_a in history]
    joined = "\n\n".join(turns)
    return f"Prior conversation turns:\n{joined}\n\n"


def judge_groundedness(
    model: Model,
    result: AnswerResult,
    cfg: config.Config,
    history: list[tuple[str, str]] | None = None,
    *,
    system_prompt: str | None = None,
) -> JudgeVerdict:
    """Score one answer for groundedness against the passages it was given.

    Coupling worth knowing before you change either side: the judge prompt
    encodes the same "as of" rule that `assistant.answer._as_of_cited` and
    `assistant.answer._align_as_of_prose` implement, and that
    `evals.checks.as_of_matches_oldest_citation` enforces on the structured
    field. The headline date is the oldest fetch date among the *cited*
    passages, deliberately, so the freshness claim is a floor rather than an
    average. Nothing tells the judge that unless the prompt does.

    Leaving it untold is what broke the 2026-09-04 nightly. Fixing #163 moved
    the sentence onto the oldest cited date without moving the rubric, so the
    judge went on reading the line as a claim that every cited document was
    fetched that day, and failed answers for a date the guard had just
    corrected. Whoever changes the convention has to change this prompt in the
    same commit; the two are one contract in two places.
    """
    user = (
        f"{_history_block(history)}"
        f"Question: {result.question}\n\n"
        f"Retrieved passages:\n{_passages_block(result)}\n\n"
        f"Assistant answer:\n{result.answer}"
    )
    completion = model.complete(
        system=(
            system_prompt if system_prompt is not None else config.load_prompt("judge_groundedness")
        ),
        user=user,
        max_tokens=config.JUDGE_MAX_TOKENS,
        temperature=config.JUDGE_TEMPERATURE,
    )
    tok = {
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "cache_creation_input_tokens": completion.cache_creation_input_tokens,
        "cache_read_input_tokens": completion.cache_read_input_tokens,
    }
    data = _parse_json(completion.text, required_key="grounded")
    if data is None or "grounded" not in data or type(data["grounded"]) is not bool:
        return JudgeVerdict(
            "groundedness",
            None,
            "judge returned unparseable or malformed output",
            raw=completion.text,
            model=completion.model,
            **tok,
        )
    detail = data.get("reasoning", "")
    if data.get("unsupported_claims"):
        detail += " | unsupported: " + "; ".join(data["unsupported_claims"])
    return JudgeVerdict(
        "groundedness",
        data["grounded"],
        detail,
        raw=completion.text,
        model=completion.model,
        **tok,
    )


def judge_helpfulness(
    model: Model,
    result: AnswerResult,
    expected_behavior: str,
    cfg: config.Config,
    history: list[tuple[str, str]] | None = None,
    rationale: str = "",
    *,
    system_prompt: str | None = None,
) -> JudgeVerdict:
    rationale_line = f"Case rationale: {rationale}\n\n" if rationale else ""
    user = (
        f"{_history_block(history)}"
        f"Question: {result.question}\n\n"
        f"Expected behavior: {expected_behavior}\n\n"
        f"{rationale_line}"
        f"Assistant answer:\n{result.answer}"
    )
    completion = model.complete(
        system=(
            system_prompt if system_prompt is not None else config.load_prompt("judge_helpfulness")
        ),
        user=user,
        max_tokens=config.JUDGE_MAX_TOKENS,
        temperature=config.JUDGE_TEMPERATURE,
    )
    tok = {
        "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens,
        "cache_creation_input_tokens": completion.cache_creation_input_tokens,
        "cache_read_input_tokens": completion.cache_read_input_tokens,
    }
    data = _parse_json(completion.text, required_key="helpful")
    if data is None or "helpful" not in data or type(data["helpful"]) is not bool:
        return JudgeVerdict(
            "helpfulness",
            None,
            "judge returned unparseable or malformed output",
            raw=completion.text,
            model=completion.model,
            **tok,
        )
    detail = f"score={data.get('score')} — {data.get('reasoning', '')}"
    return JudgeVerdict(
        "helpfulness",
        data["helpful"],
        detail,
        raw=completion.text,
        model=completion.model,
        **tok,
    )
