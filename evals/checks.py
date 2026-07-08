"""Deterministic per-case checks.

Each check returns (name, passed, detail). These run on every case regardless
of whether an API key is present, which is what makes the smoke suite useful
in CI without secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from assistant import guards
from assistant.answer import AnswerResult
from assistant.contract import build_structured_answer
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


def run_checks(case: dict, result: AnswerResult, corpus_doc_ids: set[str]) -> list[CheckResult]:
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
        phrase
        for phrase in case.get("forbidden_content", [])
        if re.search(re.escape(phrase), answer, re.I)
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
            pattern = fact[3:] if fact.startswith("re:") else re.escape(fact)
            if not re.search(pattern, answer, re.I):
                missing.append(fact)
        if case.get("required_facts"):
            out.append(CheckResult("required_facts_present", not missing, "; ".join(missing)))

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
