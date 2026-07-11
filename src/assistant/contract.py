"""EXP-04 — structured answer contract (docs/ideation/03-expansions.md).

Turns the free-text `AnswerResult.answer` into the typed payload described in
`docs/answer-contract.schema.json`: criterion, price list, proof documents,
next step, decision owner, as-of date, citations. Each prompt bump so far
(v6 asked-for-price, v7 positive handoff, v4 close-the-loop) tried to force
this shape through prose instructions, checkable only by the judge. This
module makes the contract checkable field-by-field without changing the
prompt or the model call: it deterministically parses the existing grounded
answer text plus the `AnswerResult` the pipeline already builds.

Because the parse is heuristic (sentence-level regex, not a model emitting
JSON directly), it can fail honestly on an answer shaped unusually. On parse
or schema-validation failure, `build_structured_answer` returns
`structured_ok=False` and callers fall back to the plain-text answer — never
hidden, always counted (see `evals/checks.py::structured_contract_checks` and
the `structured_fallback` log field in `web/handler.py`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import jsonschema

from assistant import config
from assistant.answer import AnswerResult

SCHEMA_PATH = config.REPO_ROOT / "docs" / "answer-contract.schema.json"

# Split on sentence-ending punctuation followed by whitespace, keeping the
# terminator. Good enough for the short, plain-sentence answer style the
# system prompt asks for; it does not need to be a general sentence splitter.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")

_PROOF_RE = re.compile(
    r"\b(proof|document|documentation|card|ID\b|identification|letter|"
    r"comprobante|documento|identificaci[óo]n|carta|tarjeta)",
    re.I,
)

_NEXT_STEP_RE = re.compile(
    r"\b(apply|contact|visit|call|register|enroll|sign up|bring|renew|"
    r"solicit(e|ar)|comun[ií]quese|llame|visite|inscr[íi]base|regist[rr]ese)\b",
    re.I,
)

_CITATION_TAG_RE = re.compile(r"\s*\[doc:[a-z0-9-]+\]")


@dataclass
class Price:
    amount: str
    currency: str
    context: str


@dataclass
class ProofDoc:
    doc_id: str
    title: str
    context: str


@dataclass
class StructuredAnswer:
    """Mirrors docs/answer-contract.schema.json. `structured_ok` and
    `fallback_reason` are not schema fields (they are metadata about the
    parse itself); `to_json_dict()` drops them before validation/rendering."""

    kind: str
    criterion: str
    prices: list[Price] = field(default_factory=list)
    proof_docs: list[ProofDoc] = field(default_factory=list)
    next_step: str = ""
    decision_owner: str = ""
    as_of_date: str = ""
    citations: list[dict] = field(default_factory=list)
    structured_ok: bool = True
    fallback_reason: str = ""

    def to_json_dict(self) -> dict:
        return {
            "kind": self.kind,
            "criterion": self.criterion,
            "prices": [
                {"amount": p.amount, "currency": p.currency, "context": p.context}
                for p in self.prices
            ],
            "proof_docs": [
                {"doc_id": p.doc_id, "title": p.title, "context": p.context}
                for p in self.proof_docs
            ],
            "next_step": self.next_step,
            "decision_owner": self.decision_owner,
            "as_of_date": self.as_of_date,
            "citations": self.citations,
        }


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_answer_contract(payload: dict) -> list[str]:
    """Return schema validation errors (empty means valid)."""
    validator = jsonschema.Draft202012Validator(_schema())
    errors = []
    for e in validator.iter_errors(payload):
        pointer = "/".join(str(p) for p in e.path) or "<root>"
        errors.append(f"{pointer}: {e.message}")
    return errors


def _extract_prices(sentences: list[str]) -> list[Price]:
    prices: list[Price] = []
    for s in sentences:
        for m in _PRICE_RE.finditer(s):
            amount = m.group(1)
            if "." not in amount:
                amount = f"{amount}.00"
            prices.append(Price(amount=amount, currency="USD", context=s))
    return prices


def _extract_next_step(sentences: list[str]) -> str:
    for s in sentences:
        if _NEXT_STEP_RE.search(s):
            return s
    return ""


def _extract_decision_owner(citations: list[dict]) -> str:
    agencies = [c["agency"] for c in citations if c.get("agency")]
    if not agencies:
        return ""
    # Preserve first-seen order, de-duplicated.
    ordered = list(dict.fromkeys(agencies))
    return ", ".join(ordered)


def build_structured_answer(result: AnswerResult) -> StructuredAnswer:
    """Deterministically derive the typed contract from an AnswerResult.

    Refusal kinds (`refused_input`, `refused_no_support`) carry no prices,
    proof docs, or citations by construction (the pipeline never calls the
    model for those) — `criterion` holds the decline message itself so the UI
    still has something to render in a labeled region rather than a bare
    paragraph.
    """
    citations = [
        {
            "doc_id": c.doc_id,
            "agency": c.agency,
            "title": c.title,
            "url": c.url,
            "fetch_date": c.fetch_date,
        }
        for c in result.citations
    ]

    if result.kind != "answered":
        structured = StructuredAnswer(
            kind=result.kind,
            criterion=_CITATION_TAG_RE.sub("", result.answer).strip(),
            as_of_date=result.as_of_date,
            citations=citations,
        )
        errors = validate_answer_contract(structured.to_json_dict())
        if errors:
            structured.structured_ok = False
            structured.fallback_reason = "; ".join(errors)
        return structured

    try:
        # Sentences that carry a citation tag become "criterion" text (with
        # the tag stripped for display); the price/proof/next-step scans run
        # over the same sentence list so context always traces back to a
        # sentence the answer actually contains.
        tagged = _SENTENCE_RE.split(result.answer.strip())
        sentences: list[str] = []
        by_id = {c["doc_id"]: c for c in citations}
        proof_docs: list[ProofDoc] = []
        seen_proof: set[str] = set()
        doc_tag_re = re.compile(r"\[doc:([a-z0-9-]+)\]")
        for raw in tagged:
            clean = _CITATION_TAG_RE.sub("", raw).strip()
            if not clean:
                continue
            sentences.append(clean)
            if _PROOF_RE.search(raw):
                for doc_id in doc_tag_re.findall(raw):
                    if doc_id in by_id and doc_id not in seen_proof:
                        seen_proof.add(doc_id)
                        proof_docs.append(
                            ProofDoc(doc_id=doc_id, title=by_id[doc_id]["title"], context=clean)
                        )

        criterion = " ".join(sentences)
        prices = _extract_prices(sentences)
        next_step = _extract_next_step(sentences)
        decision_owner = _extract_decision_owner(citations)

        structured = StructuredAnswer(
            kind=result.kind,
            criterion=criterion,
            prices=prices,
            proof_docs=proof_docs,
            next_step=next_step,
            decision_owner=decision_owner,
            as_of_date=result.as_of_date,
            citations=citations,
        )
    except Exception as exc:  # parsing must never break the response
        structured = StructuredAnswer(
            kind=result.kind,
            criterion="",
            as_of_date=result.as_of_date,
            citations=citations,
            structured_ok=False,
            fallback_reason=f"parse_error:{type(exc).__name__}",
        )
        return structured

    errors = validate_answer_contract(structured.to_json_dict())
    if errors:
        structured.structured_ok = False
        structured.fallback_reason = "; ".join(errors)
    return structured
