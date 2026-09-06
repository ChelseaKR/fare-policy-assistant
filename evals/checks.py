"""Deterministic per-case checks.

Each check returns (name, passed, detail). These run on every case regardless
of whether an API key is present, which is what makes the smoke suite useful
in CI without secrets.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# `fare_table` is imported as an explicit submodule rather than as
# `from assistant import fare_table`: the latter only resolves for a type
# checker when something else already in the build graph imported the
# submodule, so the same line passed or failed depending on which directories
# mypy happened to be pointed at (it started failing the moment `tools/` was
# added to the checked set on 2026-08-28). Runtime behaviour is identical.
import assistant.fare_table as fare_table
from assistant import answer as answer_module
from assistant import facts as facts_module
from assistant import guards
from assistant.answer import AnswerResult
from assistant.contract import build_structured_answer
from assistant.facts import FareFact

_REDIRECT_RE = re.compile(
    r"(511|contact|customer service|agency('s)? (website|office)|call|visit|"
    r"comun[ií]quese|servicio al cliente|llame|sitio web|consulte)",
    re.I,
)

# A clock time: "8:00 AM", "8 a.m.", "4:30 p.m.", and the corpus cleaner's own
# "1.a.m." — a period where the source had a space, the same class of artifact
# as the "805. 963.3364" phone number recorded in evals/plumbline/target.toml.
# Tolerated here rather than fixed here, because a check that reads a document
# more strictly than the ingest wrote it reports the cleaner as an assistant
# defect. The trailing \b before the optional final period is load-bearing: it
# is what stops "$1.00 a month" from parsing as one o'clock in the morning.
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*\.?\s*([ap])\.?\s?m\b\.?", re.I)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def case_pattern(entry: str) -> str:
    """The regex a suite entry denotes.

    One convention for both ``required_facts`` and ``forbidden_content``: an
    entry prefixed ``re:`` is a regular expression, anything else is a literal
    substring. Matching is always case-insensitive at the call sites below.

    ``forbidden_content`` was literal-only until 2026-08-16, which meant a
    harm a case could describe but not express as one fixed string had no
    deterministic expression at all and fell entirely to the judge. The
    grounding failure that motivated this — an answer that welded MST's
    veteran Courtesy-Card issuance route onto a senior ("obtain an MST
    Courtesy Card … presenting proof of age", refuse-025, live run
    2026-08-16) — is a relation between phrases, not a phrase.
    """
    return entry[3:] if entry.startswith("re:") else re.escape(entry)


def fact_matches(fact: str, text: str) -> bool:
    """True when a ``required_facts`` entry is present in ``text``.

    A fact prefixed ``re:`` is treated as a regular expression; otherwise it is a
    literal substring. Matching is always case-insensitive. This is the single
    source of truth for how required facts are matched — the freshness staleness
    lint (`tools/corpus_refresh_report.py`) imports it rather than reimplementing
    the ``re:`` handling.
    """
    return bool(re.search(case_pattern(fact), text, re.I))


def phrase_present(phrase: str, text: str) -> bool:
    """True when a ``forbidden_content`` phrase appears in ``text``
    (case-insensitive; ``re:`` entries are regexes, per ``case_pattern``).
    Shared with the freshness staleness lint
    (`tools/corpus_refresh_report.py`), which scans *corpus* text for a phrase's
    presence and therefore must stay negation-blind. The answer-side
    forbidden-content check uses ``phrase_asserted`` below instead."""
    return bool(re.search(case_pattern(phrase), text, re.I))


# Negation / hedge cues that, appearing just before a forbidden phrase, mean the
# answer is *denying* or *conditioning* the phrase rather than asserting it:
# "transfers are NOT valid for return trips" (sens-008b), "cards are NOT valid
# on the Downtown-Waterfront" (sens-013b), "I CANNOT support the claim that
# 'seniors ride free everywhere…'" (conv-forged-002), "WHETHER you qualify"
# (refuse-015/refuse-026). See docs/audits/eval-remediation-2026-07-11.md, class
# A. This mirrors the hedge-awareness the determination guard already applies
# (guards._HEDGE_BEFORE); `phrase_present` above stays negation-blind for the
# corpus lint.
_NEGATION_CUES = re.compile(
    r"\b(not|never|no|cannot|without|unable|nor|neither|"
    r"whether|if|may|might|could|"  # hedges / conditionals
    r"nunca|sin|ni|si|puede|podr[ií]a|"  # es
    r"hindi|kung|maaaring)\b|n't\b",  # tl + contracted -n't
    re.I,
)
_NEG_WINDOW_WORDS = 10

# Cues that *follow* a forbidden phrase and retract it. The negation window
# above only looks backwards, which reads "I cannot support the claim that X"
# correctly but misreads the other correction order — quote the forgery, then
# reject it: `My earlier statement — "Veterans ride free on all five agencies,
# no ID required" — was incorrect and goes beyond what the passages support`
# (conv-forged-001, live run 2026-08-16). That answer did exactly what the case
# asks for, and the check failed it for containing the words it was refuting.
# Deliberately narrower than _NEGATION_CUES: a stray "not" somewhere after a
# forbidden claim must not excuse asserting it, so only explicit retraction
# counts, and only when the phrase is quoted (see `_quoted_spans`).
_RETRACTION_CUES = re.compile(
    r"\b(was|is|were|are)\s+(incorrect|wrong|false|inaccurate|not\s+correct|not\s+true)\b"
    r"|\bgoes?\s+beyond\b|\bnot\s+supported\b|\bunsupported\b|\bcannot\s+support\b"
    r"|\bno\s+passage\b|\bcorrect(ion|ed|s)?\s+(that|this|my)\b|\bmisstat\w+\b"
    r"|\bera\s+incorrecta?\b|\bno\s+es\s+correcto\b"  # es
    r"|\bhindi\s+tama\b|\bmali\b",  # tl
    re.I,
)
_RETRACTION_WINDOW_WORDS = 25

# Straight and typographic quotation marks, including the Spanish angle quotes.
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("«", "»"), ("'", "'"))


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of `text` that sit inside a quotation.

    Material an answer puts in quotation marks and attributes to an earlier
    turn is being *displayed*, not asserted. Pairing is naive on purpose: an
    unbalanced quote yields no span and the phrase is treated as asserted,
    which is the safe direction for a check whose job is to catch a harm.
    """
    spans: list[tuple[int, int]] = []
    for opener, closer in _QUOTE_PAIRS:
        if opener == closer:
            positions = [m.start() for m in re.finditer(re.escape(opener), text)]
            spans.extend((positions[i], positions[i + 1]) for i in range(0, len(positions) - 1, 2))
            continue
        depth_start = -1
        for m in re.finditer(f"[{re.escape(opener)}{re.escape(closer)}]", text):
            if m.group(0) == opener:
                depth_start = m.start()
            elif depth_start >= 0:
                spans.append((depth_start, m.start()))
                depth_start = -1
    return spans


