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
not having the check.

That was the design and it was not what the code did. The fallback pass
labels a price with the run of text after it, which on a paragraph is a
sentence fragment, and the row went out with the same `confidence="parsed"`
as one read from a fare grid. `program="per week, or" price=20.00`;
`program="a" price=1.75`; and, on MST's Spanish page, `program=",00 — Pago
sin contacto …" rider_class="regular" price=35.00`, which asserted the
discount monthly fare as the regular one. So the design is now enforced
rather than described: `refusal_reason` holds every parsed row to a label
contract before it is published, and `partition_facts` splits the candidates
into what ships and what is refused. Refusals are written to
`corpus/processed/facts_refused.jsonl` with a reason and counted by `make
ingest` — dropping them silently would publish a corpus that reads as
complete, which is the same lie told the other way round. `make
fact-quality` (tools/check_fact_quality.py) holds the committed table to all
of this.

Every row carries `confidence`: "parsed" for anything
this module extracted automatically. There is no automated "manual" path —
if a document needs a hand-verified correction, add a row with
`confidence="manual"` directly to `corpus/processed/facts.jsonl`; rerunning
`python -m assistant.ingest process` preserves existing manual rows (see
`merge_manual_rows`) and only re-derives the "parsed" ones.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

# A rider-class / column header names a class of rider, not a program. These
# keywords distinguish "Senior (age 62+) - Discount" (a rider-class header)
# from "Monthly GoPass (31 Days)" (a program label) when a line contains
# neither a price nor a pipe.
#
# Both vocabularies carry the Spanish terms alongside the English ones. The
# Spanish fare pages are laid out identically to their English originals —
# MST's is the same grid of program labels, a rider-class header, then one
# price per label — but the header reads "Descuento Ruta fija", not "Discount
# Fixed Route". With only English keywords the grid pass recognised the
# `Regular` header (a word both languages share) and nothing else, so the
# discount half of every Spanish fare table fell through to the prose
# fallback. Recognising the header is what lets a Spanish price attach to the
# program it belongs to instead of to the run of text after it.
_RIDER_CLASS_KEYWORDS = re.compile(
    r"\b(seniors?|discounts?|regular|basic|adults?|youths?|students?|disab\w*|"
    r"medicare|veterans?|military|reduced|super senior|tk\s*-\s*12|k\s*-\s*12|"
    r"descuentos?|adultos?|j[oó]ven(?:es)?|mayores|estudiantes?|"
    r"discapacidad\w*|veteranos?|reducid[ao]s?|ni[nñ]os?|menores)\b",
    re.I,
)
_PROGRAM_KEYWORDS = re.compile(
    r"\b(pass|ticket|ride|fare|transfer|token|card|voucher|upgrade|"
    r"pase|pases|boleto|billete|tarjeta|tarifas?|viajes?|transbordo|abono)\b",
    re.I,
)
# A money token, written for both decimal conventions the corpus actually
# contains. The Spanish pages use a decimal comma ("$ 35,00" is thirty-five
# dollars), the English pages a decimal point, and both use a comma as a
# thousands separator ("$100,000"). Until 2026-09-07 the pattern was
# `\$\s?\d+(?:\.\d{2})?`, which stopped at the comma: it read "$ 35,00" as
# `$ 35` and left the orphan `,00` to be picked up as the *label* of the next
# row. That is how MST's Spanish page came to assert a $35 "regular" monthly
# fare when the regular monthly fare is $70 — the discount price presented as
# the regular one, on the page read by the riders least able to check it.
#
# The two alternatives are ordered so the thousands form is tried first:
# `\d{1,3}(?:,\d{3})+` requires groups of exactly three, and a trailing
# `(?!\d)` stops "$100,000" being taken as "$100". A two-digit group after
# either separator is a decimal fraction; three digits after a comma are a
# thousands group. That is the ordinary convention in both locales and it is
# the only reading under which every amount in the committed corpus is
# self-consistent.
_PRICE_BODY = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:[.,]\d{2})?)(?!\d)"
_PRICE_RE = re.compile(r"\$\s?" + _PRICE_BODY)
_PRICE_FULL_RE = re.compile(r"^\$\s?" + _PRICE_BODY + r"$")
# A comma is a thousands separator only when exactly three digits follow it
# and no further digit does; anything else is a decimal comma.
_THOUSANDS_SEPARATOR_RE = re.compile(r",(?=\d{3}(?:\D|$))")


