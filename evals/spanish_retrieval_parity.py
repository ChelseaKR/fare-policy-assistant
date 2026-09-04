"""Retrieval-side parity: did the Spanish question reach the same evidence?

The bilingual parity gate (`evals/runner.py::check_parity`) compares a Spanish
case's pass/fail against its English mirror's. When it fires, it says *that*
the Spanish path did worse; it cannot say *where*. The failure could be in the
answer model, in the judge, in the guards, or — the possibility this module
exists to rule in or out — in retrieval, before the answer model ever ran.

That distinction decides who fixes it, and it is not academic. On the
2026-09-04 nightly the gate read 29/40 Spanish against 34/40 mirrored English,
a 12.5-point gap. Four of the six diverging pairs turned out to be retrieval:
the Spanish question never reached the passage that carries the answer, so the
model abstained correctly on evidence it should have been given. No amount of
prompt work fixes that, and a parity number alone would have sent someone to
look at the prompt.

## What this measures, and why it is free

Four properties per mirror pair, all of them decided by
`assistant.retrieve` alone — no answer model, no judge, no provider call, no
cost, and byte-identical across runs on a fixed corpus. Run it on a laptop in
seconds, as often as you like:

* **scope** — does `detect_agencies` resolve the same agency for both
  questions? A Spanish question that names an agency the alias table cannot
  match falls back to an unscoped corpus-wide top_k, and the language boost
  (`RetrievalConfig.language_boost`) then fills those slots with Spanish
  documents belonging to *other agencies*. Measured on ml-016 ("¿... el
  autobús de Santa Bárbara?"): three of eight passages came from MST, AC
  Transit, and FAX, because "Santa Bárbara" does not match the ASCII alias
  "santa barbara" and nothing scoped the search to SBMTD.

* **augment** — do the three "close the loop" helpers
  (`_close_the_loop`, `_ensure_eligibility_passage`,
  `_ensure_child_fare_passage`) fire for both questions? Each is gated on a
  query-side regex. When that regex only matches English, the English mirror
  is handed a guaranteed extra passage — the agency's eligibility criterion,
  its application instructions, its child-fare provision — and the Spanish
  case is structurally denied it. That is an equity defect expressed as a
  character class, and pass/fail parity cannot see it.

* **facts** — are the case's own `required_facts` literally present in the
  retrieved passages, for each side? This is retrieval recall of the
  answer-bearing passage, the same question `evals/retrieval_ablation.py`
  asks of BM25-vs-hybrid, asked here per pair instead of per mode. A Spanish
  case whose facts are absent from its own retrieved set cannot pass no
  matter how good the model is; a pair where the facts are present on the
  English side and absent on the Spanish side is a retrieval finding, full
  stop.

* **evidence** — what fraction of the *documents* the English mirror
  retrieved did the Spanish retrieval also reach? Recall against the mirror
  rather than against a hand-labelled gold set, which is the point: the
  English mirror is the parity gate's own definition of what this question
  should have found, so it is the right yardstick and it needs no new
  labelling to stay current as the corpus grows.

The reported top-score ratio (Spanish top BM25 score over English) is context,
not a verdict. It is expected to exceed 1.0 for the two agencies that publish
real Spanish pages (MST, AC Transit) and to sit well below 1.0 where a Spanish
question is scored against English-only documents; a ratio far below 1.0 on a
pair whose facts and evidence are fine is a lead, not a finding.

## What this deliberately does not do

**It does not gate.** `check_parity` is the gate and stays the gate; this is
the instrument you reach for once it has fired. A second blocking check over
the same property would jam the queue on judge noise while telling nobody
anything the first one did not.

**It does not measure answer quality.** Whether the Spanish reads as Spanish a
person wrote is `evals/spanish_quality.py`, and that needs a human rater. A
pair can be clean here and still be answered in stilted, wrongly-registered
Spanish; the two modules answer different questions and neither substitutes
for the other.

**It says nothing about Tagalog.** `evals/suites/stretch_tagalog.yaml` mirrors
English the same way and the same query-side regexes gate it, so the same
defect shape is likely there — but likely is not measured, and this module
looks only at `language: es`.

    uv run python -m evals.spanish_retrieval_parity
    uv run python -m evals.spanish_retrieval_parity --verbose
"""