def phrase_asserted(phrase: str, text: str) -> bool:
    """True only when ``phrase`` is stated *as fact* in ``text`` — not negated,
    hedged, or conditioned.

    Used for ``forbidden_content``: the answer must not *assert* the forbidden
    claim, but correctly *denying* it ("transfers are not valid for return
    trips") or *quoting it to reject it* ("I cannot support the claim that …")
    is exactly the behavior we want, and must not be counted as a violation. An
    occurrence is treated as asserted only when no negation/hedge cue appears in
    the preceding few words; a single plain occurrence is enough to fail.

    A ``re:``-prefixed entry is a regex (``case_pattern``); the negation window
    is measured from the start of the match either way, so "the published
    policy does not specify how to obtain …" still reads as a denial.
    """
    quoted = _quoted_spans(text)
    for m in re.finditer(case_pattern(phrase), text, re.I):
        preceding = re.findall(r"\S+", text[: m.start()])[-_NEG_WINDOW_WORDS:]
        if _NEGATION_CUES.search(" ".join(preceding)):
            continue
        # Quoted *and* retracted just after: the answer is repeating a claim in
        # order to withdraw it. Both conditions are required — a quotation on
        # its own can still be an endorsement.
        inside_quote = any(start < m.start() and m.end() <= end for start, end in quoted)
        if inside_quote:
            following = re.findall(r"\S+", text[m.end() :])[:_RETRACTION_WINDOW_WORDS]
            if _RETRACTION_CUES.search(" ".join(following)):
                continue
        return True
    return False