def _price_value(token: str) -> float | None:
    """The numeric value of one matched money token, in either convention."""
    body = _THOUSANDS_SEPARATOR_RE.sub("", token.replace("$", "").strip())
    try:
        return round(float(body.replace(",", ".")), 2)
    except ValueError:  # pragma: no cover - the pattern cannot produce this
        return None


def _parse_price(text: str) -> float | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return _price_value(m.group())


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


#: A footnote marker separates a label from the footnote that qualifies it.
_FOOTNOTE_MARKER_RE = re.compile(r"[*†‡]+")


def _clean_label(text: str) -> str:
    text = _FOOTNOTE_MARKER_RE.sub("", text)
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


@dataclass
class RefusedRow:
    """A candidate row the extractor built and then declined to publish.

    Refusals are written to `corpus/processed/facts_refused.jsonl` and counted
    in the ingest output. Dropping them silently would be the same defect as
    publishing them: the corpus would read as if the page contained nothing
    the parser could not handle, when in fact it contained something the
    parser handled wrongly.
    """

    agency: str
    doc_id: str
    chunk_id: str
    reason: str
    program: str
    rider_class: str
    price: float | None


# ── the publication contract ────────────────────────────────────────────────
#
# The fallback pass (`_extract_inline_price_runs`) labels a price with the run
# of text that follows it. On a fare table that run is the program name. On a
# paragraph it is a sentence fragment, and the row that results reads as a
# fact: SBMTD `program="a"  price=1.75`, MST `program="per week, or"
# price=20.00`, MST's Spanish page `program=",00"  price=35.00`. Each of those
# is a real price bound to a label that names nothing, published with the same
# `confidence="parsed"` as a row read straight out of a fare grid.
#
# That is this portfolio's dominant defect class — absence rendered as a value
# — and here the value is a bus fare a rider might budget around. So a
# candidate row now has to earn publication: a price must arrive attached to a
# label that is a *label*. What cannot is refused, recorded, and counted.
#
# The rules below are deliberately shape-based rather than a blocklist of
# observed fragments. A blocklist would pass the next document.

#: Longest a program or rider-class label may be. Every hand-checked label in
#: the committed corpus is under this; the shortest prose fragment the
#: fallback produced is well over it. Sentence-length is the signal, and 80
#: characters is where the two populations separate.
_LABEL_MAX_CHARS = 80

#: A label may not end on a word that is waiting for the rest of its sentence.
_DANGLING_TAIL_RE = re.compile(
    r"\b(and|or|the|a|an|of|to|for|per|with|in|on|at|by|from|than|that|is|are|"
    r"was|were|be|will|would|pay|y|o|de|del|la|el|los|las|un|una|por|para|con|"
    r"en|que|se|su|sus)$",
    re.I,
)
#: ... nor may it end on punctuation that continues a clause or a table row.
_DANGLING_PUNCTUATION = ",;:/&+-–—|"
#: A label whose first character is not a letter or digit is a fragment cut
#: out of the middle of something (",00", ") and checks ...", "— Pago sin
#: contacto ...", "(all rides unless eligible for a reduced fare)").
_LABEL_START_RE = re.compile(r"^\w", re.UNICODE)
#: Terminal sentence punctuation, checked only on labels long enough that the
#: period cannot be an abbreviation ("Full – Ages 18 to 59." is a fare class;
#: "Good on all RTA and South County routes for 7 consecutive days." is a
#: sentence that was published as the name of a fare product).
_TERMINAL_PUNCTUATION = ".!?"
#: A bare decimal tail, which is what a mis-split money token leaves behind.
_DECIMAL_FRAGMENT_RE = re.compile(r"^[.,]\s?\d")
#: Two sentences in one label. Only applied above `_SENTENCE_SCAN_MIN_CHARS`,
#: because short labels legitimately contain an abbreviating period
#: ("St. Helena" is a VINE destination, not a paragraph).
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+\S")
_SENTENCE_SCAN_MIN_CHARS = 24
#: The fallback's placeholder for "a price with no label at all". It is the
#: literal shape of absence rendered as a value, and it is never publishable.
_UNLABELLED = "(unspecified)"
#: One character is a table sentinel, not a name. VineGo's fare matrix marks
#: unavailable city pairs with a bare "X"; read as a program label that is a
#: $4.00 fare for a program called X.
_LABEL_MIN_CHARS = 2