from __future__ import annotations

import argparse
import re
import sys

from assistant import retrieve
from evals.runner import PARITY_SUITE, load_suites

SPANISH = "es"


def _question(case: dict) -> str:
    """A case's retrieval query: the single question, or a multi-turn case's
    last turn, which is what `Retriever.search` is actually called with."""
    return case.get("question") or case["turns"][-1]


def _fact_present(fact: str, passages: list[str]) -> bool:
    """Is a `required_facts` entry literally present in the retrieved text?

    Same semantics as `evals.retrieval_ablation._fact_in_chunks` on purpose:
    the two modules must agree on what "the retrieval found the fact" means,
    or their recall numbers cannot be read side by side. Kept local rather
    than imported so neither module reaches into the other's privates.
    """
    pattern = fact[3:] if fact.startswith("re:") else re.escape(fact)
    return bool(re.search(pattern, "\n".join(passages), re.I))


def _trigger(name: str, question: str) -> bool | None:
    """Whether one of retrieval's query-side augmentation gates fires.

    Resolved by name at call time, and `None` when the gate no longer exists.
    These are private helpers in another module; a diagnostic that hard-fails
    the build the moment retrieval renames one would be worse than a
    diagnostic that reports the column as unknown.
    """
    fn = getattr(retrieve, name, None)
    return bool(fn(question)) if callable(fn) else None


def mirror_pairs(suite: str = PARITY_SUITE) -> list[tuple[dict, dict]]:
    """Every Spanish case in `suite` paired with its English mirror.

    Mirrors are looked up across all suites, not just this one, because they
    live in groundedness/edge_cases/refusal — the same lookup `parity_delta`
    does over a run's records, done here over the committed cases so this
    needs no run at all.
    """
    cases = {c["id"]: c for s in load_suites() for c in s["cases"]}
    return [
        (case, cases[case["mirror_of"]])
        for case in cases.values()
        if case.get("suite") == suite
        and case.get("language") == SPANISH
        and case.get("mirror_of") in cases
    ]


def compare(retriever: retrieve.Retriever, es_case: dict, en_case: dict) -> dict:
    """The four parity properties for one mirror pair."""
    es_q, en_q = _question(es_case), _question(en_case)
    es_hits = retriever.search(es_q)
    en_hits = retriever.search(en_q)
    es_text = [sc.chunk.text for sc in es_hits]
    en_text = [sc.chunk.text for sc in en_hits]
    es_docs = {sc.chunk.doc_id for sc in es_hits}
    en_docs = {sc.chunk.doc_id for sc in en_hits}

    facts = es_case.get("required_facts") or []
    mirror_facts = en_case.get("required_facts") or []
    return {
        "id": es_case["id"],
        "mirror": en_case["id"],
        "expected": es_case.get("expected_behavior", ""),
        "es_scope": retrieve.detect_agencies(es_q),
        "en_scope": retrieve.detect_agencies(en_q),
        "es_reduced": _trigger("_is_reduced_fare_query", es_q),
        "en_reduced": _trigger("_is_reduced_fare_query", en_q),
        "es_child": _trigger("_is_child_fare_query", es_q),
        "en_child": _trigger("_is_child_fare_query", en_q),
        "es_facts": all(_fact_present(f, es_text) for f in facts) if facts else None,
        "en_facts": all(_fact_present(f, en_text) for f in mirror_facts) if mirror_facts else None,
        "evidence": (len(es_docs & en_docs) / len(en_docs)) if en_docs else 1.0,
        "es_top": es_hits[0].score if es_hits else 0.0,
        "en_top": en_hits[0].score if en_hits else 0.0,
    }


