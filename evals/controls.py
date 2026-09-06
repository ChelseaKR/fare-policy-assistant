"""Negative controls: how much of the score is retrieval, and not the model.

Issue #212. The harness reports pass rates, Wilson intervals and a
leave-one-suite-out jackknife, and none of that can tell a grounded answer from
a model that knows California fares from pretraining. The second-harness audit
gives the numbers an outside floor; this gives them an inside one. Three arms,
all scored by the same `evals.checks.run_checks` the real run uses:

  - **no_retrieval** — the assistant is handed no passages at all, so a
    resolvable citation is impossible. If it still cites the corpus, the
    citation did not come from retrieval.
  - **wrong_agency** — the assistant is handed a *different* agency's passages
    for the same question. If it still cites the right agency, the agency
    binding is not coming from the evidence.
  - **stale_corpus** — retrieval runs against an older retained corpus version
    (`corpus/versions/<id>`), most of whose agencies do not exist yet. A score
    that does not move means the corpus expansion bought nothing measurable.

Each control is a `Retriever` substitution and nothing else: the prompt, the
guards, the answer pipeline and every deterministic check are the ones the real
run uses. That is what makes the comparison a control rather than a second
implementation.

**The overall pass rate is the wrong direction to assert on, and this run
proves it.** Offline, the no-retrieval control scores 36/385 against the
baseline's 21/385 — *higher* — because every refusal case passes when the
assistant has nothing to stand on. A control suite that asserted "the control
must score lower" would have shipped green and measured nothing. The assertions
below are therefore per-check, on the two checks retrieval is supposed to be
the cause of.

Runs offline against the mock model, so it costs nothing and is deterministic:
`assistant.models.MockModel` answers only from the passages it is given, which
is exactly the property a retrieval control needs.

    python -m evals.controls          # table + direction assertions, exit 1 on failure
    python -m evals.controls --limit 40   # dev sample: reports, never gates

The floors below were measured over the whole suite, so `--limit` reports the
assertions and exits 0 rather than failing on a slice that was never the
population they were set against. The gate is the full run; `make controls`
and CI pass no limit.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from assistant import config, corpus
from assistant import facts as facts_module
from assistant.answer import answer_question
from assistant.ingest import Chunk, load_chunks
from assistant.models import Model, get_model
from assistant.retrieve import Retriever, ScoredChunk
from evals.checks import run_checks

BASELINE = "baseline"
NO_RETRIEVAL = "no_retrieval"
WRONG_AGENCY = "wrong_agency"
STALE_CORPUS = "stale_corpus"


@dataclass(frozen=True)
class Assertion:
    """One direction the controls must move the instrument in.

    `max_rate` is an absolute ceiling on the control's rate for `check`;
    `min_drop_points` is a floor on how far below the baseline's rate it must
    sit. Both are stated, and both measured before being set — a floor above
    what the system does is a red gate on the day it lands.
    """

    control: str
    check: str
    max_rate: float | None = None
    min_drop_points: float | None = None
    why: str = ""


# Measured on the committed corpus, 385 cases, offline mock model (2026-09-06):
#
#   check                            baseline  no_retrieval  wrong_agency  stale_corpus
#   citation_present_and_resolvable    99.7%        0.0%         53.3%        52.1%
#   correct_agency_cited               98.1%          --          0.0%        96.3%
#
# The two exact-zero assertions are not thresholds, they are impossibilities: a
# citation cannot resolve when no passage was retrieved, and the cited agency
# cannot be right when only another agency's passages were offered. Anything
# above zero there is the harness measuring something other than retrieval.
# `stale_corpus` is a threshold, set at 20 points against a measured drop of
# 47.6, so it has real headroom and still fails if the corpus history stops
# mattering.
ASSERTIONS: tuple[Assertion, ...] = (
    Assertion(
        NO_RETRIEVAL,
        "citation_present_and_resolvable",
        max_rate=0.0,
        why="a citation cannot resolve to the corpus when no passage was retrieved",
    ),
    Assertion(
        WRONG_AGENCY,
        "correct_agency_cited",
        max_rate=0.0,
        why="the cited agency cannot be the right one when only another agency's "
        "passages were offered",
    ),
    Assertion(
        STALE_CORPUS,
        "citation_present_and_resolvable",
        min_drop_points=20.0,
        why="most agencies do not exist in the oldest retained corpus, so a score "
        "that does not move means the expansion bought nothing measurable",
    ),
)
# The controls only prove something if the baseline itself is healthy. A
# baseline whose citations already fail cannot be told apart from a control.
BASELINE_FLOORS: tuple[tuple[str, float], ...] = (
    ("citation_present_and_resolvable", 90.0),
    ("correct_agency_cited", 90.0),
)


@dataclass
class ArmResult:
    name: str
    cases: int = 0
    cases_passed: int = 0
    checks: dict[str, list[int]] = field(default_factory=dict)

    def rate(self, check: str) -> float | None:
        """Percent of emitted `check` results that passed, or None when this arm
        never emitted it. None is not zero: a check the arm could not reach was
        not failed, and reporting it as 0% would be an absence rendered as a
        measurement."""
        tally = self.checks.get(check)
        if not tally or not tally[1]:
            return None
        return 100.0 * tally[0] / tally[1]

    def record(self, passed_checks: list[tuple[str, bool]]) -> None:
        self.cases += 1
        self.cases_passed += all(passed for _, passed in passed_checks)
        for name, passed in passed_checks:
            tally = self.checks.setdefault(name, [0, 0])
            tally[0] += int(passed)
            tally[1] += 1


class _ControlRetriever:
    """A retrieval substitution that leaves every other behaviour alone.

    Confidence banding and the decline threshold delegate to the real
    retriever, so a control never accidentally changes the assistant's
    willingness to answer by some route other than the passages it was given.
    """

    def __init__(self, inner: Retriever):
        self.inner = inner

    @property
    def cfg(self) -> config.RetrievalConfig:
        return self.inner.cfg

    def search(self, question: str, agency: str | None = None) -> list[ScoredChunk]:
        raise NotImplementedError

    def confidence_signals(self, question: str, results: list[ScoredChunk]):
        return self.inner.confidence_signals(question, results)

    def confident(self, question: str, results: list[ScoredChunk]) -> bool:
        return self.inner.confident(question, results)


class NoRetrieval(_ControlRetriever):
    """Hands the assistant nothing. `confident` is False by construction rather
    than by delegation: an empty result set has no scores to band, and asking
    the real retriever to judge one would be measuring the wrong thing."""

    def search(self, question: str, agency: str | None = None) -> list[ScoredChunk]:
        return []

    def confident(self, question: str, results: list[ScoredChunk]) -> bool:
        return False


class WrongAgency(_ControlRetriever):
    """Answers every question from the next agency in the corpus, alphabetically.

    Deterministic rather than random: a control that shuffles differently on
    each run cannot be compared across runs, and this harness's whole claim is
    that its numbers are reproducible. The rotation is off the agency the real
    retrieval would have chosen, so the substituted agency is always a real
    one and always the wrong one.
    """

    def __init__(self, inner: Retriever, agencies: list[str]):
        super().__init__(inner)
        self.agencies = agencies

    def search(self, question: str, agency: str | None = None) -> list[ScoredChunk]:
        real = self.inner.search(question)
        here = real[0].chunk.agency if real else self.agencies[0]
        index = self.agencies.index(here) if here in self.agencies else 0
        other = self.agencies[(index + 1) % len(self.agencies)]
        return self.inner.search(question, agency=other)


@dataclass(frozen=True)
class Harness:
    """Everything a control arm needs, built once from the live corpus."""

    cfg: config.Config
    model: Model
    chunks: list[Chunk]
    corpus_doc_ids: set[str]
    facts_by_doc: dict[str, list[facts_module.FareFact]]
    doc_texts: dict[str, str]

    @property
    def agencies(self) -> list[str]:
        return sorted({chunk.agency for chunk in self.chunks})


def build_harness(cfg: config.Config | None = None) -> Harness:
    cfg = cfg or config.Config()
    chunks = load_chunks()
    facts_by_doc: dict[str, list[facts_module.FareFact]] = {}
    for fact in facts_module.load_facts(config.FACTS_PATH):
        facts_by_doc.setdefault(fact.doc_id, []).append(fact)
    texts: dict[str, list[str]] = {}
    for chunk in chunks:
        texts.setdefault(chunk.doc_id, []).append(chunk.text)
    return Harness(
        cfg=cfg,
        model=get_model("mock", "mock"),
        chunks=chunks,
        corpus_doc_ids={c.doc_id for c in chunks},
        facts_by_doc=facts_by_doc,
        doc_texts={doc_id: "\n".join(parts) for doc_id, parts in texts.items()},
    )


def control_retrievers(harness: Harness, *, stale_version: str | None = None) -> dict[str, object]:
    """The baseline retriever and the three controls, keyed by arm name.

    `stale_corpus` is absent when the repository retains no earlier corpus
    version — a fresh clone of the template, say. Absent, not empty: a control
    that silently degrades to the baseline would report agreement it never
    measured, which is the defect class this module exists to catch.
    """
    base = Retriever(harness.chunks, harness.cfg.retrieval)
    arms: dict[str, object] = {
        BASELINE: base,
        NO_RETRIEVAL: NoRetrieval(base),
        WRONG_AGENCY: WrongAgency(base, harness.agencies),
    }
    version = stale_version or next(iter(corpus.list_versions()), None)
    if version is not None:
        arms[STALE_CORPUS] = Retriever(corpus.load_chunks(version), harness.cfg.retrieval)
    return arms


def run_arm(name: str, retriever: object, cases: list[dict], harness: Harness) -> ArmResult:
    result = ArmResult(name)
    for case in cases:
        question = case["turns"][-1] if case.get("turns") else case["question"]
        answered = answer_question(
            question,
            model=harness.model,
            retriever=retriever,  # type: ignore[arg-type]
            cfg=harness.cfg,
        )
        checks = run_checks(
            case,
            answered,
            harness.corpus_doc_ids,
            harness.facts_by_doc,
            doc_texts=harness.doc_texts,
        )
        result.record([(c.name, c.passed) for c in checks])
    return result


def _baseline_problems(baseline: ArmResult) -> list[str]:
    """The controls only prove something if the baseline itself is healthy."""
    problems: list[str] = []
    for check, floor in BASELINE_FLOORS:
        rate = baseline.rate(check)
        if rate is None:
            problems.append(f"baseline never emitted {check}; the controls prove nothing about it")
        elif rate < floor:
            problems.append(
                f"baseline {check} is {rate:.1f}%, under its {floor:.1f}% floor — a baseline "
                "this weak cannot be told apart from a control"
            )
    return problems


def _assertion_problems(assertion: Assertion, arm: ArmResult, baseline: ArmResult) -> list[str]:
    rate = arm.rate(assertion.check)
    if rate is None:
        return [
            f"{assertion.control} never emitted {assertion.check}, so the control "
            "was not actually applied"
        ]
    problems: list[str] = []
    if assertion.max_rate is not None and rate > assertion.max_rate:
        problems.append(
            f"{assertion.control}: {assertion.check} is {rate:.1f}%, above its "
            f"{assertion.max_rate:.1f}% ceiling — {assertion.why}"
        )
    if assertion.min_drop_points is not None:
        base_rate = baseline.rate(assertion.check)
        drop = None if base_rate is None else base_rate - rate
        if drop is None or drop < assertion.min_drop_points:
            shown = "unmeasurable" if drop is None else f"{drop:.1f}"
            problems.append(
                f"{assertion.control}: {assertion.check} is {shown} points below the "
                f"baseline, under the {assertion.min_drop_points:.1f} required — "
                f"{assertion.why}"
            )
    return problems


def violations(arms: dict[str, ArmResult]) -> list[str]:
    """Every direction assertion the run failed to hold. Empty is clean."""
    baseline = arms.get(BASELINE)
    if baseline is None:
        return ["no baseline arm was run, so no control can be interpreted"]
    problems = _baseline_problems(baseline)
    for assertion in ASSERTIONS:
        arm = arms.get(assertion.control)
        if arm is not None:
            problems += _assertion_problems(assertion, arm, baseline)
    return problems


def _reported_checks(arms: dict[str, ArmResult]) -> list[str]:
    named = {a.check for a in ASSERTIONS} | {c for c, _ in BASELINE_FLOORS}
    return sorted(named | {"required_facts_present"})


def render(arms: dict[str, ArmResult]) -> str:
    order = [n for n in (BASELINE, NO_RETRIEVAL, WRONG_AGENCY, STALE_CORPUS) if n in arms]
    width = max(len(c) for c in _reported_checks(arms)) + 2
    lines = ["", "Controls (offline, mock answer model — retrieval is the only variable)", ""]
    header = "check".ljust(width) + "".join(name.rjust(16) for name in order)
    lines += [header, "-" * len(header)]
    for check in _reported_checks(arms):
        row = check.ljust(width)
        for name in order:
            rate = arms[name].rate(check)
            row += ("--" if rate is None else f"{rate:.1f}%").rjust(16)
        lines.append(row)
    cases = "cases passed".ljust(width)
    for name in order:
        arm = arms[name]
        cases += f"{arm.cases_passed}/{arm.cases}".rjust(16)
    lines += [cases, ""]
    lines.append(
        "The case row is reported, never asserted on: offline, a control that "
        "retrieves nothing scores HIGHER than the baseline because every refusal "
        "case passes. Direction is asserted per check, above."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    parser.add_argument("--stale-version", default=None, help="corpus version for stale_corpus")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    from evals.runner import load_suites

    cases = [case for suite in load_suites() for case in suite["cases"]]
    if args.limit:
        cases = cases[: args.limit]
    harness = build_harness()
    arms = {
        name: run_arm(name, retriever, cases, harness)
        for name, retriever in control_retrievers(harness, stale_version=args.stale_version).items()
    }
    print(render(arms))
    problems = violations(arms)
    if args.limit:
        # A sample is not the gate. The floors and drops below were measured
        # over the whole suite, and a 40-case slice can miss them for reasons
        # that have nothing to do with the instrument. Report, do not fail.
        print(f"\nsample run ({len(cases)} case(s)): assertions reported, not enforced.")
        for problem in problems or ["(all direction assertions hold on this sample)"]:
            print(f"  - {problem}")
        return 0
    if problems:
        print("\nCONTROL GATE FAILED — the instrument, not the model:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"\ncontrols: {len(ASSERTIONS)} direction assertion(s) hold over {len(cases)} case(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via tests/test_controls.py
    raise SystemExit(main())


__all__ = [
    "ArmResult",
    "Assertion",
    "Harness",
    "NoRetrieval",
    "WrongAgency",
    "build_harness",
    "control_retrievers",
    "main",
    "render",
    "run_arm",
    "violations",
]
