"""Answer pipeline: guards → retrieve → prompt → model → citation extraction → guards.

The result object carries the full trace (question, passages, raw answer, guard
flags) because the eval report shows failures end to end.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assistant import config, guards
from assistant.models import Model, get_model
from assistant.retrieve import Retriever, ScoredChunk, default_retriever


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


def answer_question(
    question: str,
    *,
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
    results = retriever.search(question)
    as_of = max((sc.chunk.fetch_date for sc in results), default="")
    if not retriever.confident(results):
        from assistant.retrieve import detect_agency

        return AnswerResult(
            question=question,
            answer=_no_support_message(detect_agency(question), lang),
            kind="refused_no_support",
            passages=results,
            as_of_date=as_of,
        )

    model = model or get_model(cfg.models.provider, cfg.models.answer_model)
    system = config.load_prompt("system")
    user = config.load_prompt("answer_user").format(
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

    cited_ids = set(guards.CITATION_RE.findall(completion.text))
    by_id = {sc.chunk.doc_id: sc.chunk for sc in results}
    citations = [
        Citation(
            doc_id=doc_id,
            agency=by_id[doc_id].agency,
            title=by_id[doc_id].doc_title,
            url=by_id[doc_id].url,
            fetch_date=by_id[doc_id].fetch_date,
        )
        for doc_id in sorted(cited_ids)
        if doc_id in by_id
    ]

    post = guards.check_output(completion.text)
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
        )
    return AnswerResult(
        question=question,
        answer=completion.text,
        kind="answered",
        citations=citations,
        passages=results,
        model=completion.model,
        as_of_date=as_of,
    )
