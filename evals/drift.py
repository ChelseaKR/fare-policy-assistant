"""Answer drift across two corpus versions: what a policy change actually altered.

Issue #216. `corpus/versions/` retains every corpus version and
`tools/corpus_refresh_report.py` already tells a reviewer which *documents*
changed and which eval cases now assert a stale fact. Neither says what the
assistant would now tell a rider **differently**, which is the thing a fare-policy
owner reviewing a refresh PR actually needs to see.

This runs the whole case set against both corpus versions through the ordinary
`assistant.answer.answer_question` pipeline — the same prompt, guards, retrieval
and deterministic checks the real run uses — pairs the two answers per case, and
classifies each pair:

* **unchanged** — the two answers are byte-identical.
* **changed_expected** — the answer moved *and* at least one document either
  side cited is in the corpus diff. A source moved, so the answer moving is the
  system working.
* **changed_unexpected** — the answer moved and **no cited document did**. That
  is retrieval (or provider) variance: the instrument drifting rather than the
  policy. This is the one that gates.

`newly_failing` is deliberately **not** a fourth bucket. An answer can be
byte-identical and newly failing — if a document it cites was *removed*,
`citation_present_and_resolvable` fails on the new side over the same text — so
folding it into the partition would have hidden exactly the case a reviewer most
needs. It is reported as a flag on every row and summarised separately.

It is eval-only. EXP-05 forbids rider-facing time travel and this proposes none:
nothing here serves an old corpus to anybody, and no artifact it writes is read
by the answer path.

## Cost

Both sides share one memoising model wrapper keyed on the **rendered prompt**
(`evals.cache.completion_key`), so a case whose retrieved passages did not move
is answered once and reused. On two identical versions the second side therefore
makes **zero** model calls, and on a one-document refresh the cost is bounded to
the cases that document reaches. `model_calls` and `reused` are reported, so a
suspiciously cheap run explains itself.

The default model is the offline `mock` backend (`assistant.models.MockModel`),
which answers only from the passages it was handed — which is the property a
retrieval-drift comparison needs, and which makes the whole tool free and
deterministic. `--live` uses the configured provider instead.

## Scoring

Scoring is `evals.checks.run_checks` — the deterministic half of the harness —
evaluated **per side against that side's own corpus**, so a citation is resolved
against the document set that side actually had. The LLM judges are not run: they
are a separate, paid, non-deterministic layer, and every classification above is
decided by answer text and citations, which need no judge.

    python -m evals.drift --from 35ec70d6359d                  # ... to the live corpus
    python -m evals.drift --from 35ec70d6359d --to 74b05330cb39
    python -m evals.drift --from-snapshot /tmp/old.json --out /tmp/drift.md --json /tmp/drift.json
    python -m evals.drift --from <v> --max-unexpected 3        # gate with headroom

Exit 1 when `changed_unexpected` exceeds `--max-unexpected`, or when the run
could not be interpreted at all (an unreadable version, an empty case set).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from assistant import config, corpus
from assistant import facts as facts_module
from assistant.answer import answer_question
from assistant.ingest import Chunk
from assistant.models import Completion, Model, get_model
from assistant.retrieve import Retriever
from evals.cache import completion_key
from evals.checks import run_checks

UNCHANGED = "unchanged"
CHANGED_EXPECTED = "changed_expected"
CHANGED_UNEXPECTED = "changed_unexpected"
CLASSIFICATIONS = (UNCHANGED, CHANGED_EXPECTED, CHANGED_UNEXPECTED)

#: The live processed corpus, named rather than spelled `None`, so a report can
#: say which side was the working tree without inventing a version id for it.
LIVE = "live"


class DriftError(ValueError):
    """The run could not be interpreted — a bad version, an empty case set."""


# ── the two sides ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Side:
    """One corpus version, and everything scoring a case against it needs.

    `facts_by_doc` is derived from *this side's* chunks with the same
    `assistant.facts` extractor `make ingest` uses, never from the committed
    `facts.jsonl`: that table describes the live corpus only, and scoring an old
    version's answer against the new table would be comparing two rulers.
    """

    version: str
    chunks: list[Chunk]
    retriever: Retriever
    doc_ids: frozenset[str]
    facts_by_doc: dict[str, list[facts_module.FareFact]]
    doc_texts: dict[str, str]


def build_side(version: str, chunks: list[Chunk], cfg: config.Config) -> Side:
    if not chunks:
        raise DriftError(f"corpus version {version!r} holds no chunks; nothing to compare")
    facts_by_doc: dict[str, list[facts_module.FareFact]] = {}
    for fact in facts_module.build_facts(chunks):
        facts_by_doc.setdefault(fact.doc_id, []).append(fact)
    texts: dict[str, list[str]] = {}
    for chunk in chunks:
        texts.setdefault(chunk.doc_id, []).append(chunk.text)
    return Side(
        version=version,
        chunks=chunks,
        retriever=Retriever(chunks, cfg.retrieval),
        doc_ids=frozenset(c.doc_id for c in chunks),
        facts_by_doc=facts_by_doc,
        doc_texts={doc_id: "\n".join(parts) for doc_id, parts in texts.items()},
    )


def load_side(spec: str | None, cfg: config.Config) -> Side:
    """Build a side from an archived corpus version id, or from the live corpus.

    `spec` of ``None`` or :data:`LIVE` means the working tree's processed corpus,
    which is what a corpus-refresh PR has just rewritten and what `--to` defaults
    to. An unknown version id raises `DriftError` naming the known ones rather
    than falling back to the live corpus — a side that silently degraded to the
    other side would report agreement it never measured.
    """
    if spec in (None, LIVE):
        chunks = corpus.load_chunks()
        return build_side(corpus.corpus_version(chunks), chunks, cfg)
    try:
        chunks = corpus.load_chunks(spec)
    except FileNotFoundError as exc:  # not archived
        raise DriftError(str(exc)) from exc
    return build_side(spec, chunks, cfg)


def side_from_snapshot(path: Path, cfg: config.Config) -> Side:
    """Build the `from` side out of a `tools/corpus_refresh_report.py --snapshot-old`
    file, which carries the full pre-refresh chunk set.

    This is the seam the weekly refresh workflow uses: it already writes that
    snapshot before re-processing overwrites the corpus, and the version it
    describes has not been archived yet at the moment the refresh PR is built.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        chunks = [Chunk(**c) for c in payload["chunks"]]
        version = str(payload["corpus_version"])
    except (KeyError, TypeError) as exc:
        raise DriftError(f"{path} is not a corpus snapshot: {exc}") from exc
    return build_side(version, chunks, cfg)


