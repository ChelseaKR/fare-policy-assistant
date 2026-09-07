"""BLOCKING gate over `corpus/processed/facts.jsonl` itself.

`assistant.facts` refuses to publish a row it cannot read into a real
(program, rider_class, price) triple. This gate holds the *committed* corpus
to that contract, because the extractor's own refusal only protects rows it
generates: a hand-edited fact, a `confidence="manual"` row, or a future parser
change can put a sentence fragment back in the program column, and until this
existed nothing would have said so.

Three things are checked.

1. **No published row violates the label contract.** Zero tolerated. A program
   field holding prose ("per week, or", "over the dollar value of pass
   activations needed to be fare capped, the passenger will be refunded the"),
   a bare decimal fragment (",00", left behind when a decimal-comma price was
   split in half), or a rider-class value where a program name belongs is a
   confident, specific, wrong answer about what a bus ride costs.

2. **The refusal count is ratcheted.** Refusals are not failures — a fare page
   written as prose legitimately yields rows the parser cannot place — but the
   count must not grow quietly. `corpus/fact-quality-pin.json` pins the
   ceiling. A corpus refresh that starts producing more unparseable rows fails
   here instead of shipping.

3. **The committed table is reproducible from the committed chunks.** Every
   `confidence="parsed"` row must be exactly what the current extractor
   derives from `chunks.jsonl`. This is what makes 1 and 2 mean anything: a
   gate over a file nobody regenerates measures the file, not the parser.

A refused row is written to `corpus/processed/facts_refused.jsonl` with its
reason. That file existing, and being counted here, is the point: a parser
that silently dropped what it could not read would publish a corpus that
reads as complete, which is the same defect as publishing the garbage, told
the other way round.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import asdict
from pathlib import Path

from assistant import config
from assistant.facts import (
    RefusedRow,
    build_facts_with_refusals,
    load_facts,
    load_refusals,
    refusal_reason,
)
from assistant.ingest import load_chunks


def _pin(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"fact-quality: the ratchet pin is missing at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _violations(facts_path: Path) -> list[tuple[str, str]]:
    """(reason, description) for every committed row that must not be there."""
    out = []
    for fact in load_facts(facts_path):
        reason = refusal_reason(fact)
        if reason is None:
            continue
        out.append(
            (
                reason,
                f"{fact.agency} {fact.chunk_id} price={fact.price} "
                f"program={fact.program!r} rider_class={fact.rider_class!r}",
            )
        )
    return out


def _reproduction_drift(facts_path: Path) -> list[str]:
    """Committed `parsed` rows that the extractor does not derive today."""
    committed = [f for f in load_facts(facts_path) if f.confidence == "parsed"]
    derived, _ = build_facts_with_refusals(load_chunks())
    committed_keys = collections.Counter(json.dumps(asdict(f), sort_keys=True) for f in committed)
    derived_keys = collections.Counter(json.dumps(asdict(f), sort_keys=True) for f in derived)
    messages = []
    for key, count in (committed_keys - derived_keys).items():
        messages.append(f"committed but not derived (x{count}): {key}")
    for key, count in (derived_keys - committed_keys).items():
        messages.append(f"derived but not committed (x{count}): {key}")
    return sorted(messages)[:20]


def _by_reason(refusals: list[RefusedRow]) -> dict[str, int]:
    return dict(sorted(collections.Counter(r.reason for r in refusals).items()))


def _failures(
    facts_path: Path,
    refused_path: Path,
    refusals: list[RefusedRow],
    ceiling: int,
) -> list[str]:
    """Every reason this corpus must not ship, as printable lines."""
    out: list[str] = []

    violations = _violations(facts_path)
    if violations:
        out.append(
            f"{len(violations)} published row(s) violate the label contract "
            f"(assistant.facts.refusal_reason). A price published under a label that "
            f"names nothing is an answer a rider can act on:"
        )
        out += [f"    {reason}: {description}" for reason, description in violations[:20]]
        if len(violations) > 20:
            out.append(f"    ... and {len(violations) - 20} more")

    if len(refusals) > ceiling:
        out.append(
            f"refused rows {len(refusals)} exceeds the pinned ceiling {ceiling}. "
            f"Either the parser lost ground or the corpus changed shape; read "
            f"{refused_path} before re-pinning with --write-pin."
        )

    drift = _reproduction_drift(facts_path)
    if drift:
        out.append(
            f"{facts_path.name} is not what the extractor derives from "
            f"{config.CHUNKS_PATH.name}. Run `make ingest`. Sample:"
        )
        out += [f"    {line}" for line in drift]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-pin",
        action="store_true",
        help="rewrite the ratchet pin from the committed corpus (use when a "
        "corpus refresh legitimately changes the refusal count, and say so in "
        "the pull request)",
    )
    args = parser.parse_args(argv)

    facts_path = config.FACTS_PATH
    refused_path = config.FACTS_REFUSED_PATH
    pin_path = config.FACT_QUALITY_PIN_PATH

    if not facts_path.exists():
        print(f"fact-quality: {facts_path} does not exist", file=sys.stderr)
        return 1
    if not refused_path.exists():
        print(
            f"fact-quality: {refused_path} does not exist. A parser that refuses rows "
            "must record them; run `make ingest`.",
            file=sys.stderr,
        )
        return 1

    refusals = load_refusals(refused_path)
    by_reason = _by_reason(refusals)

    if args.write_pin:
        pin_path.write_text(
            json.dumps(
                {
                    "max_refused_rows": len(refusals),
                    "refused_by_reason": by_reason,
                    "note": (
                        "Ratchet for tools/check_fact_quality.py. The refusal count may "
                        "fall freely; it may only rise with a deliberate re-pin, so a "
                        "corpus refresh that starts producing more unparseable rows is "
                        "loud rather than silent."
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"fact-quality: re-pinned at {len(refusals)} refused rows → {pin_path}")
        return 0

    pin = _pin(pin_path)
    ceiling = pin.get("max_refused_rows")
    if not isinstance(ceiling, int):
        print(f"fact-quality: {pin_path} has no integer max_refused_rows", file=sys.stderr)
        return 1

    failures = _failures(facts_path, refused_path, refusals, ceiling)

    print(f"published fare facts: {len(load_facts(facts_path))}")
    print(f"refused rows: {len(refusals)} (ceiling {ceiling})")
    for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:4d}  {reason}")

    if failures:
        print("\nFACT-QUALITY GATE FAILED", file=sys.stderr)
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    print("fact-quality: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