#: Ordered (reason, predicate) rules. First match wins, so the more specific
#: shapes come before the general ones.
_LABEL_RULES: tuple[tuple[str, object], ...] = (
    ("unlabelled_price", lambda s: s == _UNLABELLED),
    ("decimal_fragment", lambda s: bool(_DECIMAL_FRAGMENT_RE.match(s))),
    ("fragment_start", lambda s: not _LABEL_START_RE.match(s)),
    ("too_short", lambda s: len(s) < _LABEL_MIN_CHARS),
    ("sentence_length", lambda s: len(s) > _LABEL_MAX_CHARS),
    ("dangling_punctuation", lambda s: s[-1] in _DANGLING_PUNCTUATION),
    ("dangling_word", lambda s: bool(_DANGLING_TAIL_RE.search(s))),
    ("mid_sentence", lambda s: s[0].islower() and " " in s),
    (
        "complete_sentence",
        lambda s: len(s) >= _SENTENCE_SCAN_MIN_CHARS and s[-1] in _TERMINAL_PUNCTUATION,
    ),
    (
        "multiple_sentences",
        lambda s: len(s) >= _SENTENCE_SCAN_MIN_CHARS and bool(_SENTENCE_BOUNDARY_RE.search(s)),
    ),
)


def label_defect(label: str) -> str | None:
    """The reason `label` is not usable as a program or rider-class name, or
    `None` if it is. Empty is not a defect here — a row may legitimately carry
    only one of the two — the caller decides whether an empty label is enough.
    """
    label = label.strip()
    if not label:
        return None
    for reason, fails in _LABEL_RULES:
        if fails(label):  # type: ignore[operator]
            return reason
    return None


def refusal_reason(fact: FareFact) -> str | None:
    """Why `fact` must not be published, or `None` if it may be.

    A hand-curated `confidence="manual"` row is a maintainer's own assertion
    and is never second-guessed here; this gate exists to police what the
    parser guesses.
    """
    if fact.confidence != "parsed":
        return None
    defect = label_defect(fact.program)
    if defect is not None:
        return f"program_{defect}"
    defect = label_defect(fact.rider_class)
    if defect is not None:
        return f"rider_class_{defect}"
    if fact.price is not None and not fact.program.strip() and not fact.rider_class.strip():
        # A price with neither a program nor a rider class names no fare at
        # all. It would still satisfy a price-membership check, which is
        # exactly what makes it dangerous.
        return "price_without_label"
    if fact.price is None and fact.age_min is None and fact.age_max is None:
        return "empty_row"
    return None


def partition_facts(facts: list[FareFact]) -> tuple[list[FareFact], list[RefusedRow]]:
    """Split candidate rows into the publishable ones and the refused ones."""
    published: list[FareFact] = []
    refused: list[RefusedRow] = []
    for fact in facts:
        reason = refusal_reason(fact)
        if reason is None:
            published.append(fact)
            continue
        refused.append(
            RefusedRow(
                agency=fact.agency,
                doc_id=fact.doc_id,
                chunk_id=fact.chunk_id,
                reason=reason,
                program=fact.program,
                rider_class=fact.rider_class,
                price=fact.price,
            )
        )
    return published, refused


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


def _is_rider_class_label(label: str) -> bool:
    """`label` names a class of rider rather than a fare product."""
    return bool(_RIDER_CLASS_KEYWORDS.search(label)) and not _PROGRAM_KEYWORDS.search(label)


