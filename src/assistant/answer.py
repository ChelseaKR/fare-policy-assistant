"""Answer pipeline: guards → retrieve → prompt → model → citation extraction → guards.

The result object carries the full trace (question, passages, raw answer, guard
flags) because the eval report shows failures end to end.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from assistant import config, guards, i18n
from assistant.models import Model, get_model
from assistant.retrieve import ConfidenceSignals, Retriever, ScoredChunk, default_retriever

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
    # The rider-facing freshness claim, rendered verbatim as "Based on policies
    # published as of <date>" (web/index.html, web/embed.py) and mirrored into
    # the typed contract (docs/answer-contract.schema.json). On an *answered*
    # response this is `_as_of_cited(citations)` — the oldest passage the answer
    # actually stands on — not the newest passage retrieval happened to surface.
    # See `_as_of_cited` for why the oldest is the honest one.
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
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # When the output guard replaces an answer, the original model text is
    # kept here so eval traces show what was actually blocked. Never shown
    # to riders.
    raw_model_answer: str = ""


def _as_of_cited(citations: list[Citation]) -> str:
    """The rider-facing snapshot date for an answered response: the *oldest*
    fetch date among the passages the answer actually cites.

    Freshness is this assistant's core trust claim — every answer is a dated
    snapshot the rider can go and verify — so the headline date has to describe
    the evidence the answer rests on, not the retrieval that produced it. The
    retrieved set is up to `top_k` passages and only some of them are cited;
    dating the answer by the newest *retrieved* passage lets one recently
    refetched chunk, which the answer may never have used, certify a citation
    that is months older. (Corpus snapshots are refetched per document, so a
    mixed-age top-k is the normal case, not an edge case.)

    Oldest rather than newest among the cited passages for the same reason a
    chain is as strong as its weakest link: an answer that quotes a page fetched
    in June and another fetched in August is only verified as of June, because
    the June page could have changed in the interim without anyone looking. The
    per-citation "(fetched …)" lines still give the rider the exact date behind
    each individual claim.

    Fetch dates are ISO ``YYYY-MM-DD``, so lexicographic min is chronological
    min. Empty string when there are no citations, which the UI renders as no
    freshness line at all rather than a bare or invented date.
    """
    return min((c.fetch_date for c in citations), default="")


# The rider-facing freshness sentence, in the three languages the prompts
# mandate it in: "based on policies published as of <date>"
# (prompts/system.txt, prompts/answer_user.txt), "según las políticas
# publicadas al <date>", "batay sa mga patakaran na inilathala noong <date>".
# Only the date immediately following one of those lead-ins is matched. Other
# dates in an answer mean different things and must not be touched: "the corpus
# snapshot date is <date>" is the deadline-reasoning date (correctly the newest
# retrieved), and "documents fetched on <date>" is a per-document claim.
#
# `[0-9]` rather than `\d`: `\d` also matches non-ASCII decimal digits, which
# would let a fullwidth or Devanagari date through this alignment untouched.
_AS_OF_PROSE = re.compile(
    r"(?P<lead>(?:published as of|publicadas al|inilathala noong)[ \t]*\**[ \t]*)"
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE,
)


def prose_as_of_dates(text: str) -> list[str]:
    """Every date an answer renders in its rider-facing freshness sentence.

    Public because `evals.checks.as_of_prose_matches_structured` has to read the
    sentence with the *same* pattern `_align_as_of_prose` rewrites it with. A
    check that recognised a different set of phrasings than the normalizer would
    be green precisely where the normalizer had already missed something.
    """

    return [match.group("date") for match in _AS_OF_PROSE.finditer(text)]


def _align_as_of_prose(text: str, as_of: str) -> tuple[str, list[str]]:
    """Make the freshness sentence say the same date the contract carries.

    The prompt is handed `as_of_retrieved` — the newest fetch date across the
    retrieved set, which is the right date for reasoning about whether a
    published deadline has passed — and is told to render that value in the
    rider-facing "based on policies published as of <date>" line. The structured
    `as_of_date` on an answered response is `_as_of_cited(citations)`: the
    *oldest* passage the answer actually stands on. Those two are different
    numbers whenever the cited set is older than the freshest thing retrieval
    surfaced, which is the ordinary case, because documents are refetched one at
    a time.

    Nothing compared them. `as_of_matches_oldest_citation` reads the structured
    field only, so it passed while the sentence the rider reads overstated the
    freshness of the evidence. Measured on the 2026-08-22 full live run
    (`evals/runs/20260822T131246Z/results.jsonl`): 28 of the 345 answers that
    render the line disagreed with their own structured date, several of them by
    ten weeks (prose 2026-08-21 over evidence fetched 2026-06-12). Issue #163;
    issue #165 attributes two of the four Spanish parity failures to it.

    The realignment only ever moves the claim *older*, toward the evidence, and
    it is recorded as a guard flag rather than applied silently. The per-citation
    "(fetched ...)" lines are untouched and still carry each document's own date.
    """

    if not as_of:
        return text, []
    replaced: list[str] = []

    def _swap(match: re.Match[str]) -> str:
        stated = match.group("date")
        if stated == as_of:
            return match.group(0)
        replaced.append(stated)
        return f"{match.group('lead')}{as_of}"

    aligned = _AS_OF_PROSE.sub(_swap, text)
    if not replaced:
        return text, []
    return aligned, [f"as_of_prose_realigned:{stated}->{as_of}" for stated in sorted(set(replaced))]


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
    """Rider-facing decline when no published policy supports an answer.

    The bilingual text now lives in the gettext catalogs (assistant.i18n); this
    keeps the same signature and control flow — same agency-hint branch, same
    no-determination stance — so the no-support behavior is unchanged.
    """
    return i18n.no_support_message(
        i18n.get_translation(lang),
        agency_hint=agency_hint,
        statewide_info=config.statewide_transit_info(),
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
        'the passages below, and resolve references like "it" or "my spouse" '
        f"against these turns):\n\n{joined}\n\n"
    )


def _confidence_band(signals: ConfidenceSignals, rcfg: config.RetrievalConfig) -> str:
    """Map the calibrated retrieval signals (FIX-07 / ADR 0013) to a coarse
    band. Below the decline thresholds the pipeline declines, so an answered
    response is never "low"."""
    low_z = signals.z_score < rcfg.decline_z_threshold
    low_coverage = signals.term_coverage < rcfg.decline_coverage_floor
    if low_z or low_coverage:
        return "low"
    return "high" if signals.z_score >= rcfg.confidence_high_z else "medium"


def _disabled_document_ids() -> set[str]:
    """Operator kill switch for source material awaiting policy review.

    The value is intentionally read for each answer so an operator can contain
    an expired or disputed source through Lambda configuration without first
    rebuilding the corpus. Values are comma-separated manifest document IDs.
    """
    return {
        doc_id.strip()
        for doc_id in os.environ.get("FPA_DISABLED_DOC_IDS", "").split(",")
        if doc_id.strip()
    }


def answer_question(
    question: str,
    *,
    history: list[tuple[str, str]] | None = None,
    model: Model | None = None,
    retriever: Retriever | None = None,
    cfg: config.Config | None = None,
    system_prompt: str | None = None,
    answer_user_prompt: str | None = None,
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
    rq = _retrieval_query(question, history)
    results = retriever.search(rq)
    disabled_ids = _disabled_document_ids()
    blocked_results = [sc for sc in results if sc.chunk.doc_id in disabled_ids]
    if blocked_results:
        # A retrieved source that the operator has disabled may have materially
        # contributed to the answer. Do not simply drop it and let a weaker,
        # tangential passage stand in as support; fail closed until review.
        from assistant.retrieve import detect_agency

        usable_results = [sc for sc in results if sc.chunk.doc_id not in disabled_ids]
        return AnswerResult(
            question=question,
            answer=_no_support_message(detect_agency(question), lang),
            kind="refused_no_support",
            passages=usable_results,
            guard_flags=[
                f"source_disabled:{doc_id}"
                for doc_id in sorted({sc.chunk.doc_id for sc in blocked_results})
            ],
            as_of_date=max((sc.chunk.fetch_date for sc in usable_results), default=""),
            retrieval_score=usable_results[0].score if usable_results else 0.0,
            confidence="low",
        )
    # Newest fetch date across the retrieved set. This is a statement about the
    # *corpus*, not about any one answer, and it is deliberately not the value
    # the rider sees on an answered response (see `_as_of_cited`). Two uses:
    #
    #  * the prompt's "corpus snapshot date", which the model reasons against to
    #    decide whether a published deadline has already passed
    #    (prompts/answer_user.txt, prompts/system.txt) — that comparison wants
    #    the most recent thing we know, so the newest date is correct there;
    #  * the `as_of_date` of results that carry no citations at all (a decline,
    #    or an answer the output/citation guard replaced), where there is no
    #    cited evidence to date and the value describes the corpus consulted.
    as_of_retrieved = max((sc.chunk.fetch_date for sc in results), default="")
    top_score = results[0].score if results else 0.0
    # Band from the same signals confident() decides on, so an answered
    # response is never labeled "low".
    signals = retriever.confidence_signals(rq, results)
    band = _confidence_band(signals, retriever.cfg)
    if not retriever.confident(rq, results):
        from assistant.retrieve import detect_agency

        return AnswerResult(
            question=question,
            answer=_no_support_message(detect_agency(question), lang),
            kind="refused_no_support",
            passages=results,
            as_of_date=as_of_retrieved,
            retrieval_score=top_score,
            confidence=band,
        )

    model = model or get_model(cfg.models.provider, cfg.models.answer_model)
    system = system_prompt if system_prompt is not None else config.load_prompt("system")
    prompt = (
        answer_user_prompt if answer_user_prompt is not None else config.load_prompt("answer_user")
    )
    user = _history_block(history) + prompt.format(
        passages=_format_passages(results),
        as_of_date=as_of_retrieved,
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
            as_of_date=as_of_retrieved,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cache_creation_input_tokens=completion.cache_creation_input_tokens,
            cache_read_input_tokens=completion.cache_read_input_tokens,
            raw_model_answer=completion.text,
            retrieval_score=top_score,
            confidence=band,
        )

    cited_ids = set(guards.extract_citation_ids(text))
    by_id = {sc.chunk.doc_id: sc.chunk for sc in results}
    unknown_ids = sorted(cited_ids - set(by_id))
    if not cited_ids or unknown_ids:
        # A citation is an authorization boundary, not decorative metadata:
        # every model-supplied id must resolve to one of the exact passages
        # retrieved for this request. Never silently drop an invented or
        # cross-request id while keeping the response marked ``answered``.
        citation_flags = (
            ["missing_citation"]
            if not cited_ids
            else [f"unretrieved_citation:{doc_id}" for doc_id in unknown_ids]
        )
        return AnswerResult(
            question=question,
            answer=_no_support_message(None, lang),
            kind="answered_guarded",
            passages=results,
            guard_flags=guard_flags + citation_flags,
            model=completion.model,
            as_of_date=as_of_retrieved,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cache_creation_input_tokens=completion.cache_creation_input_tokens,
            cache_read_input_tokens=completion.cache_read_input_tokens,
            raw_model_answer=completion.text,
            retrieval_score=top_score,
            confidence=band,
        )
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
    # Dated by the evidence, not by the retrieval: `citations` is the exact set
    # validated against `by_id` just above, so the freshness the rider is shown
    # is the freshness of the passages the answer stands on. The prose sentence
    # is then pulled onto that same date, because the model was handed the
    # newest *retrieved* date and cannot know the cited one (issue #163).
    as_of = _as_of_cited(citations)
    text, as_of_flags = _align_as_of_prose(text, as_of)
    guard_flags = guard_flags + as_of_flags
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
        cache_creation_input_tokens=completion.cache_creation_input_tokens,
        cache_read_input_tokens=completion.cache_read_input_tokens,
        raw_model_answer=completion.text if guard_flags else "",
        retrieval_score=top_score,
        confidence=band,
    )