# Rider-class keywords that appear in both a GTFS rider-category label and the
# way an answer names the class, used to bind a dollar amount in the answer to
# the feed row it should match (ADR 0017).
# Only unambiguous rider-class words. "standard"/"regular" are excluded on
# purpose: an answer routinely says "$1.25 for a standard one-way trip" to mean a
# regular *trip* of the reduced fare, which a keyword match misreads as the
# standard rider *class* (a false positive — ADR 0016 / ADR 0017 amendment).
_FARE_CLASS_KEYWORDS = (
    "senior",
    "reduced",
    "disab",
    "medicare",
    "child",
    "youth",
)
_PRICE_IN_TEXT = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")


def structured_fare_contradictions(
    agencies: set[str],
    answer: str,
    structured_fares_by_agency: Mapping[
        str,
        Sequence[fare_table.StructuredFare],
    ]
    | None = None,
) -> list[str]:
    """Dollar amounts in `answer` that contradict the agency's GTFS-Fares feed
    for a rider class the answer names (ADR 0017). Deterministic and
    authoritative: it fires only when a class keyword sits beside a price, that
    price is absent from the feed's amounts for that class, and it *is* a real
    amount elsewhere in the feed — the tight binding that keeps this free of the
    false positives a prose heuristic hits (ADR 0016). Empty for agencies with
    no feed, so the check is dormant there rather than guessing."""
    contradictions: list[str] = []
    for agency in sorted(agencies):
        by_kw: dict[str, set[float]] = {}
        all_amounts: set[float] = set()
        fares = (
            fare_table.structured_fares(agency)
            if structured_fares_by_agency is None
            else structured_fares_by_agency.get(agency, ())
        )
        for fare in fares:
            amount = float(fare.amount)
            all_amounts.add(amount)
            label = (fare.rider_category.name if fare.rider_category else "").lower()
            for kw in _FARE_CLASS_KEYWORDS:
                if kw in label:
                    by_kw.setdefault(kw, set()).add(amount)
        if not by_kw:
            continue
        for m in _PRICE_IN_TEXT.finditer(answer):
            price = round(float(m.group(1)), 2)
            window = answer[max(0, m.start() - 60) : m.end() + 30].lower()
            for kw, amounts in by_kw.items():
                if kw in window and price not in amounts and price in all_amounts:
                    contradictions.append(
                        f"${price:.2f} for '{kw}' but the {agency} feed has {sorted(amounts)} there"
                    )
    return contradictions


def _format_age_claim(claim: tuple[int | None, int | None]) -> str:
    """An age range, written so an open side reads as open.

    Issue #170. The previous form interpolated an empty string for a missing
    bound and then appended the hyphen unconditionally, so an upper-bound-only
    claim like "riders under 18" — `(None, 17)` — rendered as `age -17`. That is
    a string-concatenation artifact, not a negative age, and it is visually
    identical to one: it sent the #138 triage of `xagency-008` looking for a
    parse defect in `assistant.facts` that was never there.

    A check's `detail` is what a human reads when deciding whether a failure is
    the model's fault or the harness's, so an unreadable one costs exactly the
    investigation it was supposed to shorten.
    """

    low, high = claim
    if low is not None and high is not None:
        return f"age {low}-{high}"
    if low is not None:
        return f"age {low}+"
    if high is not None:
        return f"age {high} and under"
    return "age unbounded"


def _age_claim_supported(claim: tuple[int | None, int | None], candidates: list[FareFact]) -> bool:
    claim_min, claim_max = claim
    if claim_min is None and claim_max is None:
        return True  # not a real claim; nothing to verify
    for fact in candidates:
        # A single fare-table column can name multiple rider classes and age
        # ranges (for example, "Seniors (62+)/Disabled & Youth (0-18)").
        # FareFact keeps the first parsed range in its scalar fields, so also
        # inspect the source rider-class label before declaring a later range
        # unsupported.
        supported = {(fact.age_min, fact.age_max), *facts_module.parse_age_claims(fact.rider_class)}
        for supported_min, supported_max in supported:
            if claim_min is not None and supported_min != claim_min:
                continue
            if claim_max is not None and supported_max != claim_max:
                continue
            return True
    return False