def _header_axis_is_the_program(header_cols: list[str] | None) -> bool:
    """True when a pipe table is laid out rider-class-down, program-across.

    The default reading — row label is the program, column header is the
    rider class — is Yolobus's ("Local Fare | $2.00 | $1.00" under "Regular
    Adult (19-61) | Senior/Disabled (62+)"). VINE's passes table is the
    transpose: the row labels are "Adult (19-64)", "Youth (6-18)", "Half" and
    the columns are "Day Pass", "20-Ride Pass", "31-Day Pass". Read the
    default way it produced 12 rows whose `program` was a rider class and
    whose `rider_class` was a program — a fare table with its two axes
    swapped, published as fact.

    The decision is made from the header alone, so every row of one table is
    read the same way. Deciding it per row instead left VINE's "Half" row
    transposed relative to the two rows directly above it, because "Half" is
    not a word this module recognises as a rider class — one table published
    with its axes disagreeing between adjacent rows.
    """
    if not header_cols:
        return False
    return all(
        bool(_PROGRAM_KEYWORDS.search(col)) and not _RIDER_CLASS_KEYWORDS.search(col)
        for col in header_cols
    )


def _is_symmetric_matrix_row(row_label: str, header_cols: list[str] | None) -> bool:
    """True when the row label is itself one of the table's column headers.

    That is the signature of a matrix indexed the same way on both axes —
    VineGo publishes its paratransit fares as origin city down, destination
    city across, with "Napa" appearing as both. Neither axis is a program and
    neither is a rider class, so no reading of such a row produces the triple
    this table is defined to hold. It used to yield 42 rows asserting a
    program called "Calistoga" and a rider class called "Kaiser Vallejo".
    """
    if not header_cols:
        return False
    folded = {col.casefold() for col in header_cols if col}
    return row_label.casefold() in folded


def _matrix_refusals(
    agency: str, doc_id: str, chunk_id: str, program: str, prices: list[str]
) -> list[RefusedRow]:
    return [
        RefusedRow(
            agency=agency,
            doc_id=doc_id,
            chunk_id=chunk_id,
            reason="matrix_axis_is_not_a_program",
            program=program,
            rider_class="",
            price=_parse_price(cell),
        )
        for cell in prices
    ]


def _scoped_rows(
    agency: str,
    doc_id: str,
    chunk_id: str,
    program: str,
    header_cols: list[str],
    prices: list[str],
) -> list[FareFact]:
    """One row per priced column, each scoped to its own header column."""
    transposed = _header_axis_is_the_program(header_cols)
    rows: list[FareFact] = []
    for col, cell in zip(header_cols, prices, strict=True):
        price = _parse_price(cell)
        if price is None:
            continue
        class_label = program if transposed else col
        age_min, age_max = _parse_age(class_label)
        rows.append(
            FareFact(
                agency=agency,
                doc_id=doc_id,
                chunk_id=chunk_id,
                program=col if transposed else program,
                rider_class=class_label,
                price=price,
                currency="USD",
                age_min=age_min,
                age_max=age_max,
                confidence="parsed",
            )
        )
    return rows


def _unscoped_rows(
    agency: str, doc_id: str, chunk_id: str, program: str, prices: list[str]
) -> list[FareFact]:
    """No usable header, so there is no second axis to scope against.

    The one label the row does carry still belongs in the column that
    describes it: VINE's fares table is a bare "Adult (19-64) | $2.00" list,
    and filing "Adult (19-64)" as a *program* is how 74 of that agency's 95
    rows came to carry a rider class in the program field with the
    rider-class field left empty.
    """
    is_class = _is_rider_class_label(program)
    age_min, age_max = _parse_age(program) if is_class else (None, None)
    rows: list[FareFact] = []
    for cell in prices:
        price = _parse_price(cell)
        if price is None:
            continue
        rows.append(
            FareFact(
                agency=agency,
                doc_id=doc_id,
                chunk_id=chunk_id,
                program="" if is_class else program,
                rider_class=program if is_class else "",
                price=price,
                currency="USD",
                age_min=age_min,
                age_max=age_max,
                confidence="parsed",
            )
        )
    return rows