# ── the shared, counting model ───────────────────────────────────────────────


class SharedAnswerModel:
    """One model, memoised on the rendered prompt, shared by both sides.

    The key is `evals.cache.completion_key` over the exact `(system, user,
    max_tokens, temperature)` the pipeline rendered, which already encodes the
    corpus version (the passages are interpolated into it), the prompt version
    and the question. So "the retrieved passages did not move" and "the key is
    the same" are the same statement, and reuse costs nothing in fidelity.

    Counters are per side and are part of the report: a run that reused
    everything made no calls, and saying so is what stops a cheap run from
    reading as a thorough one.
    """

    def __init__(self, inner: Model, *, provider: str, model_id: str):
        self.inner = inner
        self.provider = provider
        self.model_id = model_id
        self._store: dict[str, Completion] = {}
        self.calls: dict[str, int] = {}
        self.reused: dict[str, int] = {}
        self.side = ""

    def for_side(self, side: str) -> None:
        self.side = side
        self.calls.setdefault(side, 0)
        self.reused.setdefault(side, 0)

    def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> Completion:
        key = completion_key(
            kind="answer",
            provider=self.provider,
            model=self.model_id,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        hit = self._store.get(key)
        if hit is not None:
            self.reused[self.side] = self.reused.get(self.side, 0) + 1
            return hit
        completion = self.inner.complete(
            system=system, user=user, max_tokens=max_tokens, temperature=temperature
        )
        self._store[key] = completion
        self.calls[self.side] = self.calls.get(self.side, 0) + 1
        return completion


def build_model(*, live: bool, cfg: config.Config) -> SharedAnswerModel:
    provider, model_id = ("mock", "mock")
    if live:
        provider, model_id = cfg.models.provider, cfg.models.answer_model
    return SharedAnswerModel(get_model(provider, model_id), provider=provider, model_id=model_id)


# ── per-case comparison ──────────────────────────────────────────────────────


@dataclass
class CaseDrift:
    case_id: str
    suite: str
    question: str
    classification: str
    #: Documents in the corpus diff that one side or the other actually cited.
    #: Empty on `changed_unexpected` by definition, and that is the finding.
    explained_by: list[str] = field(default_factory=list)
    cited_from: list[str] = field(default_factory=list)
    cited_to: list[str] = field(default_factory=list)
    passed_from: bool = False
    passed_to: bool = False
    #: Orthogonal to `classification` on purpose — see the module docstring.
    newly_failing: bool = False
    failed_checks_to: list[str] = field(default_factory=list)
    answer_from: str = ""
    answer_to: str = ""


def _case_question(case: dict) -> str:
    turns = case.get("turns")
    return turns[-1] if turns else case["question"]


def changed_doc_ids(diff: dict) -> frozenset[str]:
    """Every document the corpus diff touched, in one set.

    A *removed* document counts: an answer that still cites it has had a source
    move under it just as surely as one whose text was edited, and treating a
    removal as "nothing changed" would file that case as instrument drift.
    """
    return frozenset(diff["added"]) | frozenset(diff["removed"]) | frozenset(diff["changed"])


def compare_case(
    case: dict,
    *,
    from_side: Side,
    to_side: Side,
    model: SharedAnswerModel,
    cfg: config.Config,
    moved: frozenset[str],
) -> CaseDrift:
    answers = {}
    checks = {}
    for name, side in (("from", from_side), ("to", to_side)):
        model.for_side(name)
        answers[name] = answer_question(
            _case_question(case),
            model=model,  # type: ignore[arg-type]
            retriever=side.retriever,
            cfg=cfg,
        )
        checks[name] = run_checks(
            case,
            answers[name],
            set(side.doc_ids),
            side.facts_by_doc,
            doc_texts=side.doc_texts,
        )

    cited_from = sorted(c.doc_id for c in answers["from"].citations)
    cited_to = sorted(c.doc_id for c in answers["to"].citations)
    explained_by = sorted(moved & (set(cited_from) | set(cited_to)))
    if answers["from"].answer == answers["to"].answer:
        classification = UNCHANGED
    elif explained_by:
        classification = CHANGED_EXPECTED
    else:
        classification = CHANGED_UNEXPECTED

    passed_from = all(c.passed for c in checks["from"])
    passed_to = all(c.passed for c in checks["to"])
    return CaseDrift(
        case_id=case.get("id", "<unknown>"),
        suite=case.get("suite", "?"),
        question=_case_question(case),
        classification=classification,
        explained_by=explained_by,
        cited_from=cited_from,
        cited_to=cited_to,
        passed_from=passed_from,
        passed_to=passed_to,
        newly_failing=passed_from and not passed_to,
        failed_checks_to=[c.name for c in checks["to"] if not c.passed],
        answer_from=answers["from"].answer,
        answer_to=answers["to"].answer,
    )


# ── the report ───────────────────────────────────────────────────────────────


@dataclass
class DriftReport:
    from_version: str
    to_version: str
    corpus_diff: dict
    cases: list[CaseDrift]
    model_calls: dict[str, int]
    reused: dict[str, int]

    @property
    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(CLASSIFICATIONS, 0)
        for row in self.cases:
            out[row.classification] += 1
        out["newly_failing"] = sum(1 for row in self.cases if row.newly_failing)
        return out

    def of(self, classification: str) -> list[CaseDrift]:
        return [row for row in self.cases if row.classification == classification]

    def to_json_dict(self) -> dict:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "corpus_diff": self.corpus_diff,
            "counts": self.counts,
            # Provenance: which corpus each side read and what the comparison
            # actually cost, so a report can be re-derived and a cheap run is
            # visibly cheap rather than silently partial.
            "model_calls": self.model_calls,
            "reused": self.reused,
            "cases": [asdict(row) for row in self.cases],
        }


