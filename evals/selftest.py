"""Harness self-test: prove the deterministic gate catches planted defects.

Three different questions, three different tools:

- **Coverage** says a line of check code ran.
- **Mutation testing** (`docs/mutation-testing.md`) says a unit test would
  notice if that check code were *wrong*.
- **This** says the thing that actually matters to a skeptic: given a
  deliberately *wrong answer*, does the gate fail the *right case*?

It takes an otherwise-clean, grounded answer, plants one known defect (a fare
that contradicts the corpus, a determination phrase, a dropped citation, a
missing date, the wrong agency, an asserted forbidden claim, a missing required
fact), and asserts that the specific check meant to catch it flips from pass to
fail — and only that check's family. No model calls; the deterministic gate is
pure, so CI can enforce this.

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
    return AnswerResult(
        question="q",
        answer=answer,
        kind="answered",
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