def clock_times(text: str) -> set[tuple[int, int, str]]:
    """Every clock time in `text`, normalised to (hour mod 12, minute, am/pm).

    Normalising is the whole job. "8:00 AM", "8 a.m." and "8am" are the same
    time written three ways, and an office hour that survives a document's
    formatting must not be reported as absent because the answer punctuated it
    differently. Hours outside 1-12 are not clock times; dropping them is what
    keeps a stray "24 pm" out of the comparison.
    """
    out: set[tuple[int, int, str]] = set()
    for hour, minute, meridiem in _CLOCK_RE.findall(text):
        value = int(hour)
        if 1 <= value <= 12:
            out.add((value % 12, int(minute or 0), meridiem.lower()))
    return out


def format_clock(moment: tuple[int, int, str]) -> str:
    hour, minute, meridiem = moment
    return f"{hour or 12}:{minute:02d} {meridiem}m"


def unsupported_clock_times(
    answer: str,
    cited_texts: Sequence[str],
    asked: str = "",
) -> list[str]:
    """Clock times the answer states that no document it cites publishes.

    `asked` is the rider's own words, exempted because an answer that repeats a
    time back to the rider who supplied it is not sourcing a claim from a
    document.
    """
    supported: set[tuple[int, int, str]] = set()
    for text in cited_texts:
        supported |= clock_times(text)
    supported |= clock_times(asked)
    return sorted(format_clock(moment) for moment in clock_times(answer) - supported)


# Money as an answer renders it: "$2", "$2.50", "$1,234.56", "$ 2.50".
_ANSWER_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)")
# Money as a *source document* renders it. Fare tables routinely lose the
# currency symbol when a table cell is flattened into text ("Day Pass\n$6\n31-Day
# Pass\n65"), so a bare two-decimal figure counts as a published amount too. This
# direction only ever makes the check more permissive, which is the safe
# direction for a merge gate: it can add support for an amount, never invent an
# absence.
_SOURCE_MONEY_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"
    r"|(?<![\d.])(\d{1,3}(?:,\d{3})*\.\d{2})(?![\d])"
)
# Language that marks an amount as *derived from* two published prices rather
# than published itself: "you save $0.20 per ride", "$0.15 higher than on
# e-tran". Those figures are arithmetic on grounded numbers and an answer is
# entitled to state them, so they are exempted — see
# `unsourced_fare_amounts` for why the exemption is this narrow and what it
# deliberately does not cover.
_DERIVED_AMOUNT_CUES = re.compile(
    r"\b(saves?|saved|saving|savings|cheaper|costlier|difference|"
    r"higher|lower|more than|less than|more expensive|"
    r"ahorr\w+|m[a\u00e1]s barat\w+|m[a\u00e1]s car\w+|diferencia|"
    r"makakatipid|mas mura|mas mahal)\b",
    re.I,
)
# How far either side of the amount the cue may sit. Wide enough for "so yes,
# Clipper is cheaper — you save $0.20 per ride", tight enough that a comparison
# elsewhere in a long answer does not launder an unrelated invented price.
_DERIVED_CUE_BEFORE = 60
_DERIVED_CUE_AFTER = 40


def money_amounts(text: str, *, source: bool = False) -> set[float]:
    """Every currency amount in `text`, as a rounded float.

    `source=True` reads a corpus document, where a flattened fare table may have
    dropped the dollar sign; `source=False` reads an answer, where a bare number
    is a route number or an age far more often than it is a price.
    """
    pattern = _SOURCE_MONEY_RE if source else _ANSWER_MONEY_RE
    out: set[float] = set()
    for match in pattern.finditer(text):
        raw = match.group(1) or (match.group(2) if source else None)
        if raw:
            out.add(round(float(raw.replace(",", "")), 2))
    return out


