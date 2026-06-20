"""Answer pipeline: guards → retrieve → prompt → model → citation extraction → guards.

The result object carries the full trace (question, passages, raw answer, guard
flags) because the eval report shows failures end to end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from assistant import config, guards
from assistant.models import Model, get_model
from assistant.retrieve import Retriever, ScoredChunk, default_retriever

# Citations render as clickable links in the browser. Corpus URLs are
# operator-controlled and always https, but defend in depth at the point an
# answer leaves the server: a non-http(s) scheme (javascript:, data:) would run
# on click, so anything else is dropped to an empty href.
_SAFE_URL = re.compile(r"^https?://", re.I)


def _safe_url(url: str) -> str:
    return url if _SAFE_URL.match(url) else ""


@dataclass
class Citation:
    doc_id: str
    agency: str
    title: str
    url: str
    fetch_date: str


@dataclass
class AnswerResult:
    question: str
    answer: str
    kind: str  # "answered" | "refused_input" | "refused_no_support"
    citations: list[Citation] = field(default_factory=list)
    passages: list[ScoredChunk] = field(default_factory=list)
    guard_flags: list[str] = field(default_factory=list)
    model: str = ""
    as_of_date: str = ""
    # Retrieval confidence, an operational signal for staff and integrators
    # (persona research F-16). `retrieval_score` is the top passage's score;
    # `confidence` is its band ("low" when the assistant declined for lack of
    # support, "medium"/"high" on an answered response). It never changes the
    # answer text or the guard behavior.
    retrieval_score: float = 0.0
    confidence: str = ""
    # Token usage of the answer model call (0 when no model was called, e.g.
    # an input-guard refusal or a low-confidence decline). Eval runs aggregate
    # these into a per-run cost estimate.
    input_tokens: int = 0
    output_tokens: int = 0
    # When the output guard replaces an answer, the original model text is
    # kept here so eval traces show what was actually blocked. Never shown
    # to riders.
    raw_model_answer: str = ""


def _format_passages(results: list[ScoredChunk]) -> str:
    blocks = []
    for sc in results:
        c = sc.chunk
        blocks.append(
            f"[doc:{c.doc_id}] {c.agency_full} — {c.doc_title} — {c.section}\n"
            f"(source: {c.url}, fetched {c.fetch_date})\n{c.text}"
        )
    return "\n\n".join(blocks)


def _no_support_message(agency_hint: str | None, lang: str = "en") -> str:
    if lang == "es":
        where = (
            "el sitio web o el servicio al cliente de la agencia"
            if agency_hint
            else f"su agencia de tránsito directamente, o {config.STATEWIDE_TRANSIT_INFO}"
        )
        return (
            "No tengo un documento de política publicado que responda eso, y no "
            f"voy a adivinar sobre tarifas o elegibilidad. Consulte {where} para "
            "obtener información actualizada."
        )
    where = "the agency's website or customer service" if agency_hint else (
        f"your transit agency directly, or {config.STATEWIDE_TRANSIT_INFO}"
    )
    return (
        "I don't have a published policy document that answers that, and I won't "
        f"guess about fares or eligibility. Please check {where} for current "
        "information."
    )


def _retrieval_query(question: str, history: list[tuple[str, str]] | None) -> str:
    """Carry the prior user turn into retrieval so a follow-up that names no
    agency ("what about my spouse?") inherits the earlier turn's context."""
    if not history:
        return question
    prev_user = history[-1][0]
    return f"{prev_user} {question}"


def _history_block(history: list[tuple[str, str]] | None) -> str:
    """Render prior turns as context prepended to the answer prompt. Empty when
    there is no history, so single-shot questions are unchanged."""
    if not history:
        return ""
    turns = []
    for user_q, assistant_a in history:
        turns.append(f"Rider: {user_q}\nYou answered: {assistant_a}")
    joined = "\n\n".join(turns)
    return (
        "Earlier in this conversation (context only — re-ground every claim in "
        "the passages below, and resolve references like \"it\" or \"my spouse\" "
        f"against these turns):\n\n{joined}\n\n"
    )


