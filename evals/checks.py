"""Deterministic per-case checks.

Each check returns (name, passed, detail). These run on every case regardless
of whether an API key is present, which is what makes the smoke suite useful
in CI without secrets.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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


def structured_fare_contradictions(
    agencies: set[str],
    answer: str,
    structured_fares_by_agency: Mapping[
        str,
        Sequence[fare_table.StructuredFare],
    ]
    | None = None,
) -> list[str]:
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
        fares = (
            fare_table.structured_fares(agency)
            if structured_fares_by_agency is None
            else structured_fares_by_agency.get(agency, ())
        )
        for fare in fares:
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


def run_checks(
    case: dict,
    result: AnswerResult,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list[FareFact]] | None = None,
    structured_fares_by_agency: Mapping[
        str,
        Sequence[fare_table.StructuredFare],
    ]
    | None = None,
) -> list[CheckResult]:
    out: list[CheckResult] = []
    expected = case["expected_behavior"]  # answer | partial | refuse_redirect
    answer = result.answer

    # 1. Determination language is forbidden on every case, including refusals.
    hits = guards.find_determination_language(answer)
    out.append(
        CheckResult(
            "no_determination_language",
            not hits,
            "; ".join(hits) if hits else "",
        )
    )

    # 1b. EXP-04 (docs/ideation/03-expansions.md): every result, regardless of
    # kind, must parse into a schema-valid structured contract. The schema
    # allows empty prices/proof_docs/next_step/decision_owner, so this checks
    # that the contract is well-typed, not that any particular field is
    # populated — field-completeness gating (next_step present, a price
    # listed when a fact row matches, etc.) is the item's own excellence bar,
    # and per its risk note needs a live regression cycle to add without
    # false failures, which a deterministic-only run cannot provide.
    structured = build_structured_answer(result)
    out.append(
        CheckResult(
            "structured_contract_schema_valid",
            structured.structured_ok,
            structured.fallback_reason,
        )
    )

    # 2. Case-specific forbidden content. Uses phrase_asserted (not
    # phrase_present) so an answer that correctly *denies* or quotes-to-reject a
    # forbidden claim is not miscounted as asserting it (class A of
    # docs/audits/eval-remediation-2026-07-11.md).
    forbidden = [
        phrase for phrase in case.get("forbidden_content", []) if phrase_asserted(phrase, answer)
    ]
    out.append(CheckResult("forbidden_content_absent", not forbidden, "; ".join(forbidden)))

    # 3. Response language matches the question language.
    expected_lang = case.get("language", "en")
    actual_lang, confidence, unsure = guards.detect_language_confident(answer)
    out.append(
        CheckResult(
            "language_match",
            actual_lang == expected_lang,
            f"expected {expected_lang}, got {actual_lang} "
            f"(confidence={confidence:.3f}, unsure={str(unsure).lower()})",
        )
    )

    if expected in ("answer", "partial"):
        # 4. A real answer must carry a citation that resolves to the corpus.
        cited = set(guards.CITATION_RE.findall(answer))
        resolvable = cited & corpus_doc_ids
        out.append(
            CheckResult(
                "citation_present_and_resolvable",
                result.kind == "answered" and bool(resolvable),
                f"kind={result.kind}, cited={sorted(cited) or 'none'}",
            )
        )

        # 5. The citation points at the agency the case is about.
        scope = case.get("agency_scope")
        if scope and result.kind == "answered":
            agencies = {c.agency for c in result.citations}
            out.append(
                CheckResult(
                    "correct_agency_cited",
                    scope in agencies,
                    f"expected {scope}, cited {sorted(agencies) or 'none'}",
                )
            )

        # 6. The "as of" disclosure appears.
        out.append(
            CheckResult(
                "as_of_disclosure",
                bool(guards.AS_OF_RE.search(answer)),
            )
        )

        # 7. Required facts (verbatim or regex with re: prefix) appear.
        missing = []
        for fact in case.get("required_facts", []):
            if not fact_matches(fact, answer):
                missing.append(fact)
        if case.get("required_facts"):
            out.append(CheckResult("required_facts_present", not missing, "; ".join(missing)))

        # 8. EXP-01: numeric price/age claims verified against the structured
        # FareFact table for the cited doc(s), deterministically, instead of
        # relying only on the LLM judge for groundedness of numbers. Only
        # emitted when we have a fact table for at least one cited doc — a
        # doc the extractor found no facts in (e.g. a narrative or contact
        # page) falls back to today's judge-only behavior rather than
        # failing every numeric claim against an empty candidate set.
        if facts_by_doc is not None and result.kind == "answered":
            candidates = [f for c in result.citations for f in facts_by_doc.get(c.doc_id, [])]
            if candidates:
                unverified = [
                    f"${amount:.2f}"
                    for amount in facts_module.parse_price_claims(answer)
                    if not any(
                        f.price is not None and abs(f.price - amount) < 0.005 for f in candidates
                    )
                ]
                unverified += [
                    f"age {'' if claim[0] is None else claim[0]}-"
                    f"{'' if claim[1] is None else claim[1]}"
                    for claim in facts_module.parse_age_claims(answer)
                    if not _age_claim_supported(claim, candidates)
                ]
                out.append(
                    CheckResult("fare_facts_consistent", not unverified, "; ".join(unverified))
                )

        # 8b. Structured fare consistency against the agency's GTFS-Fares feed
        # (ADR 0017). Where the cited agency publishes a machine-readable feed,
        # a dollar amount the answer states for a named rider class must match
        # the feed's amount for that class. Authoritative and false-positive-free
        # by construction (validated at 0 flags over the promoted run); dormant
        # for agencies with no feed. Catches the wrong-number-for-the-right-class
        # misread the judge otherwise owns alone.
        if result.kind == "answered":
            cited_agencies = {c.agency for c in result.citations}
            fares_for_check = {
                agency: tuple(
                    fare_table.structured_fares(agency)
                    if structured_fares_by_agency is None
                    else structured_fares_by_agency.get(agency, ())
                )
                for agency in cited_agencies
            }
            feed_agencies = {agency for agency, fares in fares_for_check.items() if fares}
            if feed_agencies:
                feed_conflicts = structured_fare_contradictions(
                    feed_agencies,
                    answer,
                    fares_for_check,
                )
                out.append(
                    CheckResult(
                        "structured_fare_consistent",
                        not feed_conflicts,
                        "; ".join(feed_conflicts),
                    )
                )

        # 9. Positive verification handoff (RR4). An eligibility-adjacent answer
        # must route the rider to where the decision actually happens — the
        # agency or Cal-ITP — and how to start, never stopping at the criterion.
        # Opt-in per case (`requires_handoff: true`); this strengthens the
        # no-determination rule by requiring the constructive next step beside
        # the refusal to rule on the rider, and never relaxes it.
        if case.get("requires_handoff"):
            out.append(
                CheckResult(
                    "verification_handoff_present",
                    guards.find_verification_handoff(answer),
                    "no verify/apply/contact next step found"
                    if not guards.find_verification_handoff(answer)
                    else "",
                )
            )

    if expected == "refuse_redirect":
        # The assistant either refused outright or declined for lack of support,
        # and in all cases pointed the rider somewhere real.
        refused = result.kind != "answered"
        out.append(CheckResult("refused", refused, f"kind={result.kind}"))
        out.append(
            CheckResult(
                "redirect_present",
                bool(_REDIRECT_RE.search(answer)),
            )
        )

    return out