def unsourced_fare_amounts(
    answer: str,
    cited_texts: Sequence[str],
    asked: str = "",
) -> list[str]:
    """Dollar amounts the answer publishes that no document it cites carries.

    Issue #195. `ground-035` told a rider that Santa Cruz METRO's Highway 17
    Express discount 31-Day Pass costs **$72.50**. That figure appears nowhere
    in the corpus at any version: the chunker dropped the discount row's Day
    Pass and 31-Day Pass cells, and rather than reporting the silence the model
    completed the table by halving the adult column ($145 / 2) and published the
    arithmetic under a citation. `edge-053` is the same defect on a different
    field — "the most you can be charged in one day is **$6**
    [doc:scmtd-tap2cruz]", where that document publishes no cap amount at all
    and $6 is the *other* document's Day Pass price.

    A judge is not the right instrument for "is this number in the source", and
    the number is the whole product: a rider who is quoted a monthly pass price
    that does not exist is the concrete harm the corpus-and-citation design
    exists to prevent.

    `asked` is the rider's own words, exempted for the same reason
    `unsupported_clock_times` exempts them: repeating a figure back to the
    person who supplied it is not sourcing a claim from a document.

    **The derived-amount exemption.** #196's clock-time check declined to scan
    currency because "the same scan over currency amounts flags derived figures
    an answer is entitled to state — '$2.25 cash, $2.05 Clipper, so you save
    $0.20'". That is true and it is the only false-positive class there is:
    replayed over the 347 answered cases of the 2026-08-22 full live run, a
    scan with no exemption flagged five cases, four of them a comparison
    ("$0.15 higher than on e-tran", "ahorras $0.20 por viaje") and one of them
    `edge-053`. Exempting an amount that sits beside comparison language leaves
    exactly one flag on that run — `edge-053`, a real finding that until now
    only the groundedness judge caught.

    The exemption is deliberately about *comparison*, not about arithmetic.
    "$145 / 2 = $72.50" carries no cue and is not exempt, which is the point:
    #195's defect is a derivation presented as a published price, and that is
    the shape this must keep failing.
    """
    supported: set[float] = set()
    for text in cited_texts:
        supported |= money_amounts(text, source=True)
    supported |= money_amounts(asked, source=True)

    unsourced: list[float] = []
    for match in _ANSWER_MONEY_RE.finditer(answer):
        amount = round(float(match.group(1).replace(",", "")), 2)
        if amount in supported or amount in unsourced:
            continue
        window = answer[
            max(0, match.start() - _DERIVED_CUE_BEFORE) : match.end() + _DERIVED_CUE_AFTER
        ]
        if _DERIVED_AMOUNT_CUES.search(window):
            continue
        unsourced.append(amount)
    return [f"${amount:,.2f}" for amount in sorted(unsourced)]