def _extract_pipe_table(
    agency: str, doc_id: str, chunk_id: str, lines: list[str]
) -> tuple[list[FareFact], list[RefusedRow]]:
    """Pipe-delimited tables (`Label | $x | $y`), header-column-aware.

    A header row is the most recent all-non-price pipe row. When a data row's
    price-cell count matches the header's column count, each price is scoped
    to its header column (a rider-class / payment-method label carrying its
    own age hint if present); a mismatched cell count is emitted with an
    unscoped rider_class rather than guessed at. When every column names a
    fare product, the table is read transposed
    (`_header_axis_is_the_program`).

    A row whose label is one of the table's own column headers is refused
    outright (`_is_symmetric_matrix_row`); the refusal is returned rather than
    dropped.
    """
    facts: list[FareFact] = []
    refused: list[RefusedRow] = []
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
        if _is_symmetric_matrix_row(program, header_cols):
            refused += _matrix_refusals(agency, doc_id, chunk_id, program, prices)
        elif header_cols and len(prices) == len(header_cols):
            facts += _scoped_rows(agency, doc_id, chunk_id, program, header_cols, prices)
        else:
            facts += _unscoped_rows(agency, doc_id, chunk_id, program, prices)
    return facts, refused


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
        run = text[m.end() : end].split("\n")[0]
        # A footnote marker ends the label and starts the footnote. SBMTD
        # writes "$8.50 Senior (65+)* Mobility (For Disabled Persons and
        # Medicare Card Holders)* Apply here for persons with disabilities."
        # — one price, its rider class, and then two footnotes run together.
        # Taking the whole run made the label a sentence, so the publication
        # contract refused it and the corpus lost a real senior pass price
        # that the page states plainly. Cutting at the marker recovers
        # "Senior (65+)".
        run = _FOOTNOTE_MARKER_RE.split(run, maxsplit=1)[0]
        label = _clean_label(run)[:120]
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

    A line naming several rider classes is segmented at the class keywords and
    each class takes the age bound stated inside its own segment. Taking the
    first keyword on the line and the first age bound on the line
    independently — which is what this did until 2026-09-07 — pairs them
    across the intervening classes: CCCTA's "Clipper START/Youth (6-18)/
    Senior (65+)/Disabled (RTC)" published *youth means 65+*, and VTA's
    "Seniors must be age 65 or older and Youth must be age 5-18" published
    *seniors means 5-18*. A class whose own segment states no age bound
    yields no row, which is the honest outcome: the line does not say.
    """
    facts: list[FareFact] = []
    for line in lines:
        if _PRICE_RE.search(line) or "|" in line:
            continue
        matches = list(_RIDER_CLASS_KEYWORDS.finditer(line))
        if not matches:
            age_min, age_max = _parse_age(line)
            if age_min is None and age_max is None:
                continue
            segments = [(_clean_label(line)[:60], (age_min, age_max))]
        else:
            segments = []
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                bounds = _parse_age(line[match.end() : end])
                if bounds == (None, None):
                    continue
                segments.append((match.group(0).lower(), bounds))
        for rider_class, (age_min, age_max) in segments:
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


def _normalize_axes(fact: FareFact) -> FareFact:
    """Put a rider-class value in the rider-class column.

    The prose fallback labels a price with whatever text follows it, and on a
    fare list that text is often the rider class rather than a program
    ("Youth (6-18)", "Regular one-way"). Filed as a program it reads as the
    name of a fare product that does not exist.

    When the rider-class column is empty the label moves into it. When that
    column already holds the normalised keyword the same label produced
    ("seniors" out of "Seniors (age 65+) Persons with Disabilities"), the
    keyword is kept — it is the comparable form — and only the duplicate in
    the program column is cleared. A row that genuinely names both a program
    and a rider class is left alone.
    """
    program = fact.program.strip()
    if not program or not _is_rider_class_label(program):
        return fact
    existing = fact.rider_class.strip()
    if existing:
        if existing.casefold() not in program.casefold():
            return fact
        return replace(fact, program="")
    age_min, age_max = fact.age_min, fact.age_max
    if age_min is None and age_max is None:
        age_min, age_max = _parse_age(program)
    return replace(fact, program="", rider_class=program, age_min=age_min, age_max=age_max)


def extract_chunk_candidates(agency: str, doc_id: str, chunk_id: str, text: str) -> list[FareFact]:
    """Every row the layout passes propose, before the publication contract.

    Callers that want the corpus as it is actually served want
    `extract_chunk_facts`; this exists so the refusal gate has something to
    refuse and so a test can assert what was proposed.
    """
    return _extract_chunk_candidates(agency, doc_id, chunk_id, text)[0]


def _extract_chunk_candidates(
    agency: str, doc_id: str, chunk_id: str, text: str
) -> tuple[list[FareFact], list[RefusedRow]]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    facts: list[FareFact] = []
    refused: list[RefusedRow] = []

    if any("|" in ln for ln in lines):
        piped, piped_refusals = _extract_pipe_table(agency, doc_id, chunk_id, lines)
        facts += piped
        refused += piped_refusals
    facts += _extract_label_price_blocks(agency, doc_id, chunk_id, lines)
    facts += _extract_age_only(agency, doc_id, chunk_id, lines)

    # A price a structured pass refused must not come back through the prose
    # fallback wearing a worse label. Excluding only the *captured* prices let
    # VineGo's refused fare matrix reappear one line later as five rows whose
    # program was a destination city — the refusal undone by the pass that
    # exists to cover layouts the structured passes do not model.
    already = {f.price for f in facts if f.price is not None}
    already |= {row.price for row in refused if row.price is not None}
    facts += _extract_inline_price_runs(agency, doc_id, chunk_id, text, already)
    return [_normalize_axes(f) for f in facts], refused


def extract_chunk_rows(
    agency: str, doc_id: str, chunk_id: str, text: str
) -> tuple[list[FareFact], list[RefusedRow]]:
    """The publishable rows of one chunk, and the ones refused with reasons."""
    candidates, structural = _extract_chunk_candidates(agency, doc_id, chunk_id, text)
    published, refused = partition_facts(candidates)
    return published, structural + refused


def extract_chunk_facts(agency: str, doc_id: str, chunk_id: str, text: str) -> list[FareFact]:
    """The publishable rows of one chunk."""
    return extract_chunk_rows(agency, doc_id, chunk_id, text)[0]


def build_facts(chunks) -> list[FareFact]:
    """Automated extraction pass over every chunk. `chunks` are
    `assistant.ingest.Chunk` (or anything with the same attributes)."""
    return build_facts_with_refusals(chunks)[0]


def build_facts_with_refusals(chunks) -> tuple[list[FareFact], list[RefusedRow]]:
    """`build_facts`, plus every candidate row the contract declined.

    The refusals are the point. A parser that quietly drops what it cannot
    read publishes a corpus that looks complete, which is the same lie as
    publishing the garbage — told the other way round.
    """
    facts: list[FareFact] = []
    refused: list[RefusedRow] = []
    for chunk in chunks:
        kept, declined = extract_chunk_rows(chunk.agency, chunk.doc_id, chunk.chunk_id, chunk.text)
        facts += kept
        refused += declined
    return facts, refused


def write_refusals(refusals: list[RefusedRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in refusals:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def load_refusals(path: Path) -> list[RefusedRow]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(RefusedRow(**json.loads(line)))
    return rows


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
    """Every fare-like `$amount` a rider-facing answer states, as floats.

    Large financial figures such as "$3.0 million in funding" are context,
    not rider prices, and must not be checked against the fare-fact table.
    """
    out = []
    for m in _PRICE_RE.finditer(answer_text):
        suffix = answer_text[m.end() : m.end() + 16]
        if re.match(r"(?:\.\d+)?\s*(million|billion)\b", suffix, re.I):
            continue
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
