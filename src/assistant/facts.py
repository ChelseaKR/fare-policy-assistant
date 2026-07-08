"""Structured fare-fact extraction (EXP-01, docs/ideation/03-expansions.md).

Turns the price/age figures buried in fare-table chunks into a typed,
queryable table (`FareFact`) so a numeric claim in an answer ("the discount
monthly pass is $35") can be checked against the corpus deterministically
instead of only by the LLM judge. See `evals.checks.run_checks`'s
`fare_facts_consistent` check for the check that consumes this table.

Extraction is conservative by design: a row is only emitted when a price or
age bound is unambiguously tied to a chunk by one of three layout patterns
observed in the pilot corpus (`_extract_pipe_table`, `_extract_label_price_
blocks`, `_extract_inline_price_runs`). A dollar amount or age the parser
can't confidently place is left out rather than guessed at — a false
negative (an unverified true claim) just means that claim keeps relying on
the judge, which is the status quo; a false positive (a wrong fact) would
actively teach the check to wave through bad answers, which is worse than
not having the check. Every row carries `confidence`: "parsed" for anything
this module extracted automatically. There is no automated "manual" path —
if a document needs a hand-verified correction, add a row with
`confidence="manual"` directly to `corpus/processed/facts.jsonl`; rerunning
`python -m assistant.ingest process` preserves existing manual rows (see
`merge_manual_rows`) and only re-derives the "parsed" ones.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

# A rider-class / column header names a class of rider, not a program. These
# keywords distinguish "Senior (age 62+) - Discount" (a rider-class header)
# from "Monthly GoPass (31 Days)" (a program label) when a line contains
# neither a price nor a pipe.
_RIDER_CLASS_KEYWORDS = re.compile(
    r"\b(seniors?|discounts?|regular|basic|adults?|youths?|students?|disab\w*|"
    r"medicare|veterans?|military|reduced|super senior|tk\s*-\s*12|k\s*-\s*12)\b",
    re.I,
)
_PROGRAM_KEYWORDS = re.compile(
    r"\b(pass|ticket|ride|fare|transfer|token|card|voucher|upgrade)\b", re.I
)
_PRICE_RE = re.compile(r"\$\s?\d+(?:\.\d{2})?")
_PRICE_FULL_RE = re.compile(r"^\$\s?\d+(?:\.\d{2})?$")


def _parse_price(text: str) -> float | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return round(float(m.group().replace("$", "").strip()), 2)


# Age patterns, checked in order against a candidate line/label. Each entry is
# (regex, extractor) where extractor(match) -> (age_min, age_max).
_AGE_PATTERNS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"age\s*(\d{1,3})\s*\+", re.I), lambda m: (int(m.group(1)), None)),
    (re.compile(r"\((\d{1,3})\s*\+\)", re.I), lambda m: (int(m.group(1)), None)),
    (
        re.compile(r"(\d{1,3})\s*years?\s*(?:of age\s*)?and\s*(?:older|over)", re.I),
        lambda m: (int(m.group(1)), None),
    ),
    (
        re.compile(r"(\d{1,3})\s*years?\s*(?:of age\s*)?and\s*under", re.I),
        lambda m: (None, int(m.group(1))),
    ),
    (re.compile(r"under\s*(\d{1,3})\b", re.I), lambda m: (None, int(m.group(1)) - 1)),
    (
        re.compile(r"ages?\s*(\d{1,3})\s*-\s*(\d{1,3})", re.I),
        lambda m: (int(m.group(1)), int(m.group(2))),
    ),
    (
        re.compile(r"\((\d{1,3})\s*-\s*(\d{1,3})\)", re.I),
        lambda m: (int(m.group(1)), int(m.group(2))),
    ),
]


def _parse_age(text: str) -> tuple[int | None, int | None]:
    for pattern, extractor in _AGE_PATTERNS:
        m = pattern.search(text)
        if m:
            return extractor(m)  # type: ignore[operator]
    return (None, None)


def _clean_label(text: str) -> str:
    text = re.sub(r"[*†‡]+", "", text)
    return re.sub(r"\s+", " ", text).strip(" -:")


@dataclass
class FareFact:
    agency: str
    doc_id: str
    chunk_id: str
    program: str
    rider_class: str
    price: float | None
    currency: str
    age_min: int | None
    age_max: int | None
    confidence: str  # "parsed" | "manual"


def _is_rider_class_header(line: str) -> bool:
    if _PRICE_RE.search(line) or "|" in line:
        return False
    return bool(_RIDER_CLASS_KEYWORDS.search(line)) and not _PROGRAM_KEYWORDS.search(line)


def _is_plain_label(line: str) -> bool:
    return bool(line) and not _PRICE_RE.search(line) and "|" not in line


def _looks_like_program_label(line: str) -> bool:
    """A plain label short and title-like enough to safely defer-pair in grid
    mode (see `_extract_label_price_blocks`). Prose sentences (which also
    pass `_is_plain_label`) are excluded so they can't pollute the pending
    label queue and shift a later price onto the wrong program."""
    if not _is_plain_label(line) or len(line) > 60 or ". " in line:
        return False
    return line[-1] not in ".!?,;"


def _extract_pipe_table(
    agency: str, doc_id: str, chunk_id: str, lines: list[str]
) -> list[FareFact]:
    """Pipe-delimited tables (`Label | $x | $y`), header-column-aware.

    A header row is the most recent all-non-price pipe row. When a data row's
    price-cell count matches the header's column count, each price is scoped
    to its header column (a rider-class / payment-method label carrying its
    own age hint if present); a mismatched cell count is emitted with an
    unscoped rider_class rather than guessed at.
    """
    facts: list[FareFact] = []
    header_cols: list[str] | None = None
    for line in lines:
        if "|" not in line:
            continue
        cells = [_clean_label(c) for c in line.split("|")]
        if not any(_PRICE_RE.search(c) for c in cells):
            header_cols = cells
            continue
        program, *rest = cells
        if not program or not rest:
            continue
        prices = [c for c in rest if _PRICE_FULL_RE.match(c) or _PRICE_RE.search(c)]
        if header_cols and len(prices) == len(header_cols):
            for col, cell in zip(header_cols, prices, strict=True):
                price = _parse_price(cell)
                if price is None:
                    continue
                age_min, age_max = _parse_age(col)
                facts.append(
                    FareFact(
                        agency=agency,
                        doc_id=doc_id,
                        chunk_id=chunk_id,
                        program=program,
                        rider_class=col,
                        price=price,
                        currency="USD",
                        age_min=age_min,
                        age_max=age_max,
                        confidence="parsed",
                    )
                )
        else:
            for cell in prices:
                price = _parse_price(cell)
                if price is None:
                    continue
                facts.append(
                    FareFact(
                        agency=agency,
                        doc_id=doc_id,
                        chunk_id=chunk_id,
                        program=program,
                        rider_class="",
                        price=price,
                        currency="USD",
                        age_min=None,
                        age_max=None,
                        confidence="parsed",
                    )
                )
    return facts


def _extract_label_price_blocks(
    agency: str, doc_id: str, chunk_id: str, lines: list[str]
) -> list[FareFact]:
    """Two label/price layouts, tried line by line in priority order:

    1. Direct pairing — a label line immediately followed by its own price
       line ("Single Ride Ticket" / "$2.50"). Applied whenever the very next
       line is a price, regardless of whether a rider-class header has been
       seen yet, so a program listed with no header above it (e.g. a section
       whose heading *is* the rider class, like SacRT's "Students (TK-12) -
       Discount") still gets its price attached to the right label instead of
       shifting onto its neighbor.
    2. Column-major grid — N program labels listed together with no price
       ("Single Ride 2 hours", "Daily GoPass...", ...), then a rider-class
       header, then exactly N consecutive price lines in the same order
       (MST's fare table). Only short, title-like lines are ever queued as
       grid labels (`_looks_like_program_label`) so prose paragraphs can't be
       mistaken for pending labels; a direct pairing anywhere clears the
       queue, since it proves the surrounding layout is not grid-shaped.
    """
    facts: list[FareFact] = []
    pending_labels: list[str] = []
    current_class = ""
    current_age: tuple[int | None, int | None] = (None, None)
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None

        if _is_rider_class_header(line):
            current_class = _clean_label(line)
            current_age = _parse_age(line)
            if (
                len(pending_labels) >= 2
                and i + len(pending_labels) < len(lines)
                and all(_PRICE_RE.search(lines[i + 1 + k]) for k in range(len(pending_labels)))
            ):
                for k, program in enumerate(pending_labels):
                    price = _parse_price(lines[i + 1 + k])
                    if price is None:
                        continue
                    facts.append(
                        FareFact(
                            agency=agency,
                            doc_id=doc_id,
                            chunk_id=chunk_id,
                            program=program,
                            rider_class=current_class,
                            price=price,
                            currency="USD",
                            age_min=current_age[0],
                            age_max=current_age[1],
                            confidence="parsed",
                        )
                    )
                i += 1 + len(pending_labels)
                continue
            i += 1
            continue

        if _is_plain_label(line) and nxt is not None and _PRICE_RE.fullmatch(nxt):
            price = _parse_price(nxt)
            if price is not None:
                facts.append(
                    FareFact(
                        agency=agency,
                        doc_id=doc_id,
                        chunk_id=chunk_id,
                        program=_clean_label(line),
                        rider_class=current_class,
                        price=price,
                        currency="USD",
                        age_min=current_age[0],
                        age_max=current_age[1],
                        confidence="parsed",
                    )
                )
            pending_labels = []
            i += 2
            continue

        if _looks_like_program_label(line):
            pending_labels.append(_clean_label(line))
            i += 1
            continue

        i += 1
    return facts


def _extract_inline_price_runs(
    agency: str, doc_id: str, chunk_id: str, text: str, exclude: set[float]
) -> list[FareFact]:
    """Fallback: every `$amount` in the chunk not already captured by a
    structured pass becomes a row scoped to the doc, with the run of text up
    to the next `$amount` as its (best-effort) program/rider-class label. This
    is what guarantees "consistent with the cited doc" coverage even on prose
    layouts (SBMTD's `$2.50 Regular one-way Youth (K-12th grade)` style) that
    the structured passes don't model.
    """
    facts: list[FareFact] = []
    matches = list(_PRICE_RE.finditer(text))
    for idx, m in enumerate(matches):
        # "$3.0 million" is a budget figure, not a rider-facing price; parsing
        # it as price=3.0 would plant a false fact (sbmtd-farechange's fare
        # equity narrative is full of these). Skip anything magnitude-qualified.
        if re.search(
            r"^[\d.,]{0,4}\s*(million|billion|thousand|millones?|mil millones)",
            text[m.end() : m.end() + 20],
            re.I,
        ):
            continue
        price = _parse_price(m.group())
        if price is None or price in exclude:
            continue
        end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(text), m.end() + 160)
        label = _clean_label(text[m.end() : end])
        label = label.split("\n")[0][:120]
        rider_match = _RIDER_CLASS_KEYWORDS.search(label)
        age_min, age_max = _parse_age(label)
        facts.append(
            FareFact(
                agency=agency,
                doc_id=doc_id,
                chunk_id=chunk_id,
                program=label or "(unspecified)",
                rider_class=rider_match.group(0).lower() if rider_match else "",
                price=price,
                currency="USD",
                age_min=age_min,
                age_max=age_max,
                confidence="parsed",
            )
        )
    return facts


def _extract_age_only(agency: str, doc_id: str, chunk_id: str, lines: list[str]) -> list[FareFact]:
    """Age-eligibility statements with no attached price (e.g. MST's Discount
    Eligibility bullet list: "65 years and older", "18 years and under").
    Only emitted for lines that carry no price, so they never collide with
    the price-bearing extraction passes above.
    """
    facts: list[FareFact] = []
    for line in lines:
        if _PRICE_RE.search(line) or "|" in line:
            continue
        age_min, age_max = _parse_age(line)
        if age_min is None and age_max is None:
            continue
        rider_match = _RIDER_CLASS_KEYWORDS.search(line)
        rider_class = rider_match.group(0).lower() if rider_match else _clean_label(line)[:60]
        facts.append(
            FareFact(
                agency=agency,
                doc_id=doc_id,
                chunk_id=chunk_id,
                program="",
                rider_class=rider_class,
                price=None,
                currency="USD",
                age_min=age_min,
                age_max=age_max,
                confidence="parsed",
            )
        )
    return facts


def extract_chunk_facts(agency: str, doc_id: str, chunk_id: str, text: str) -> list[FareFact]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    facts: list[FareFact] = []

    if any("|" in ln for ln in lines):
        facts += _extract_pipe_table(agency, doc_id, chunk_id, lines)
    facts += _extract_label_price_blocks(agency, doc_id, chunk_id, lines)
    facts += _extract_age_only(agency, doc_id, chunk_id, lines)

    already = {f.price for f in facts if f.price is not None}
    facts += _extract_inline_price_runs(agency, doc_id, chunk_id, text, already)
    return facts


def build_facts(chunks) -> list[FareFact]:
    """Automated extraction pass over every chunk. `chunks` are
    `assistant.ingest.Chunk` (or anything with the same attributes)."""
    facts: list[FareFact] = []
    for chunk in chunks:
        facts += extract_chunk_facts(chunk.agency, chunk.doc_id, chunk.chunk_id, chunk.text)
    return facts


def load_facts(path: Path) -> list[FareFact]:
    if not path.exists():
        return []
    facts = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                facts.append(FareFact(**json.loads(line)))
    return facts


def merge_manual_rows(parsed: list[FareFact], existing_path: Path) -> list[FareFact]:
    """Automated rows plus any hand-curated `confidence="manual"` rows already
    committed at `existing_path`, so a manual correction survives re-running
    `python -m assistant.ingest process`."""
    manual = [f for f in load_facts(existing_path) if f.confidence == "manual"]
    return parsed + manual


def write_facts(facts: list[FareFact], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for fact in facts:
            f.write(json.dumps(asdict(fact), ensure_ascii=False) + "\n")


# ── answer-side claim parsing, used by evals.checks ─────────────────────────
#
# Reuses the same price/age regexes the extractor uses on the corpus, so a
# claim is recognized in an answer with exactly the vocabulary it would have
# been recognized with in the source document.


def parse_price_claims(answer_text: str) -> list[float]:
    """Every `$amount` a rider-facing answer states, as floats."""
    out = []
    for m in _PRICE_RE.finditer(answer_text):
        price = _parse_price(m.group())
        if price is not None:
            out.append(price)
    return out


def parse_age_claims(answer_text: str) -> list[tuple[int | None, int | None]]:
    """Every age bound ("65+", "18 and under", "ages 19-61", ...) an answer
    states, as (age_min, age_max) tuples. Unlike `_parse_age` (first match
    only, used while extracting one label at a time from the corpus) this
    scans the whole answer and returns every match, since an answer commonly
    states more than one age bound (e.g. a senior threshold and a youth one).
    """
    out: list[tuple[int | None, int | None]] = []
    for pattern, extractor in _AGE_PATTERNS:
        for m in pattern.finditer(answer_text):
            out.append(extractor(m))  # type: ignore[operator]
    return out