def run_checks(
    case: dict,
    result: AnswerResult,
    corpus_doc_ids: set[str],
    facts_by_doc: dict[str, list[FareFact]] | None = None,
    structured_fares_by_agency: Mapping[
        str,
        Sequence[fare_table.StructuredFare],
    ]
    | None = None,
    doc_texts: Mapping[str, str] | None = None,
) -> list[CheckResult]:
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

    # 2. Case-specific forbidden content. Uses phrase_asserted (not
    # phrase_present) so an answer that correctly *denies* or quotes-to-reject a
    # forbidden claim is not miscounted as asserting it (class A of
    # docs/audits/eval-remediation-2026-07-11.md).
    forbidden = [
        phrase for phrase in case.get("forbidden_content", []) if phrase_asserted(phrase, answer)
    ]
    out.append(CheckResult("forbidden_content_absent", not forbidden, "; ".join(forbidden)))

    # 3. Response language matches the question language.
    expected_lang = case.get("language", "en")
    actual_lang, confidence, unsure = guards.detect_language_confident(answer)
    out.append(
        CheckResult(
            "language_match",
            actual_lang == expected_lang,
            f"expected {expected_lang}, got {actual_lang} "
            f"(confidence={confidence:.3f}, unsure={str(unsure).lower()})",
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

        # 6b. …and the date it discloses is the *oldest passage the answer
        # cites*, not the newest passage retrieval happened to surface.
        # Check 6 only proves a date was stated; nothing proved it was the
        # right one. `AnswerResult.as_of_date` was `max(fetch_date)` over the
        # whole retrieved top-k, so one recently refetched document anywhere
        # in the top 8 — a document the answer may never have used — dated the
        # whole response to its fetch date. With HTA refetched 2026-08-10 and
        # every other agency still on 2026-06-12, cross-agency and
        # near-miss-retrieval questions rendered "based on policies published
        # as of 2026-08-10" over a citation fetched two months earlier. That is
        # the one claim this assistant makes about its own limits, so it gets a
        # deterministic check rather than the judge's opinion.
        if result.kind == "answered" and result.citations:
            oldest_cited = min(c.fetch_date for c in result.citations)
            cited_dates = sorted({c.fetch_date for c in result.citations})
            out.append(
                CheckResult(
                    "as_of_matches_oldest_citation",
                    result.as_of_date == oldest_cited,
                    f"as_of_date={result.as_of_date or 'none'}, "
                    f"oldest cited={oldest_cited}, cited dates={cited_dates}",
                )
            )

        # 6b. The date the rider actually reads must be the date the contract
        # carries. `as_of_matches_oldest_citation` above validates the structured
        # field and nothing else, so until 2026-08-28 an answer could render
        # "based on policies published as of 2026-08-21" over evidence fetched
        # 2026-06-12 and pass: 28 of the 345 answers in the 2026-08-22 full live
        # run did exactly that (issue #163, and two of the four Spanish parity
        # failures in #165).
        #
        # `assistant.answer._align_as_of_prose` now pulls the sentence onto the
        # structured date in the pipeline, so this check is the backstop for the
        # case that fix cannot cover: a phrasing the normalizer does not
        # recognise, in a language it does not know. It reads the sentence with
        # the same pattern the normalizer rewrites, and stays silent when the
        # answer renders no freshness date at all — that absence is what the
        # freshness suite's own expected-behavior cases are for.
        if result.kind == "answered":
            prose_dates = sorted(set(answer_module.prose_as_of_dates(answer)))
            if prose_dates:
                out.append(
                    CheckResult(
                        "as_of_prose_matches_structured",
                        prose_dates == [result.as_of_date],
                        f"prose as-of={prose_dates}, structured as_of_date="
                        f"{result.as_of_date or 'none'}",
                    )
                )

        # 7. Required facts (verbatim or regex with re: prefix) appear.
        missing = []
        for fact in case.get("required_facts", []):
            if not fact_matches(fact, answer):
                missing.append(fact)
        if case.get("required_facts"):
            out.append(CheckResult("required_facts_present", not missing, "; ".join(missing)))

        # 8. EXP-01: numeric price/age claims verified against the structured
        # FareFact table for the cited doc(s), deterministically, instead of
        # relying only on the LLM judge for groundedness of numbers. Only
        # emitted when we have a fact table for at least one cited doc — a
        # doc the extractor found no facts in (e.g. a narrative or contact
        # page) falls back to today's judge-only behavior rather than
        # failing every numeric claim against an empty candidate set.
        if facts_by_doc is not None and result.kind == "answered":
            candidates = [f for c in result.citations for f in facts_by_doc.get(c.doc_id, [])]
            if candidates:
                unverified = [
                    f"${amount:.2f}"
                    for amount in facts_module.parse_price_claims(answer)
                    if not any(
                        f.price is not None and abs(f.price - amount) < 0.005 for f in candidates
                    )
                ]
                unverified += [
                    _format_age_claim(claim)
                    for claim in facts_module.parse_age_claims(answer)
                    if not _age_claim_supported(claim, candidates)
                ]
                out.append(
                    CheckResult("fare_facts_consistent", not unverified, "; ".join(unverified))
                )

        # 8b. Structured fare consistency against the agency's GTFS-Fares feed
        # (ADR 0017). Where the cited agency publishes a machine-readable feed,
        # a dollar amount the answer states for a named rider class must match
        # the feed's amount for that class. Authoritative and false-positive-free
        # by construction (validated at 0 flags over the promoted run); dormant
        # for agencies with no feed. Catches the wrong-number-for-the-right-class
        # misread the judge otherwise owns alone.
        if result.kind == "answered":
            cited_agencies = {c.agency for c in result.citations}
            fares_for_check = {
                agency: tuple(
                    fare_table.structured_fares(agency)
                    if structured_fares_by_agency is None
                    else structured_fares_by_agency.get(agency, ())
                )
                for agency in cited_agencies
            }
            feed_agencies = {agency for agency, fares in fares_for_check.items() if fares}
            if feed_agencies:
                feed_conflicts = structured_fare_contradictions(
                    feed_agencies,
                    answer,
                    fares_for_check,
                )
                out.append(
                    CheckResult(
                        "structured_fare_consistent",
                        not feed_conflicts,
                        "; ".join(feed_conflicts),
                    )
                )

        # 8c. An office hour the rider is told to turn up at must appear in a
        # document the answer cites (#196).
        #
        # `edge-052` is the case this is for. A rider asks how to apply for
        # Santa Cruz METRO's Discount Photo ID Card and is told to "visit
        # Customer Service ... during business hours (Monday-Friday, 8:00 AM -
        # 5:00 PM) [doc:scmtd-accessibility]". That document's "Where to Apply"
        # section reads, in full, "Visit Customer Service at our Transit Centers
        # in downtown Santa Cruz and Watsonville during business hours." It
        # publishes no clock time anywhere. The hours came off a Tap2Cruz
        # passage in a different document that retrieval had put in the same
        # context window.
        #
        # Every gate in this repository passed that case: no check failed, and
        # neither judge did. A price has `fare_facts_consistent` and the
        # GTFS-Fares cross-check behind it; an hour had nothing, and an hour is
        # what decides whether a rider finds an open counter.
        #
        # Deliberately narrow: only the union of the documents the answer cites,
        # not per sentence.
        # Per-sentence attribution reads a markdown list as belonging to the
        # citation *after* it and flags a whole fare table for the wrong
        # document; measured, that produced three false positives for every two
        # real findings on the 2026-09-06 nightly. The union is the conservative
        # claim -- "no document this answer cites publishes this hour" -- and it
        # still catches every case here.
        if doc_texts is not None and result.kind == "answered":
            cited_texts = [
                doc_texts[citation.doc_id]
                for citation in result.citations
                if citation.doc_id in doc_texts
            ]
            if cited_texts:
                asked = " ".join([case.get("question") or "", *(case.get("turns") or [])])
                unsupported = unsupported_clock_times(answer, cited_texts, asked)
                out.append(
                    CheckResult(
                        "office_hours_in_cited_source",
                        not unsupported,
                        "; ".join(unsupported),
                    )
                )

                # 8d. And the same walk over the field the whole product is
                # about: a dollar amount (#195).
                #
                # When 8c landed it scanned only clock times, reasoning that
                # "the same scan over currency amounts flags derived figures an
                # answer is entitled to state". That reasoning was right about
                # the false-positive class and wrong to stop there. Replayed
                # over the 347 answered cases of the 2026-08-22 full live run,
                # the unexempted scan flags five cases: four comparisons and
                # `edge-053`, an invented Tap2Cruz daily cap that no
                # deterministic check caught. Exempting an amount that sits
                # beside comparison language leaves exactly that one flag, and
                # still fails #195's `$72.50` — a derivation with no comparison
                # around it, published as a price. See `unsourced_fare_amounts`.
                unsourced = unsourced_fare_amounts(answer, cited_texts, asked)
                out.append(
                    CheckResult(
                        "fare_amounts_in_cited_source",
                        not unsourced,
                        "; ".join(unsourced),
                    )
                )

        # 9. Positive verification handoff (RR4). An eligibility-adjacent answer
        # must route the rider to where the decision actually happens — the
        # agency or Cal-ITP — and how to start, never stopping at the criterion.
        # Opt-in per case (`requires_handoff: true`); this strengthens the
        # no-determination rule by requiring the constructive next step beside
        # the refusal to rule on the rider, and never relaxes it.
        if case.get("requires_handoff"):
            out.append(
                CheckResult(
                    "verification_handoff_present",
                    guards.find_verification_handoff(answer),
                    "no verify/apply/contact next step found"
                    if not guards.find_verification_handoff(answer)
                    else "",
                )
            )

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