def findings(row: dict) -> list[str]:
    """Everything this pair is short on, in the order it costs recall.

    Only asymmetries count. A trigger that fires for neither language, or a
    fact absent on both sides, is a property of the case or the corpus rather
    than of Spanish, and the parity gate would not read it as an equity gap
    either.

    A case whose expected behavior is a refusal is reported in the table but
    never flagged: retrieval is *supposed* to come back thin on an
    out-of-corpus or determination-seeking question, so "reached less
    evidence" is the correct outcome there rather than a defect. Counting
    those would inflate the finding list with rows nobody should act on,
    which is how a diagnostic stops being read.
    """
    if row["expected"] == "refuse_redirect":
        return []
    out = []
    if row["es_scope"] != row["en_scope"]:
        out.append(f"scope {row['es_scope'] or 'unscoped'} vs mirror {row['en_scope']}")
    for gate in ("reduced", "child"):
        if row[f"en_{gate}"] and not row[f"es_{gate}"]:
            out.append(f"{gate}-fare augmentation fires for the mirror only")
    if row["en_facts"] and row["es_facts"] is False:
        out.append("required facts absent from the Spanish retrieved set, present in the mirror's")
    return out


def report(rows: list[dict], *, verbose: bool = False) -> list[str]:
    """The table and its summary. Returns lines so tests can read them."""
    lines = [
        f"{'spanish case':<18}{'mirror':<24}{'expected':<16}{'scope':<7}{'augment':<9}"
        f"{'facts':<7}{'evidence':<10}{'es/en top':<10}",
        "-" * 101,
    ]
    for row in sorted(rows, key=lambda r: r["id"]):
        scope = "ok" if row["es_scope"] == row["en_scope"] else "DIFF"
        augment = "ok"
        if (row["en_reduced"] and not row["es_reduced"]) or (
            row["en_child"] and not row["es_child"]
        ):
            augment = "EN-ONLY"
        elif row["es_reduced"] is None or row["es_child"] is None:
            augment = "-"
        facts = {True: "ok", False: "MISS", None: "-"}[row["es_facts"]]
        ratio = row["es_top"] / row["en_top"] if row["en_top"] else 0.0
        lines.append(
            f"{row['id']:<18}{row['mirror']:<24}{row['expected']:<16}{scope:<7}{augment:<9}"
            f"{facts:<7}{row['evidence'] * 100:>7.0f}%   {ratio:>7.2f}"
        )

    flagged = [(row, findings(row)) for row in sorted(rows, key=lambda r: r["id"])]
    flagged = [(row, f) for row, f in flagged if f]
    lines.append("")
    if not flagged:
        lines.append(
            f"No retrieval-side asymmetry across {len(rows)} mirror pairs. A parity gap that "
            "survives this is in the answer model, the judge, or the guards — not in retrieval."
        )
    else:
        lines.append(
            f"{len(flagged)} of {len(rows)} mirror pairs reach less evidence in Spanish than "
            "their English mirror reaches. Each line below is a retrieval finding, decided "
            "without a model call:"
        )
        for row, found in flagged:
            for item in found:
                lines.append(f"  {row['id']} (vs {row['mirror']}): {item}")
    if verbose:
        lines.append("")
        for row, _ in flagged:
            lines.append(f"  {row['id']}: es_top={row['es_top']:.1f} en_top={row['en_top']:.1f}")
    mean = sum(r["evidence"] for r in rows) / len(rows) if rows else 0.0
    lines.append("")
    lines.append(
        f"Mean mirror-evidence recall: {mean * 100:.1f}% — the share of the documents each "
        "English mirror retrieved that the Spanish question also reached."
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", action="store_true", help="also print raw top scores for flagged pairs"
    )
    args = parser.parse_args()
    pairs = mirror_pairs()
    if not pairs:
        print("no Spanish/English mirror pairs in the committed suites", file=sys.stderr)
        return 1
    retriever = retrieve.default_retriever()
    rows = [compare(retriever, es, en) for es, en in pairs]
    for line in report(rows, verbose=args.verbose):
        print(line)
    # Diagnostic, never a gate: `check_parity` owns the pass/fail on this
    # property, and a second blocking check over it would jam the queue on
    # judge noise without telling anyone anything new.
    return 0


if __name__ == "__main__":
    sys.exit(main())