def run_drift(
    cases: list[dict],
    *,
    from_side: Side,
    to_side: Side,
    model: SharedAnswerModel,
    cfg: config.Config,
) -> DriftReport:
    if not cases:
        raise DriftError("no eval cases were selected; there is nothing to compare")
    diff = corpus.diff_corpus(from_side.chunks, to_side.chunks)
    moved = changed_doc_ids(diff)
    rows = [
        compare_case(case, from_side=from_side, to_side=to_side, model=model, cfg=cfg, moved=moved)
        for case in cases
    ]
    return DriftReport(
        from_version=from_side.version,
        to_version=to_side.version,
        corpus_diff=diff,
        cases=rows,
        model_calls=dict(model.calls),
        reused=dict(model.reused),
    )


def _excerpt(text: str, limit: int = 400) -> str:
    """A bounded excerpt, marked as one. An answer truncated without saying so is
    a passage pretending to be complete — the defect #187 fixed elsewhere."""
    text = text.strip() or "(empty)"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f" […truncated, {len(text)} chars total]"


def render_markdown(report: DriftReport, *, max_unexpected: int) -> str:
    counts = report.counts
    diff = report.corpus_diff
    lines = [
        "## Answer drift",
        "",
        f"Corpus `{report.from_version}` → `{report.to_version}`, "
        f"{len(report.cases)} case(s), deterministic checks only (no judge).",
        "",
        "| outcome | cases |",
        "| --- | ---: |",
        f"| unchanged | {counts[UNCHANGED]} |",
        f"| changed, explained by the corpus diff | {counts[CHANGED_EXPECTED]} |",
        f"| **changed with no cited document moving** | **{counts[CHANGED_UNEXPECTED]}** |",
        f"| newly failing a deterministic check | {counts['newly_failing']} |",
        "",
        "`newly failing` is a flag, not a fourth bucket: an answer can be "
        "byte-identical and newly failing when a document it cites was removed.",
        "",
        "### Documents that moved",
        "",
    ]
    for label, key in (("Added", "added"), ("Removed", "removed"), ("Changed", "changed")):
        if diff[key]:
            lines.append(f"- **{label}:** " + ", ".join(f"`{d}`" for d in diff[key]))
    if not changed_doc_ids(diff):
        lines.append("- No document was added, removed, or changed.")
    lines += [
        "",
        f"Model calls: {report.model_calls or '{}'} — reused: {report.reused or '{}'}. "
        "A case whose rendered prompt did not move is answered once and reused, "
        "so the cost is bounded to the cases the changed documents reach.",
        "",
    ]

    unexpected = report.of(CHANGED_UNEXPECTED)
    lines += ["### Changed with no cited document moving", ""]
    if not unexpected:
        lines.append("None. Every answer that moved cites a document that moved. ✅")
    else:
        lines.append(
            f"{len(unexpected)} case(s), against a ceiling of {max_unexpected}. An answer "
            "that moved without a source moving is retrieval or provider variance, "
            "not a policy change."
        )
        for row in unexpected:
            lines += [
                "",
                f"<details><summary><code>{row.suite}::{row.case_id}</code> — "
                f"{row.question}</summary>",
                "",
                f"- cited before: {', '.join(f'`{d}`' for d in row.cited_from) or '(none)'}",
                f"- cited after: {', '.join(f'`{d}`' for d in row.cited_to) or '(none)'}",
                "",
                "**Before**",
                "",
                "```",
                _excerpt(row.answer_from),
                "```",
                "",
                "**After**",
                "",
                "```",
                _excerpt(row.answer_to),
                "```",
                "",
                "</details>",
            ]

    failing = [row for row in report.cases if row.newly_failing]
    lines += ["", "### Newly failing a deterministic check", ""]
    if not failing:
        lines.append("None. ✅")
    else:
        for row in failing:
            lines.append(
                f"- `{row.suite}::{row.case_id}` ({row.classification}) — "
                + ", ".join(row.failed_checks_to)
            )

    changed_expected = report.of(CHANGED_EXPECTED)
    lines += ["", "### Changed, explained by the corpus diff", ""]
    if not changed_expected:
        lines.append("None.")
    else:
        for row in changed_expected:
            lines.append(
                f"- `{row.suite}::{row.case_id}` — cites "
                + ", ".join(f"`{d}`" for d in row.explained_by)
            )
    return "\n".join(lines).rstrip() + "\n"


