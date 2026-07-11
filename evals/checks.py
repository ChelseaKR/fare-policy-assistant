"""Deterministic per-case checks.

Each check returns (name, passed, detail). These run on every case regardless
of whether an API key is present, which is what makes the smoke suite useful
in CI without secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from assistant import facts as facts_module
from assistant import guards
from assistant.answer import AnswerResult
from assistant.contract import build_structured_answer
from assistant.facts import FareFact
from assistant.guards import detect_language

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
    case-insensitive). Shared with the freshness staleness lint."""
    return bool(re.search(re.escape(phrase), text, re.I))


def _age_claim_supported(claim: tuple[int | None, int | None], candidates: list[FareFact]) -> bool:
    claim_min, claim_max = claim
    if claim_min is None and claim_max is None:
        return True  # not a real claim; nothing to verify
    for fact in candidates:
        if claim_min is not None and fact.age_min != claim_min:
            continue
        if claim_max is not None and fact.age_max != claim_max:
            continue
        return True
    return False


def run_checks(
    case: dict,
    result: AnswerResult,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list[FareFact]] | None = None,
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

    # 2. Case-specific forbidden content.
    forbidden = [
        phrase for phrase in case.get("forbidden_content", []) if phrase_present(phrase, answer)
    ]
    out.append(CheckResult("forbidden_content_absent", not forbidden, "; ".join(forbidden)))

    # 3. Response language matches the question language.
    expected_lang = case.get("language", "en")
    actual_lang = detect_language(answer)
    out.append(
        CheckResult(
            "language_match",
            actual_lang == expected_lang,
            f"expected {expected_lang}, got {actual_lang}",
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
                    f"age {claim[0] or ''}-{claim[1] or ''}"
                    for claim in facts_module.parse_age_claims(answer)
                    if not _age_claim_supported(claim, candidates)
                ]
                out.append(
                    CheckResult("fare_facts_consistent", not unverified, "; ".join(unverified))
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