def _confidence_band(top_score: float, rcfg: config.RetrievalConfig) -> str:
    """Map a top retrieval score to a coarse band. Below min_confidence the
    pipeline declines, so an answered response is "medium" or "high"."""
    if top_score < rcfg.min_confidence:
        return "low"
    return "high" if top_score >= rcfg.confidence_high else "medium"


def answer_question(
    question: str,
    *,
    history: list[tuple[str, str]] | None = None,
    model: Model | None = None,
    retriever: Retriever | None = None,
    cfg: config.Config | None = None,
) -> AnswerResult:
    cfg = cfg or config.Config()
    retriever = retriever or default_retriever()

    pre = guards.check_input(question)
    if not pre.ok:
        return AnswerResult(
            question=question,
            answer=pre.message or "",
            kind="refused_input",
            guard_flags=pre.flags,
        )

    lang = guards.detect_language(question)
    results = retriever.search(_retrieval_query(question, history))
    as_of = max((sc.chunk.fetch_date for sc in results), default="")
    top_score = results[0].score if results else 0.0
    # Band from the retriever's own config, the same threshold confident() uses,
    # so an answered response is never labeled "low".
    band = _confidence_band(top_score, retriever.cfg)
    if not retriever.confident(results):
        from assistant.retrieve import detect_agency

        return AnswerResult(
            question=question,
            answer=_no_support_message(detect_agency(question), lang),
            kind="refused_no_support",
            passages=results,
            as_of_date=as_of,
            retrieval_score=top_score,
            confidence=band,
        )

    model = model or get_model(cfg.models.provider, cfg.models.answer_model)
    system = config.load_prompt("system")
    user = _history_block(history) + config.load_prompt("answer_user").format(
        passages=_format_passages(results),
        as_of_date=as_of,
        question=question,
    )
    completion = model.complete(
        system=system,
        user=user,
        max_tokens=cfg.models.max_tokens,
        temperature=cfg.models.temperature,
    )

    text = completion.text
    guard_flags: list[str] = []
    post = guards.check_output(text)
    if not post.ok and any(f.startswith("determination_language") for f in post.flags):
        # First try dropping just the offending sentences; a good answer that
        # quotes a forbidden phrase keeps its cited content.
        redacted = guards.redact_determination_language(text)
        if redacted and guards.check_output(redacted).ok:
            guard_flags = [f"redacted_{f}" for f in post.flags]
            text = redacted
            post = guards.check_output(text)

    if not post.ok:
        # Enforcement, not just measurement: an answer that decides eligibility
        # or carries no citation never reaches the rider. The flags stay on the
        # result so eval reports show how often this tripped.
        return AnswerResult(
            question=question,
            answer=_no_support_message(None, lang),
            kind="answered_guarded",
            passages=results,
            guard_flags=post.flags,
            model=completion.model,
            as_of_date=as_of,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            raw_model_answer=completion.text,
            retrieval_score=top_score,
            confidence=band,
        )

    cited_ids = set(guards.CITATION_RE.findall(text))
    by_id = {sc.chunk.doc_id: sc.chunk for sc in results}
    citations = [
        Citation(
            doc_id=doc_id,
            agency=by_id[doc_id].agency,
            title=by_id[doc_id].doc_title,
            url=_safe_url(by_id[doc_id].url),
            fetch_date=by_id[doc_id].fetch_date,
        )
        for doc_id in sorted(cited_ids)
        if doc_id in by_id
    ]
    return AnswerResult(
        question=question,
        answer=text,
        kind="answered",
        citations=citations,
        passages=results,
        guard_flags=guard_flags,
        model=completion.model,
        as_of_date=as_of,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        raw_model_answer=completion.text if guard_flags else "",
        retrieval_score=top_score,
        confidence=band,
    )