def gate_problems(report: DriftReport, *, max_unexpected: int) -> list[str]:
    """Every reason this comparison should stop a corpus refresh. Empty is clean."""
    count = report.counts[CHANGED_UNEXPECTED]
    if count > max_unexpected:
        return [
            f"{count} case(s) changed with no cited document moving, above the "
            f"ceiling of {max_unexpected}: the answers moved without a source "
            "moving, which is the instrument drifting rather than the policy."
        ]
    return []


# ── CLI ──────────────────────────────────────────────────────────────────────


def _select_cases(limit: int, suite: str | None) -> list[dict]:
    from evals.runner import load_suites

    cases: list[dict] = []
    for suite_data in load_suites(suite):
        for case in suite_data["cases"]:
            cases.append({**case, "suite": suite_data.get("suite", "?")})
    return cases[:limit] if limit else cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Answer drift across two corpus versions.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from", dest="from_version", help="archived corpus version id")
    source.add_argument(
        "--from-snapshot",
        metavar="PATH",
        help="a tools/corpus_refresh_report.py --snapshot-old file",
    )
    parser.add_argument(
        "--to",
        dest="to_version",
        default=LIVE,
        help="archived corpus version id, or 'live' (default: the working tree's corpus)",
    )
    parser.add_argument("--limit", type=int, default=0, help="compare only the first N cases")
    parser.add_argument("--suite", default=None, help="compare only one suite")
    parser.add_argument("--out", metavar="PATH", help="write the Markdown report here")
    parser.add_argument(
        "--json", dest="json_out", metavar="PATH", help="write the JSON report here"
    )
    parser.add_argument(
        "--max-unexpected",
        type=int,
        default=0,
        help="fail above this many changed-with-no-cited-document-moving cases (default 0)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="answer with the configured provider instead of the offline mock model",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    cfg = config.Config()
    try:
        from_side = (
            side_from_snapshot(Path(args.from_snapshot), cfg)
            if args.from_snapshot
            else load_side(args.from_version, cfg)
        )
        to_side = load_side(args.to_version, cfg)
        report = run_drift(
            _select_cases(args.limit, args.suite),
            from_side=from_side,
            to_side=to_side,
            model=build_model(live=args.live, cfg=cfg),
            cfg=cfg,
        )
    except DriftError as exc:
        print(f"drift: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report, max_unexpected=args.max_unexpected)
    print(markdown)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    problems = gate_problems(report, max_unexpected=args.max_unexpected)
    if args.limit or args.suite:
        # A slice is not the gate. The ceiling is a statement about the whole
        # case set, and a subset can clear or breach it for reasons that have
        # nothing to do with the corpus change. Report, do not fail.
        print(f"\nsample run ({len(report.cases)} case(s)): the ceiling is reported, not enforced.")
        for problem in problems or ["(the unexpected-change ceiling holds on this sample)"]:
            print(f"  - {problem}")
        return 0
    if problems:
        print("\nDRIFT GATE FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via tests/test_drift.py
    raise SystemExit(main())


__all__ = [
    "CHANGED_EXPECTED",
    "CHANGED_UNEXPECTED",
    "LIVE",
    "UNCHANGED",
    "CaseDrift",
    "DriftError",
    "DriftReport",
    "SharedAnswerModel",
    "Side",
    "build_model",
    "build_side",
    "changed_doc_ids",
    "compare_case",
    "gate_problems",
    "load_side",
    "main",
    "render_markdown",
    "run_drift",
    "side_from_snapshot",
]
