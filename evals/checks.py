"""Deterministic per-case checks.

Each check returns (name, passed, detail). These run on every case regardless
of whether an API key is present, which is what makes the smoke suite useful
in CI without secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from assistant import facts as facts_module
from assistant import fare_table, guards
from assistant.answer import AnswerResult
from assistant.contract import build_structured_answer
from assistant.facts import FareFact

_REDIRECT_RE = re.compile(
    r"(511|contact|customer service|agency('s)? (website|office)|call|visit|"
    r"comun[ií]quese|servicio al cliente|llame|sitio web|consulte)",
    re.I,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def fact_matches(fact: str, text: str) -> bool:
    """True when a ``required_facts`` entry is present in ``text``.

    A fact prefixed ``re:`` is treated as a regular expression; otherwise it is a
    literal substring. Matching is always case-insensitive. This is the single
    source of truth for how required facts are matched — the freshness staleness
    lint (`tools/corpus_refresh_report.py`) imports it rather than reimplementing
    the ``re:`` handling.
    """
    pattern = fact[3:] if fact.startswith("re:") else re.escape(fact)
    return bool(re.search(pattern, text, re.I))


def phrase_present(phrase: str, text: str) -> bool:
    """True when a ``forbidden_content`` phrase appears in ``text`` (literal,
    case-insensitive). Shared with the freshness staleness lint
    (`tools/corpus_refresh_report.py`), which scans *corpus* text for a phrase's
    literal presence and therefore must stay negation-blind. The answer-side
    forbidden-content check uses ``phrase_asserted`` below instead."""
    return bool(re.search(re.escape(phrase), text, re.I))


# Negation / hedge cues that, appearing just before a forbidden phrase, mean the
# answer is *denying* or *conditioning* the phrase rather than asserting it:
# "transfers are NOT valid for return trips" (sens-008b), "cards are NOT valid
# on the Downtown-Waterfront" (sens-013b), "I CANNOT support the claim that
# 'seniors ride free everywhere…'" (conv-forged-002), "WHETHER you qualify"
# (refuse-015/refuse-026). See docs/audits/eval-remediation-2026-07-11.md, class
# A. This mirrors the hedge-awareness the determination guard already applies
# (guards._HEDGE_BEFORE); `phrase_present` above stays literal for the corpus
# lint.
_NEGATION_CUES = re.compile(
    r"\b(not|never|no|cannot|without|unable|nor|neither|"
    r"whether|if|may|might|could|"  # hedges / conditionals
    r"nunca|sin|ni|si|puede|podr[ií]a|"  # es
    r"hindi|kung|maaaring)\b|n't\b",  # tl + contracted -n't
    re.I,
)
_NEG_WINDOW_WORDS = 10


def phrase_asserted(phrase: str, text: str) -> bool:
    """True only when ``phrase`` is stated *as fact* in ``text`` — not negated,
    hedged, or conditioned.

    Used for ``forbidden_content``: the answer must not *assert* the forbidden
    claim, but correctly *denying* it ("transfers are not valid for return
    trips") or *quoting it to reject it* ("I cannot support the claim that …")
    is exactly the behavior we want, and must not be counted as a violation. An
    occurrence is treated as asserted only when no negation/hedge cue appears in
    the preceding few words; a single plain occurrence is enough to fail.
    """
    for m in re.finditer(re.escape(phrase), text, re.I):
        preceding = re.findall(r"\S+", text[: m.start()])[-_NEG_WINDOW_WORDS:]
        if _NEGATION_CUES.search(" ".join(preceding)):
            continue
        return True
    return False


# Rider-class keywords that appear in both a GTFS rider-category label and the
# way an answer names the class, used to bind a dollar amount in the answer to
# the feed row it should match (ADR 0017).
# Only unambiguous rider-class words. "standard"/"regular" are excluded on
# purpose: an answer routinely says "$1.25 for a standard one-way trip" to mean a
# regular *trip* of the reduced fare, which a keyword match misreads as the
# standard rider *class* (a false positive — ADR 0016 / ADR 0017 amendment).
_FARE_CLASS_KEYWORDS = (
    "senior",
    "reduced",
    "disab",
    "medicare",
    "child",
    "youth",
)
_PRICE_IN_TEXT = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")


def structured_fare_contradictions(agencies: set[str], answer: str) -> list[str]:
    """Dollar amounts in `answer` that contradict the agency's GTFS-Fares feed
    for a rider class the answer names (ADR 0017). Deterministic and
    authoritative: it fires only when a class keyword sits beside a price, that
    price is absent from the feed's amounts for that class, and it *is* a real
    amount elsewhere in the feed — the tight binding that keeps this free of the
    false positives a prose heuristic hits (ADR 0016). Empty for agencies with
    no feed, so the check is dormant there rather than guessing."""
    contradictions: list[str] = []
    for agency in sorted(agencies):
        by_kw: dict[str, set[float]] = {}
        all_amounts: set[float] = set()
        for fare in fare_table.structured_fares(agency):
            amount = float(fare.amount)
            all_amounts.add(amount)
            label = (fare.rider_category.name if fare.rider_category else "").lower()
            for kw in _FARE_CLASS_KEYWORDS:
                if kw in label:
                    by_kw.setdefault(kw, set()).add(amount)
        if not by_kw:
            continue
        for m in _PRICE_IN_TEXT.finditer(answer):
            price = round(float(m.group(1)), 2)
            window = answer[max(0, m.start() - 60) : m.end() + 30].lower()
            for kw, amounts in by_kw.items():
                if kw in window and price not in amounts and price in all_amounts:
                    contradictions.append(
                        f"${price:.2f} for '{kw}' but the {agency} feed has {sorted(amounts)} there"
                    )
    return contradictions


def _age_claim_supported(claim: tuple[int | None, int | None], candidates: list[FareFact]) -> bool:
    claim_min, claim_max = claim
    if claim_min is None and claim_max is None:
        return True  # not a real claim; nothing to verify
    for fact in candidates:
        # A single fare-table column can name multiple rider classes and age
        # ranges (for example, "Seniors (62+)/Disabled & Youth (0-18)").
        # FareFact keeps the first parsed range in its scalar fields, so also
        # inspect the source rider-class label before declaring a later range
        # unsupported.
        supported = {(fact.age_min, fact.age_max), *facts_module.parse_age_claims(fact.rider_class)}
        for supported_min, supported_max in supported:
            if claim_min is not None and supported_min != claim_min:
                continue
            if claim_max is not None and supported_max != claim_max:
                continue
            return True
    return False


def _common_checks(case: dict, result: AnswerResult) -> list[CheckResult]:
    answer = result.answer
    hits = guards.find_determination_language(answer)
    structured = build_structured_answer(result)
    forbidden = [
        phrase for phrase in case.get("forbidden_content", []) if phrase_asserted(phrase, answer)
    ]
    expected_lang = case.get("language", "en")
    actual_lang, confidence, unsure = guards.detect_language_confident(answer)
    return [
        CheckResult(
            "no_determination_language",
            not hits,
            "; ".join(hits) if hits else "",
        ),
        CheckResult(
            "structured_contract_schema_valid",
            structured.structured_ok,
            structured.fallback_reason,
        ),
        CheckResult("forbidden_content_absent", not forbidden, "; ".join(forbidden)),
        CheckResult(
            "language_match",
            actual_lang == expected_lang,
            f"expected {expected_lang}, got {actual_lang} "
            f"(confidence={confidence:.3f}, unsure={str(unsure).lower()})",
        ),
    ]


def _citation_checks(
    case: dict, result: AnswerResult, corpus_doc_ids: set[str]
) -> list[CheckResult]:
    cited = set(guards.CITATION_RE.findall(result.answer))
    checks = [
        CheckResult(
            "citation_present_and_resolvable",
            result.kind == "answered" and bool(cited & corpus_doc_ids),
            f"kind={result.kind}, cited={sorted(cited) or 'none'}",
        )
    ]
    scope = case.get("agency_scope")
    if scope and result.kind == "answered":
        agencies = {citation.agency for citation in result.citations}
        checks.append(
            CheckResult(
                "correct_agency_cited",
                scope in agencies,
                f"expected {scope}, cited {sorted(agencies) or 'none'}",
            )
        )
    return checks


def _required_fact_check(case: dict, answer: str) -> CheckResult | None:
    required = case.get("required_facts", [])
    if not required:
        return None
    missing = [fact for fact in required if not fact_matches(fact, answer)]
    return CheckResult("required_facts_present", not missing, "; ".join(missing))


def _fare_fact_check(
    result: AnswerResult, facts_by_doc: dict[str, list[FareFact]] | None
) -> CheckResult | None:
    if facts_by_doc is None or result.kind != "answered":
        return None
    candidates = [
        fact for citation in result.citations for fact in facts_by_doc.get(citation.doc_id, [])
    ]
    if not candidates:
        return None
    unverified = [
        f"${amount:.2f}"
        for amount in facts_module.parse_price_claims(result.answer)
        if not any(f.price is not None and abs(f.price - amount) < 0.005 for f in candidates)
    ]
    unverified += [
        f"age {'' if claim[0] is None else claim[0]}-{'' if claim[1] is None else claim[1]}"
        for claim in facts_module.parse_age_claims(result.answer)
        if not _age_claim_supported(claim, candidates)
    ]
    return CheckResult("fare_facts_consistent", not unverified, "; ".join(unverified))


def _structured_fare_check(result: AnswerResult) -> CheckResult | None:
    if result.kind != "answered":
        return None
    cited_agencies = {citation.agency for citation in result.citations}
    feed_agencies = {agency for agency in cited_agencies if fare_table.structured_fares(agency)}
    if not feed_agencies:
        return None
    conflicts = structured_fare_contradictions(feed_agencies, result.answer)
    return CheckResult("structured_fare_consistent", not conflicts, "; ".join(conflicts))


def _answer_checks(
    case: dict,
    result: AnswerResult,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list[FareFact]] | None,
) -> list[CheckResult]:
    checks = _citation_checks(case, result, corpus_doc_ids)
    checks.append(CheckResult("as_of_disclosure", bool(guards.AS_OF_RE.search(result.answer))))
    for optional_check in (
        _required_fact_check(case, result.answer),
        _fare_fact_check(result, facts_by_doc),
        _structured_fare_check(result),
    ):
        if optional_check is not None:
            checks.append(optional_check)
    if case.get("requires_handoff"):
        handoff = guards.find_verification_handoff(result.answer)
        checks.append(
            CheckResult(
                "verification_handoff_present",
                handoff,
                "" if handoff else "no verify/apply/contact next step found",
            )
        )
    return checks


def run_checks(
    case: dict,
    result: AnswerResult,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list[FareFact]] | None = None,
) -> list[CheckResult]:
    out = _common_checks(case, result)
    expected = case["expected_behavior"]  # answer | partial | refuse_redirect
    if expected in ("answer", "partial"):
        out.extend(_answer_checks(case, result, corpus_doc_ids, facts_by_doc))

    if expected == "refuse_redirect":
        # The assistant either refused outright or declined for lack of support,
        # and in all cases pointed the rider somewhere real.
        refused = result.kind != "answered"
        out.append(CheckResult("refused", refused, f"kind={result.kind}"))
        out.append(
            CheckResult(
                "redirect_present",
                bool(_REDIRECT_RE.search(result.answer)),
            )
        )

    return out
