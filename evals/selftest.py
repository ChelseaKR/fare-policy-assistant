"""Harness self-test: prove the deterministic gate catches planted defects.

Three different questions, three different tools:

- **Coverage** says a line of check code ran.
- **Mutation testing** (`docs/mutation-testing.md`) says a unit test would
  notice if that check code were *wrong*.
- **This** says the thing that actually matters to a skeptic: given a
  deliberately *wrong answer*, does the gate fail the *right case*?

It takes an otherwise-clean, grounded answer, plants one known defect (a fare
that contradicts the corpus, a determination phrase, a dropped citation, a
missing date, a date borrowed from a passage the answer never cited, the wrong
agency, an asserted forbidden claim, a missing required fact), and asserts that
the specific check meant to catch it flips from pass to fail — and only that
check's family. No model calls; the deterministic gate is pure, so CI can
enforce this.

Every check `evals/checks.py` emits has a scenario here, and
`tests/test_selftest.py::test_every_check_the_grader_can_emit_has_a_planted_defect`
reads the names out of the grader's source to keep that true. It was not true
until 2026-08-05: five checks had no planted defect, among them `language_match`
(what makes the multilingual suite a language test rather than a second English
suite) and `refused` / `redirect_present` (the entirety of the refusal suite's
deterministic scoring). Those suites score 22/22 and 34/34, and a check that has
never failed and was never shown able to fail is indistinguishable from one that
cannot — the shape of bug that left the bilingual parity gate saturated for a
month while nothing validated its own denominator.

    python -m evals.selftest        # prints a report, exits 1 if any defect slips

A defect that *survives* (mutated answer still passes its check) is a hole in
the harness and fails the run. This is the "we test our tests" evidence the
evaluation story leans on: we break the assistant on purpose and watch the gate
catch it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace

from assistant import config
from assistant import facts as facts_module
from assistant.answer import AnswerResult, Citation
from assistant.ingest import load_chunks
from assistant.retrieve import ScoredChunk
from evals.checks import CheckResult, run_checks

AS_OF = "2026-06-12"


def _corpus_doc_ids() -> set[str]:
    return {c.doc_id for c in load_chunks()}


def _facts_by_doc() -> dict[str, list[facts_module.FareFact]]:
    by_doc: dict[str, list[facts_module.FareFact]] = {}
    for fact in facts_module.load_facts(config.FACTS_PATH):
        by_doc.setdefault(fact.doc_id, []).append(fact)
    return by_doc


def _priced_fact(by_doc: dict[str, list[facts_module.FareFact]]) -> facts_module.FareFact:
    """A real corpus fare fact with a numeric price, so the clean answer quotes a
    value the corpus actually supports rather than a hard-coded constant that
    could drift out of the table."""
    for doc_id in ("mst-fares", "sbmtd-fares-passes", "yolobus-fares"):
        for fact in by_doc.get(doc_id, []):
            if fact.price is not None:
                return fact
    raise RuntimeError("no priced fact in the corpus fact table; cannot build self-test")


def _clean(doc_id: str, answer: str, *, agency: str = "MST") -> AnswerResult:
    # `as_of_date` mirrors the single citation's fetch date, which is what the
    # answer pipeline now produces (assistant.answer._as_of_cited) and what
    # `as_of_matches_oldest_citation` requires of a clean answer.
    return AnswerResult(
        question="q",
        answer=answer,
        kind="answered",
        as_of_date=AS_OF,
        citations=[
            Citation(
                doc_id=doc_id,
                agency=agency,
                title="Fares",
                url="https://example.org/fares/",
                fetch_date=AS_OF,
            )
        ],
    )


def _mixed_freshness_passages(cited_doc_id: str) -> list[ScoredChunk]:
    """A retrieved set spanning two fetch dates: the cited document at `AS_OF`
    and the corpus's most recently refetched document alongside it.

    Built from the real corpus so the scenario stays honest about the shape that
    produces the defect — documents are refetched one at a time, so a top-k that
    mixes fetch dates is routine. Falls back to a synthetic fresher chunk if the
    corpus ever becomes uniformly dated, which would otherwise make this
    scenario silently untestable.
    """
    chunks = load_chunks()
    cited = next(c for c in chunks if c.doc_id == cited_doc_id)
    cited = replace(cited, fetch_date=AS_OF)
    fresher = max(chunks, key=lambda c: c.fetch_date)
    if fresher.fetch_date <= AS_OF:
        fresher = replace(fresher, fetch_date="2026-08-10")
    return [ScoredChunk(chunk=cited, score=10.0), ScoredChunk(chunk=fresher, score=9.0)]


@dataclass
class Scenario:
    name: str
    check: str  # the check whose `.passed` must be True on clean, False on mutated
    case: dict
    clean: AnswerResult
    mutate: Callable[[AnswerResult], AnswerResult]


def _scenarios() -> list[Scenario]:
    by_doc = _facts_by_doc()
    fact = _priced_fact(by_doc)
    doc_id = fact.doc_id
    good_price = f"${fact.price:.2f}"
    # A price guaranteed absent from every doc's fact table, so the wrong-fare
    # mutant cannot accidentally match some other row.
    all_prices = {f.price for fs in by_doc.values() for f in fs if f.price is not None}
    bad = max(all_prices) + 1000.0
    bad_price = f"${bad:.2f}"

    base_answer = (
        f"The fare is {good_price} [doc:{doc_id}], based on policies published as of {AS_OF}."
    )

    return [
        Scenario(
            name="fare contradicts the corpus",
            check="fare_facts_consistent",
            case={"expected_behavior": "answer", "agency_scope": fact.agency, "language": "en"},
            clean=_clean(doc_id, base_answer, agency=fact.agency),
            mutate=lambda r: replace(r, answer=r.answer.replace(good_price, bad_price)),
        ),
        Scenario(
            name="determination language",
            check="no_determination_language",
            case={"expected_behavior": "answer", "language": "en"},
            clean=_clean(doc_id, base_answer),
            mutate=lambda r: replace(r, answer=r.answer + " You qualify for this discount."),
        ),
        Scenario(
            name="dropped citation",
            check="citation_present_and_resolvable",
            case={"expected_behavior": "answer", "language": "en"},
            clean=_clean(doc_id, base_answer),
            # No resolvable citation: strip the tag and empty the citation list.
            mutate=lambda r: replace(
                r, answer=r.answer.replace(f" [doc:{doc_id}]", ""), citations=[]
            ),
        ),
        Scenario(
            name="missing as-of date",
            check="as_of_disclosure",
            case={"expected_behavior": "answer", "language": "en"},
            clean=_clean(doc_id, base_answer),
            mutate=lambda r: replace(
                r, answer=r.answer.replace(f", based on policies published as of {AS_OF}", "")
            ),
        ),
        Scenario(
            name="wrong agency cited",
            check="correct_agency_cited",
            case={"expected_behavior": "answer", "agency_scope": "Yolobus", "language": "en"},
            clean=_clean(
                "yolobus-fares", base_answer.replace(doc_id, "yolobus-fares"), agency="Yolobus"
            ),
            mutate=lambda r: replace(
                r,
                answer=base_answer,  # cites the MST doc for a Yolobus-scoped case
                citations=[replace(r.citations[0], doc_id=doc_id, agency="MST")],
            ),
        ),
        Scenario(
            name="asserted forbidden claim",
            check="forbidden_content_absent",
            case={
                "expected_behavior": "answer",
                "language": "en",
                "forbidden_content": ["valid for return trips"],
            },
            clean=_clean(
                doc_id,
                f"Transfers are not valid for return trips [doc:{doc_id}], as of {AS_OF}.",
            ),
            mutate=lambda r: replace(
                r,
                answer=f"Your transfer is valid for return trips [doc:{doc_id}], as of {AS_OF}.",
            ),
        ),
        Scenario(
            name="fare contradicts the agency's GTFS feed",
            check="structured_fare_consistent",
            case={"expected_behavior": "answer", "language": "en"},
            # SBMTD's feed binds the senior (reduced) class to $1.25 and the
            # standard class to $2.50; stating $2.50 for the senior class is the
            # wrong-number-for-the-right-class misread.
            clean=_clean(
                "sbmtd-fares-passes",
                f"The SBMTD senior fare is $1.25 [doc:sbmtd-fares-passes], as of {AS_OF}.",
                agency="SBMTD",
            ),
            mutate=lambda r: replace(
                r,
                answer=r.answer.replace("$1.25", "$2.50"),  # a real feed amount, wrong class
            ),
        ),
        Scenario(
            name="missing required fact",
            check="required_facts_present",
            case={
                "expected_behavior": "answer",
                "language": "en",
                "required_facts": ["DD Form 214"],
            },
            clean=_clean(
                doc_id,
                f"Veterans show a DD Form 214 [doc:{doc_id}], as of {AS_OF}.",
            ),
            mutate=lambda r: replace(
                r, answer=f"Veterans show some paperwork [doc:{doc_id}], as of {AS_OF}."
            ),
        ),
        # ── added 2026-08-05 ──────────────────────────────────────────────
        # The five scenarios below cover the five checks `run_checks` emits
        # that nothing here had ever planted a defect against. Three of them
        # are the only deterministic teeth their suite has: `language_match`
        # is what makes the multilingual suite a language test rather than a
        # second English suite, and `refused` / `redirect_present` are the
        # whole of the refusal suite's non-judge scoring. Refusal has scored
        # 34/34 and multilingual 22/22; a check that has never failed and was
        # never shown able to fail is indistinguishable from one that cannot.
        # `test_every_emitted_check_has_a_planted_defect` keeps the set closed.
        Scenario(
            name="answered in the wrong language",
            check="language_match",
            case={"expected_behavior": "answer", "agency_scope": "MST", "language": "es"},
            clean=_clean(
                doc_id,
                f"El pase mensual con descuento cuesta {good_price} [doc:{doc_id}]. "
                f"Segun las politicas publicadas al {AS_OF}, confirme con la agencia.",
                agency=fact.agency,
            ),
            # The Spanish case answered in English: the mirrored-pair parity
            # number is only an equity measurement if this fails.
            mutate=lambda r: replace(
                r,
                answer=f"The discounted monthly pass costs {good_price} [doc:{doc_id}]. "
                f"Based on policies published as of {AS_OF}, please confirm with the agency.",
            ),
        ),
        Scenario(
            name="answers a question it was supposed to decline",
            check="refused",
            case={"expected_behavior": "refuse_redirect", "language": "en"},
            clean=AnswerResult(
                question="q",
                answer="I don't have a published policy that answers that. "
                "Please contact the agency's customer service.",
                kind="refused_no_support",
                citations=[],
            ),
            mutate=lambda r: replace(r, kind="answered"),
        ),
        Scenario(
            name="declines without pointing anywhere",
            check="redirect_present",
            case={"expected_behavior": "refuse_redirect", "language": "en"},
            clean=AnswerResult(
                question="q",
                answer="I don't have a published policy that answers that. "
                "Please contact the agency's customer service.",
                kind="refused_no_support",
                citations=[],
            ),
            mutate=lambda r: replace(
                r, answer="I don't have a published policy that answers that."
            ),
        ),
        Scenario(
            name="states the criterion and stops",
            check="verification_handoff_present",
            case={"expected_behavior": "answer", "language": "en", "requires_handoff": True},
            clean=_clean(
                doc_id,
                f"The published criterion is age 65 and over [doc:{doc_id}], as of {AS_OF}. "
                "Contact the agency to apply and verify your eligibility.",
            ),
            mutate=lambda r: replace(
                r,
                answer=f"The published criterion is age 65 and over [doc:{doc_id}], as of {AS_OF}.",
            ),
        ),
        Scenario(
            name="answer that cannot be rendered as the typed contract",
            check="structured_contract_schema_valid",
            case={"expected_behavior": "answer", "language": "en"},
            clean=_clean(doc_id, base_answer),
            # A kind outside the contract's enum: the UI would silently fall
            # back to prose, which this check exists to count rather than hide.
            mutate=lambda r: replace(r, kind="answered_partial"),
        ),
        Scenario(
            name="dated by the freshest passage retrieved, not the one cited",
            check="as_of_matches_oldest_citation",
            case={"expected_behavior": "answer", "language": "en"},
            # A realistic mixed-freshness top-k: the answer cites a passage
            # fetched on AS_OF, and retrieval also surfaced (but the answer
            # never used) a passage refetched two months later.
            clean=replace(_clean(doc_id, base_answer), passages=_mixed_freshness_passages(doc_id)),
            # The mutation *is* the pre-fix expression, verbatim: the headline
            # date taken as max(fetch_date) over everything retrieved. The rider
            # was told the policy was current as of a page the answer does not
            # rest on, while the citation under it was months older.
            mutate=lambda r: replace(r, as_of_date=max(sc.chunk.fetch_date for sc in r.passages)),
        ),
        Scenario(
            name="the sentence the rider reads is dated later than the evidence under it",
            check="as_of_prose_matches_structured",
            case={"expected_behavior": "answer", "language": "en"},
            clean=replace(_clean(doc_id, base_answer), passages=_mixed_freshness_passages(doc_id)),
            # The mutation is the pre-fix behaviour of issue #163, verbatim: the
            # prompt is handed max(fetch_date) over the retrieved set and told to
            # render it in "based on policies published as of <date>", while the
            # structured as_of stays on the oldest cited passage. The structured
            # check above still passes on this mutant, which is the whole point:
            # it validates a field, and the rider reads a sentence. 28 of 345
            # answers in the 2026-08-22 full live run diverged this way.
            mutate=lambda r: replace(
                r,
                answer=r.answer.replace(AS_OF, max(sc.chunk.fetch_date for sc in r.passages)),
            ),
        ),
    ]


@dataclass
class Outcome:
    name: str
    check: str
    clean_passed: bool  # the check passes on the clean answer (no false positive)
    caught: bool  # the check fails on the mutated answer (defect caught)

    @property
    def ok(self) -> bool:
        return self.clean_passed and self.caught


def _named(checks: list[CheckResult]) -> dict[str, CheckResult]:
    return {c.name: c for c in checks}


def run_selftest() -> list[Outcome]:
    doc_ids = _corpus_doc_ids()
    by_doc = _facts_by_doc()
    outcomes: list[Outcome] = []
    for sc in _scenarios():
        clean = _named(run_checks(sc.case, sc.clean, doc_ids, by_doc))
        mutated = _named(run_checks(sc.case, sc.mutate(sc.clean), doc_ids, by_doc))
        # A check absent on the clean run (e.g. the case did not opt into it)
        # counts as not-passing, which would surface a mis-built scenario.
        clean_passed = sc.check in clean and clean[sc.check].passed
        caught = sc.check in mutated and not mutated[sc.check].passed
        outcomes.append(Outcome(sc.name, sc.check, clean_passed, caught))
    return outcomes


def _report(outcomes: list[Outcome]) -> int:
    """Print the self-test result and return a process exit code (0 = all
    defects caught and no clean answer wrongly failed)."""
    caught = sum(o.caught for o in outcomes)
    print(f"Harness self-test: planted {len(outcomes)} defects into clean answers.\n")
    for o in outcomes:
        mark = "OK  " if o.ok else "FAIL"
        if not o.clean_passed:
            note = " (clean answer did not pass — scenario is mis-built)"
        elif not o.caught:
            note = " (DEFECT SURVIVED — the gate did not catch it)"
        else:
            note = ""
        print(f"  [{mark}] {o.check:<32} {o.name}{note}")
    print(
        f"\nCaught {caught}/{len(outcomes)} planted defects; "
        f"{sum(o.clean_passed for o in outcomes)}/{len(outcomes)} clean answers passed cleanly."
    )
    ok = all(o.ok for o in outcomes)
    print(
        "PASS: the deterministic gate catches every planted defect."
        if ok
        else "FAIL: at least one defect slipped or a clean answer was wrongly failed."
    )
    return 0 if ok else 1


def main() -> int:
    return _report(run_selftest())


if __name__ == "__main__":
    sys.exit(main())
