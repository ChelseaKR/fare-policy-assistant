"""Hybrid retrieval over the processed corpus.

BM25 (rank_bm25) is the default and works offline. Dense retrieval via
sentence-transformers is optional (`pip install .[dense]`, FPA_DENSE=1) and is
mixed with BM25 by a fixed weight — see ADR 0001 for why this stays simple.
When a question names a known agency, retrieval is filtered to that agency.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from assistant import config, domain
from assistant.ingest import Chunk, load_chunks


# Aliases users actually type, mapped to scope keys, are sourced from the active
# domain profile (src/assistant/domain.py) at call time (see detect_agencies)
# rather than pinned at import — the active profile is chosen by FPA_DOMAIN,
# which may switch at runtime.
#
# Backward-compat: AGENCY_ALIASES resolves to the live profile's aliases on each
# access for callers/tests that import it as a constant.
def __getattr__(name: str) -> dict[str, str]:
    if name == "AGENCY_ALIASES":
        return domain.get_profile().aliases
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


@dataclass
class ConfidenceSignals:
    """Normalized, corpus-size-independent decline signals (FIX-07 / ADR
    0009), replacing the absolute BM25 constant `min_confidence` used to gate
    on. Absolute BM25 scores drift with the corpus (every new agency changes
    IDF for every existing chunk); these three do not, because each is a
    ratio or a distributional position rather than a raw score.

    - z_score: how far the top result's score sits above the score
      distribution of the *entire corpus* for this same query. As the corpus
      grows, both the top score and the background distribution move
      together, so the top result's position in that distribution stays
      stable even though the raw numbers do not.
    - margin: the normalized gap between the top and second-ranked
      candidates actually returned. A genuinely on-topic question usually has
      one clearly-best chunk; an off-topic one has several similarly weak
      matches with no real winner.
    - term_coverage: the fraction of the (lexicon-expanded) query terms that
      literally appear in the top chunk. Catches the case a bare score
      z-score can miss: a short, low-IDF query that happens to land on a
      chunk sharing almost none of its actual words.
    """

    z_score: float
    margin: float
    term_coverage: float


def _fold_accents(text: str) -> str:
    """`text` with combining accents stripped, length preserved.

    Every one of the 87 aliases in the shipped profile is written unaccented
    ("santa barbara"), because they were written from the agencies' English
    pages. A Spanish speaker writes the same agency accented — "Santa
    Bárbara", "Los Ángeles" — and the literal alias search below then finds
    nothing at all, so `search()` drops the agency filter entirely and answers
    an SBMTD question out of a global top_k spread across four agencies. That
    is what happens to eval cases ml-016 and ml-020 in the 2026-09-04 nightly:
    both detect no agency, and both fail `required_facts_present`.

    Folding is applied to the question and to the alias, so an accented alias
    would keep working too. NFC first, so that dropping the combining marks
    leaves one character where the composed form had one and match offsets
    stay comparable with the caller's original string.
    """
    composed = unicodedata.normalize("NFC", text)
    return "".join(
        ch for ch in unicodedata.normalize("NFD", composed) if unicodedata.category(ch) != "Mn"
    )


def detect_agencies(question: str, aliases: dict[str, str] | None = None) -> list[str]:
    """All known scopes named in the question, in order of first mention. The
    alias map defaults to the active profile's but can be injected (a different
    domain, or a test) without touching this logic. Matching is accent-blind on
    both sides — see `_fold_accents`."""
    q = _fold_accents(question.lower())
    selected_aliases = domain.get_profile().aliases if aliases is None else aliases
    matches: list[tuple[int, int, str]] = []
    for alias, agency in selected_aliases.items():
        match = re.search(rf"\b{re.escape(_fold_accents(alias.lower()))}\b", q)
        if match:
            # Text position defines behavior. Alias mapping insertion order is
            # deliberately irrelevant because canonical configuration identity
            # sorts object keys.
            matches.append((match.start(), -len(alias), agency))

    found: list[str] = []
    for _, _, agency in sorted(matches):
        if agency not in found:
            found.append(agency)
    return found


def detect_agency(question: str) -> str | None:
    agencies = detect_agencies(question)
    return agencies[0] if agencies else None


# Small Spanish→English lexicon for query expansion. Four of five agencies
# publish policy in English only, so Spanish questions need their key terms
# mirrored into English for BM25 to stand a chance (eval cases ml-009/010/011;
# see ADR 0001 — dense multilingual retrieval is the heavier alternative).
_ES_EN_LEXICON: dict[str, str] = {
    "pasaje": "fare",
    "tarifa": "fare",
    "tarifas": "fares",
    "boleto": "ticket",
    "mensual": "monthly",
    "semanal": "weekly",
    "diario": "daily",
    "descuento": "discount",
    "reducido": "reduced",
    "reducida": "reduced",
    "mayores": "seniors senior",
    "mayor": "senior",
    "niños": "children youth kids",
    "jóvenes": "youth",
    "gratis": "free",
    "edad": "age",
    "años": "years",
    "veterano": "veteran",
    "veteranos": "veterans",
    "discapacidad": "disability disabled",
    "estudiante": "student",
    "estudiantes": "students",
    "autobús": "bus",
    "viajan": "ride",
    "viajar": "ride",
    "cuesta": "cost costs",
    "costo": "cost",
    "precio": "price",
    "sencillo": "single",
    "tarjeta": "card",
    "prueba": "proof",
    "esposa": "spouse",
    "esposo": "spouse",
    "cónyuge": "spouse",
    "mes": "month",
    "identificación": "identification id",
    "pagan": "pay fare",
    "paga": "pay fare",
    "pagar": "pay fare",
    "viaje": "ride trip",
    "viajes": "rides trips",
    "personas": "persons",
    # GoPass is MST's product name for passes; the fare tables say "GoPass"
    # where a rider says "pase" (eval case ml-002).
    "pases": "passes gopass",
    "pase": "pass gopass",
}


def _tokenize(text: str) -> list[str]:
    """Word tokens, minus 1–2 letter noise ("a", "en", "of") that lets BM25
    score unrelated chunks on articles alone. Digits, "$", and "id" stay —
    ages, prices, and ID cards are exactly what riders ask about."""
    raw = re.findall(r"[a-záéíóúüñ0-9$]+", text.lower())
    return [t for t in raw if len(t) > 2 or t == "id" or any(ch.isdigit() for ch in t) or "$" in t]


# English variants BM25 can't bridge without stemming: riders say "disabled",
# the policies say "Persons with Disabilities" (eval cases refuse-002,
# edge-011).
_EN_SYNONYMS: dict[str, str] = {
    "cash": "single ride fare",
    "disabled": "disabilities disability",
    "disability": "disabled disabilities",
    "kid": "youth child",
    "kids": "youth children",
    "teen": "youth",
    "teenager": "youth",
}

# Stretch language: Tagalog, a high-demand California language that, unlike
# Chinese, is space-delimited Latin script, so the existing tokenizer handles it
# and only a fare-vocabulary lexicon is needed to bridge a Tagalog query to the
# English-only corpus. This is the retrieval half of stretch-language support
# (R2-3); answering *in* Tagalog and a parity suite need a live model and a
# detect_language extension, and are tracked as live-gated work.
_TL_EN_LEXICON: dict[str, str] = {
    "pamasahe": "fare",
    "magkano": "how much cost price",
    "diskwento": "discount reduced",
    "nakatatanda": "senior seniors",
    "matatanda": "seniors senior",
    "libre": "free",
    "bata": "youth child children",
    "estudyante": "student students",
    "beterano": "veteran veterans",
    "kapansanan": "disability disabled",
    "buwanang": "monthly",
    "tiket": "ticket",
    "pasahero": "rider passenger",
}


# "Close the loop" retrieval (persona research R1-2). A rider who asks about a
# reduced fare almost always needs the *next step* — where to apply for the
# discount ID and what it costs — even when they don't spell that out. These two
# patterns let search() append the agency's application/ID-card passage when the
# top-ranked passages are the fare/eligibility tables and the where-to-apply
# passage fell out of top_k.
#
# The query trigger is the reduced-fare vocabulary riders use; the companion
# signal is keyed on the real section titles and phrasings in the corpus
# (grep of corpus/processed/chunks.jsonl): MST "Courtesy Cards", SBMTD
# "Mobility Pass: Reduced Fare and Medicare ID Cards", Yolobus reduced-fare-id
# ("obtain a reduced fare photo ID by visiting …").
_REDUCED_FARE_QUERY = re.compile(
    r"\b(senior|seniors|disabled|disabilit\w*|medicare|discount|reduced|reduced[- ]fare"
    r"|youth|id card|photo id|courtesy card|mobility pass|apply|application|obtain)\b",
    re.I,
)
_APPLICATION_PASSAGE = re.compile(
    r"obtain (an?|the)? ?(application|reduced[- ]fare|photo id|mtd photo id|courtesy card)"
    r"|download an application"
    r"|application completed"
    r"|reduced[- ]fare (mtd )?photo id card"
    r"|reduced[- ]fare photo id"
    r"|courtesy card"
    r"|mobility pass"
    r"|obtenga (una? )?solicitud"
    r"|tarjetas? de cortes[ií]a"
    r"|solicitud en persona",
    re.I,
)


def _is_reduced_fare_query(question: str) -> bool:
    return bool(_REDUCED_FARE_QUERY.search(question))


# Child/youth free-fare "close the loop" (eval sens-010a). A rider asking
# whether a young child rides free almost never uses the corpus's vocabulary for
# the provision — SBMTD publishes "FREE Children under 45 inches tall", Yolobus
# "Youth ages 0-18 ride free!" — so a bare age query ("does my 3-year-old ride
# free?") scores the fare-schedule chunk far below the fare-change narrative and
# it falls out of top_k. Same remedy as _close_the_loop: when the question is a
# child-fare query and the provision passage is missing, append it so the answer
# can state the actual rule (a height/age threshold) instead of declining.
#
# The rider's word for a child, in the three languages the assistant answers
# in. It was English-only until 2026-09-04, which meant the whole helper was
# unreachable from a Spanish or Tagalog question even though the provision it
# looks for is published in English and the retriever already bridges the
# query with _ES_EN_LEXICON / _TL_EN_LEXICON: eval ml-016 ("¿Los niños
# pequeños viajan gratis…?") asks exactly the question this helper exists for
# and never triggered it. Accents are folded before matching (see
# `_is_child_fare_query`), so "niño" and "nino" both match.
_CHILD_FARE_QUERY = re.compile(
    r"\b(child|children|kid|kids|toddler|toddlers|infant|infants|baby|babies|"
    r"son|daughter|\d+[- ]?year[- ]?olds?|years? old"
    # es
    r"|nino|nina|ninos|ninas|hijo|hija|hijos|hijas|bebe|bebes|menores"
    r"|\d+\s*anos?"
    # tl
    r"|bata|bata-bata|anak|sanggol)\b",
    re.I,
)
_CHILD_FARE_PROVISION = re.compile(
    r"child(ren)?\s+under\s+\d+\s*inch"  # SBMTD: "Children under 45 inches tall"
    r"|(child(ren)?|youth)[^.]{0,40}\brides?\s+free"  # "youth … ride free"
    r"|youth\s+ages?\s+0",  # Yolobus: "Youth ages 0-18 ride free!"
    re.I,
)


def _is_child_fare_query(question: str) -> bool:
    return bool(_CHILD_FARE_QUERY.search(_fold_accents(question)))


def _is_child_fare_provision(chunk: Chunk) -> bool:
    return bool(_CHILD_FARE_PROVISION.search(f"{chunk.section} {chunk.text}"))


def _is_application_passage(chunk: Chunk) -> bool:
    """A passage that tells the rider how to apply for / obtain a reduced-fare
    program or ID card (where, cost, hours) — the 'close the loop' next step."""
    return bool(_APPLICATION_PASSAGE.search(f"{chunk.section} {chunk.text}"))


# Eligibility-criterion "close the loop" (issue #150/#138): the chunk that
# states an agency's actual age/eligibility cutoff is often short and
# term-sparse next to the same agency's "how to pay" / "ways to pay" / pass
# chunks, which repeat fare, discount, and payment vocabulary densely and
# routinely outrank it on BM25 — sometimes out of the *entire* per-agency
# top_k, not just a multi-agency quota. Reproduced offline: for "I'm 70 years
# old. What senior discount do I get on AC Transit, and how do I pay for it?"
# scoped to AC Transit alone (top_k=8, no quota split), actransit-discounts#1
# ("Riders aged 65 and older ... eligible for Senior/Disabled fares") ranks
# 10th; six lower-signal chunks about passes, Clipper START, and payment
# methods rank above it. On a two-agency comparison the same chunk additionally
# has to survive the per-agency quota (xagency-actransit-001), compounding the
# problem. Patterns below are grounded in the corpus's own phrasings (grep of
# corpus/processed/*.md): "aged 65 and older", "age 65 or older", "62+",
# "age 80 and above are eligible", "years of age or older".
_ELIGIBILITY_CRITERION = re.compile(
    # "+" endings never take a trailing \b: "+" is itself non-word, so when
    # it is followed by whitespace or a line break (the common case, e.g.
    # "65+\nValid Medicare Card..."), both sides of that position are
    # non-word and \b never asserts there. Verified against a live corpus
    # miss ("65+\n" in soltrans-fare-table#2) before landing.
    r"\bage[ds]?\s*\d{2}\s*\+"
    r"|\bage[ds]?\s*\d{2}\s*(and\s+(older|above|up)|or\s+(older|above))\b"
    r"|\b\d{2}\s*\+"
    r"|\byears?\s*(of\s+age\s*)?(and|or)\s*(older|up|above)\b",
    re.I,
)


def _is_eligibility_criterion_passage(chunk: Chunk) -> bool:
    return bool(_ELIGIBILITY_CRITERION.search(f"{chunk.section} {chunk.text}"))


# Priced-fare-table "close the loop" (issue #138). A rider who asks what
# something costs needs the agency's own priced fare table, and BM25
# systematically ranks that table BELOW the prose surrounding it. A table
# states each term once per row — "Local Single Ride | $2.75 | $3.00" — while
# the narrative pages beside it ("Multiple Ride Passes", "East Bay Day Pass",
# "Group Discount Program", "Students (TK-12)") repeat "fare", "pass",
# "Clipper" and "day" across whole sentences, and term frequency is what BM25
# scores. The gap does not close as the corpus grows, it widens: every agency
# added brings more prose competing for the same top_k, while each agency
# still has only the one table.
#
# `config.RetrievalConfig.top_k` already carries a comment that names this
# ("8 rather than 6: fare-table chunks are number-heavy and rank low on BM25
# even when they hold the answer"). Raising k is the wrong lever, because the
# table's *rank* is what moves with corpus size, not its distance from a
# constant. Measured offline against the 2026-09-04 nightly's own questions,
# with the agency filter applied and no multi-agency quota in play:
#
#   mst-fares#1               "Single Ride ... $2.00"        rank 8, 14 of 28 MST chunks
#   cccta-fare-types-prices#0 "Single Ride | $2.00"          rank 10, 10, 16 of 25
#   actransit-fares#1         "Local Day Pass ... $6.00"     rank 9 of 26
#   actransit-fares-es#1      "Pase Local Diario ... $6.00"  rank 8 of 26
#   sbmtd-fares-passes#1      "FREE Children under 45 in"    rank 8 of 23
#
# — every one of them just past a top_k of 8, and every one of them the only
# place its agency publishes the number the case asks for.
#
# The consequence is worse than a missing number. On xagency-016 SacRT's adult
# fare row (`sacrt-fares#1`, "Age 19-61 - Basic | Single Ride Ticket | $2.50")
# never arrived while the TK-12 student table (`sacrt-fares#2`, "$1.25") did,
# and the model reported $1.25 as the basic single ride. Issue #138 files that
# as a cross-agency attribution error; it is a retrieval miss wearing an
# attribution error's clothes, and no prompt rule can fix it, because the
# correct row was never in the context window.
#
# WHICH priced passage is guaranteed matters as much as the guarantee. An
# agency publishes several priced pages — MST prices its Group Discount
# Program, CCCTA its East Bay Day Pass, SacRT its TK-12 student table — and
# those are what BM25 already returns. Guaranteeing merely "some passage with
# prices in it" is therefore satisfied by the very passages that crowded the
# schedule out, which is measurably useless: it recovered 2 of the 11
# never-retrieved facts in the 2026-09-04 nightly.
#
# The rider asking what a ride costs needs the agency's *fare schedule* — the
# one table that prices its products together — and that table is
# identifiable without naming any chunk: it is the agency's densest, by a
# wide margin. MST's schedule prices 16 products where its next densest page
# prices 6; SacRT's 16 against 5; CCCTA's 9 against 4. Checked against all 18
# agencies in the corpus, the densest chunk is the agency's fare schedule for
# 14 of them ("Current Fares", "Fares & Passes", "Local Fixed Route Fares",
# "CASH FARES", "Fare Table", …); for the other four it is an adjacent
# schedule — SLORTA's fare-capping table, SBMTD's passes table — which is the
# same kind of evidence and still the right passage to hold.
#
# Ties are common and load-bearing: an agency that publishes a Spanish mirror
# has two equally dense schedules (actransit-fares#1 and actransit-fares-es#1
# both price 21 products). Both count as the schedule, and the tie is broken
# by this query's own ranking, so a Spanish question gets the Spanish table
# through the existing language boost rather than through a second rule.
_FARE_TABLE_PRICE = re.compile(r"\$\s?\d")

# An agency whose densest page prices fewer than four products has no schedule
# worth guaranteeing; injecting its best-priced page would be noise. No agency
# in today's corpus is below this (the lowest, VTA, prices six), so the floor
# is a guard against a future thin agency rather than an active filter.
_FARE_TABLE_MIN_PRICES = 4

# How relevant the schedule must be, relative to the agency's best retrieved
# passage, to be worth appending when that best passage is ALREADY a priced
# table. See `Retriever._dominated_by_a_better_table` for the measurement and
# for ground-024, the live regression that made this necessary.
_FARE_SCHEDULE_RELEVANCE_FLOOR = 0.25

# A question asking for an AMOUNT, in the three languages the assistant
# answers in. Deliberately the rider's vocabulary rather than the agencies':
# "what's the most I'll be charged?" (edge-actransit-001) and "magkano ang
# pamasahe" (tl-001) ask for a number off the fare table just as plainly as
# "how much does it cost".
#
# The word "fare" is deliberately NOT a trigger on its own, and this repo's
# own containment test is why. Half the corpus's questions mention a fare
# without asking its price — "What proof do I need for the veteran fare on
# MST?" wants a document list — and with a bare `\bfares?\b` alternative the
# helper appended MST's schedule to that question at score 0.0, a passage with
# no term overlap with the query at all. The interrogative form below asks for
# the fare's value ("what is the ... fare") and leaves the proof question
# alone.
_FARE_PRICE_QUERY = re.compile(
    r"\bhow much\b|\bcosts?\b|\bprices?\b|\bcharges?\b|\bcharged\b"
    r"|\bfare max\w*|\bcap(ped|s)?\b|\bmaximum\b|\bmost i\b"
    r"|\bwhat(?:'s| is| are)\s+(?:the\s+)?[\w -]{0,24}\b(?:fares?|pass(?:es)?|ticket)\b"
    # es
    r"|\bcuant[oa]s?\b|\bcuesta\b|\bcuestan\b|\bcosto\b|\bprecios?\b|\bmaximo\b"
    # tl
    r"|\bmagkano\b",
    re.I,
)


def _is_fare_price_query(question: str) -> bool:
    return bool(_FARE_PRICE_QUERY.search(_fold_accents(question)))


# Effective-date "close the loop" (issue #138, freshness suite). "How long are
# the current Yolobus fares in effect?" and "Did SLO RTA change its fares
# recently?" both need one specific passage: the one carrying the schedule's
# effective-date stamp. Neither is retrieved today, and for the same reason
# the fare schedule is not — the stamp is one short sentence, or a section
# heading with no body text at all, next to pages that discuss fares at
# length. `yolobus-fares#0` is 140 characters ("All below fares are effective
# July 1, 2026 – June 30, 2027") and ranks 9th of 22 Yolobus chunks; SLORTA
# publishes its date ONLY in a heading, "New cash fares as of April 6, 2026",
# which appears nowhere in any chunk body in the corpus.
#
# That heading is why this matches against section and text together, as
# `_priced_products` does: `answer._format_passages` renders the section in
# each passage block, so a heading-only fact does reach the model — but only
# if the chunk is retrieved at all.
#
# Narrow by construction: 13 of 301 chunks qualify, across 7 of the 18
# agencies. The other eleven publish no effective date, and for them this
# helper correctly does nothing rather than inventing a stand-in.
_MONTH = (
    r"(january|february|march|april|may|june|july|august|september|october"
    r"|november|december|enero|febrero|marzo|abril|mayo|junio|julio|agosto"
    r"|septiembre|octubre|noviembre|diciembre)"
)
_EFFECTIVE_DATE_PASSAGE = re.compile(
    rf"\b(fares?|tarifas?|prices?)\b[^.\n]{{0,60}}"
    rf"\b(effective|as of|in effect|vigent\w*|a partir de)\b"
    rf"|\b(effective|as of|vigente desde)\b[^.\n]{{0,20}}{_MONTH}\s+\d",
    re.I,
)
_EFFECTIVE_DATE_QUERY = re.compile(
    r"\b(in effect|effective|expires?|expiring|expiration|how long|until when)\b"
    r"|\b(chang\w+|increas\w+|rais\w+|new|current|recent\w*)\b[^?.\n]{0,40}\b(fares?|prices?)\b"
    r"|\b(fares?|prices?)\b[^?.\n]{0,40}\b(chang\w+|increas\w+|went up|go up|recent\w*)\b"
    # es
    r"|\b(vigent\w*|vencen?|caducan?|hasta cuando)\b"
    r"|\b(cambi\w+|subi\w+|nuev[ao]s?)\b[^?.\n]{0,40}\btarifas?\b"
    r"|\btarifas?\b[^?.\n]{0,40}\b(cambi\w+|subi\w+)\b",
    re.I,
)


def _is_effective_date_query(question: str) -> bool:
    return bool(_EFFECTIVE_DATE_QUERY.search(_fold_accents(question)))


def _is_effective_date_passage(chunk: Chunk) -> bool:
    return bool(_EFFECTIVE_DATE_PASSAGE.search(f"{chunk.section} {chunk.text}"))


def _priced_products(chunk: Chunk) -> int:
    """How many fare products a passage prices. The section heading counts:
    several agencies carry the price or the effective date in the heading and
    nowhere in the body, and `answer._format_passages` shows the model the
    heading too."""
    return len(_FARE_TABLE_PRICE.findall(f"{chunk.section} {chunk.text}"))


# Corpus-wide enumeration questions (issue #150, xagency-010): "Which agencies
# in your corpus take Clipper?" names no agency, so `detect_agencies` returns
# nothing and `search()` falls back to the plain global top_k — 8 chunks out of
# a 301-chunk, 18-agency corpus, which structurally cannot support an answer
# that enumerates across agencies. The strongest-matching agency's documents
# take every slot (for Clipper: SolTrans and Marin, which mention it densest)
# and the model is forced to either under-enumerate or reach past its evidence.
#
# The remedy is a different selection shape, not a bigger k: on an
# enumeration-form question with no agency named, take the single best-ranked
# positive-scoring chunk PER AGENCY, in global score order. Every agency whose
# text has any term overlap with the query contributes exactly one passage, so
# the answer model sees a corpus-wide cross-section (bounded by the agency
# count, ≤18 today) and can attribute per agency under system-prompt rule 7 —
# including negative statements ("Clipper Cards are not honored on METRO
# buses"), which for that agency IS the best-matching chunk. Depth is traded
# for breadth deliberately: an enumeration answer needs one passage per
# agency, not eight passages about one agency.
_ENUMERATION_QUERY = re.compile(
    r"\b(which|what|list( of| all)?|how many|name( the| all)?)\b[^?.\n]{0,60}"
    r"\b(agencies|agency|operators?|systems|networks|transit providers?)\b"
    r"|\b(qué|cuáles|cuántas)\b[^?.\n]{0,60}\b(agencias?|operador\w*|sistemas?)\b",
    re.I,
)


def _is_enumeration_query(question: str) -> bool:
    return bool(_ENUMERATION_QUERY.search(question))


# The enumeration scaffolding itself ("which agencies", "in your corpus")
# must not decide which chunk represents an agency: those tokens match fare
# tables and boilerplate ("participating agencies", "transit systems") more
# densely than the actual topic does. Measured on the real corpus with the
# scaffolding left in, CCCTA's representative chunk for "Which agencies in
# your corpus take Clipper?" was its RTC-discount page rather than its
# dedicated Clipper page, and SCMTD's was an accessibility page rather than
# the chunk stating "Clipper Cards are not honored on METRO buses". The
# per-agency pick therefore ranks against the question minus these tokens
# ("Clipper"), while the reported scores stay on the original-question
# scale so `confidence_signals` keeps comparing like with like.
#
# Generic question verbs (take/accept/offer/have/…) are scaffolding too, and
# measurably worse than the noun scaffolding: BM25's IDF makes a rare generic
# verb decisive. Measured before adding them: "take" appears in so few chunks
# that WestCAT's representative chunk for the Clipper question became a
# pass-purchasing page scoring 7.1 on "take" alone, ahead of every dedicated
# WestCAT Clipper chunk (max 4.9), and SCMTD's became a troubleshooting page.
# The verb is redundant with its object for retrieval — "Clipper" alone finds
# acceptance passages; "free youth fares" alone finds free-fare provisions.
_ENUMERATION_SCAFFOLD_TOKENS = frozenset(
    {
        # English scaffolding — question form
        "which",
        "what",
        "list",
        "how",
        "many",
        "name",
        "all",
        "agencies",
        "agency",
        "operator",
        "operators",
        "system",
        "systems",
        "network",
        "networks",
        "provider",
        "providers",
        "transit",
        "corpus",
        "your",
        "the",
        "does",
        "are",
        # English scaffolding — generic question verbs
        "take",
        "takes",
        "taking",
        "accept",
        "accepts",
        "accepting",
        "offer",
        "offers",
        "offering",
        "have",
        "has",
        "honor",
        "honors",
        "honour",
        "honours",
        "use",
        "uses",
        "using",
        "provide",
        "provides",
        "sell",
        "sells",
        "support",
        "supports",
        # Spanish scaffolding
        "qué",
        "cuales",
        "cuáles",
        "cuantas",
        "cuántas",
        "agencia",
        "agencias",
        "sistema",
        "sistemas",
        "aceptan",
        "acepta",
        "ofrecen",
        "ofrece",
        "tienen",
        "tiene",
        "usan",
        "usa",
        "venden",
        "vende",
    }
)


def _enumeration_topic(question: str) -> str:
    """The question with the enumeration scaffolding removed — what the rider
    is actually enumerating over ("take Clipper", "free youth fares")."""
    kept = [tok for tok in _tokenize(question) if tok not in _ENUMERATION_SCAFFOLD_TOKENS]
    return " ".join(kept)


_RIDER_CLASS_PATTERNS = {
    "veteran": re.compile(r"\b(veteran\w*|veteran[oa]s?)\b", re.I),
    "senior": re.compile(
        r"\b(senior\w*|adulto mayor|personas mayores|nakatatanda|6[25]\s*(años|years)?)\b",
        re.I,
    ),
    "disabled": re.compile(r"\b(disab\w*|discapac\w*|wheelchair|kapansanan)\b", re.I),
}


def _rider_classes(text: str) -> set[str]:
    return {name for name, pattern in _RIDER_CLASS_PATTERNS.items() if pattern.search(text)}


def _application_matches_question(question: str, chunk: Chunk) -> bool:
    """Do not attach one rider class's application process to another.

    Several agency pages reuse labels such as "Courtesy Card" while publishing
    locations or proof rules only for disabled riders. Appending that chunk to a
    senior or veteran query encouraged the answer model to extend those rules
    across classes. A generic application passage still matches every query;
    only an explicitly class-bound, disjoint passage is excluded.
    """
    query_classes = _rider_classes(question)
    if not query_classes:
        return True
    passage_classes = _rider_classes(f"{chunk.section} {chunk.text}")
    return not passage_classes or bool(query_classes & passage_classes)


def _expand_query(tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    for tok in tokens:
        expanded.extend(_ES_EN_LEXICON.get(tok, "").split())
        expanded.extend(_TL_EN_LEXICON.get(tok, "").split())
        expanded.extend(_EN_SYNONYMS.get(tok, "").split())
        # Poor man's plural folding, query side only.
        if tok.isalpha():
            if tok.endswith("s") and len(tok) > 3:
                expanded.append(tok[:-1])
            else:
                expanded.append(tok + "s")
    return expanded


class Retriever:
    def __init__(
        self, chunks: list[Chunk] | None = None, cfg: config.RetrievalConfig | None = None
    ):
        self.cfg = cfg or config.RetrievalConfig()
        self.chunks = chunks if chunks is not None else load_chunks()
        self._bm25 = BM25Okapi([_tokenize(f"{c.section} {c.text}") for c in self.chunks])
        self._dense = self._load_dense() if self.cfg.use_dense else None
        self._fare_schedules: dict[str, frozenset[str]] | None = None

    def _load_dense(self):
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(self.cfg.dense_model)
        embeddings = model.encode(
            [f"{c.section}. {c.text}" for c in self.chunks], normalize_embeddings=True
        )
        return model, embeddings

    def _rank_all(self, question: str) -> list[ScoredChunk]:
        """Every chunk in the corpus, scored against `question` and sorted
        best-first — unfiltered by agency. Shared by `search()` (which then
        filters/truncates it) and `confidence_signals()` (which needs the
        full-corpus score distribution as background, not just the top-k)."""
        from assistant.guards import detect_language

        q_lang = detect_language(question)
        scores = self._bm25.get_scores(_expand_query(_tokenize(question)))
        if self._dense is not None:
            model, embeddings = self._dense
            q_emb = model.encode([question], normalize_embeddings=True)[0]
            dense_scores = embeddings @ q_emb
            # Scale dense cosine ([-1,1]) into BM25's rough range before mixing.
            bm25_max = max(float(scores.max()), 1.0)
            w = self.cfg.dense_weight
            scores = (1 - w) * scores + w * dense_scores * bm25_max

        # Mild boost for chunks in the question's language, so translated
        # documents don't crowd out the originals (and vice versa) when an
        # agency publishes in both (eval cases ground-001, ml-002).
        boosted = (
            float(s) * (self.cfg.language_boost if c.language == q_lang else 1.0)
            for c, s in zip(self.chunks, scores, strict=True)
        )
        return sorted(
            (ScoredChunk(chunk=c, score=s) for c, s in zip(self.chunks, boosted, strict=True)),
            key=lambda sc: sc.score,
            reverse=True,
        )

    def search(self, question: str, agency: str | None = None) -> list[ScoredChunk]:
        agencies = [agency] if agency else detect_agencies(question)
        ranked = self._rank_all(question)
        if len(agencies) == 1:
            scoped = [sc for sc in ranked if sc.chunk.agency == agencies[0]]
            results = scoped[: self.cfg.top_k]
        elif agencies:
            # A question comparing agencies needs passages from each; a plain
            # union lets one agency's stronger lexical matches take every slot
            # (eval cases edge-004, edge-011). Give each agency an equal quota.
            quota = max(2, self.cfg.top_k // len(agencies))
            picked: list[ScoredChunk] = []
            for ag in agencies:
                picked.extend([sc for sc in ranked if sc.chunk.agency == ag][:quota])
            results = sorted(picked, key=lambda sc: sc.score, reverse=True)
        elif _is_enumeration_query(question):
            # Corpus-wide enumeration (issue #150, xagency-010): one chunk per
            # agency, so the answer can enumerate across the whole corpus
            # instead of seeing eight chunks from whichever agency matched
            # densest. WHICH chunk represents each agency is decided by the
            # topic ranking (scaffolding stripped — see _enumeration_topic);
            # the chunk's REPORTED score is its original-question score, so
            # downstream confidence math stays on one scale. See
            # _ENUMERATION_QUERY above for the full rationale.
            topic = _enumeration_topic(question)
            topical = self._rank_all(topic) if topic.strip() else ranked
            original_score = {sc.chunk.chunk_id: sc.score for sc in ranked}
            seen_agencies: set[str] = set()
            picked_enum: list[ScoredChunk] = []
            for sc in topical:
                if sc.score <= 0:
                    break  # topical is sorted; nothing below has term overlap
                if sc.chunk.agency in seen_agencies:
                    continue
                picked_enum.append(
                    ScoredChunk(chunk=sc.chunk, score=original_score[sc.chunk.chunk_id])
                )
                seen_agencies.add(sc.chunk.agency)
            results = sorted(picked_enum, key=lambda sc: sc.score, reverse=True)
        else:
            results = ranked[: self.cfg.top_k]
        # Remove application instructions explicitly scoped to a different
        # rider class before the model sees them; ordinary policy passages stay.
        results = [
            sc
            for sc in results
            if not _is_application_passage(sc.chunk)
            or _application_matches_question(question, sc.chunk)
        ]
        results = self._close_the_loop(question, agencies, results, ranked)
        results = self._ensure_eligibility_passage(question, agencies, results, ranked)
        results = self._ensure_child_fare_passage(question, agencies, results, ranked)
        results = self._ensure_fare_table_passage(question, agencies, results, ranked)
        return self._ensure_effective_date_passage(question, agencies, results, ranked)

    def _augmentation_targets(
        self, question: str, agencies: list[str], results: list[ScoredChunk]
    ) -> list[str]:
        """Which agencies the three append-one-passage helpers below act on.

        The named agencies, when the question names any. Otherwise the single
        best-scored agency stands in, which is reasonable for an ordinary
        unscoped question because a plain top_k is normally dominated by one
        agency anyway.

        An enumeration question is the exception, and it is why this is a shared
        helper rather than three copies of the same fallback (issue #169).
        `search()` has just built one passage per agency across the whole corpus
        (ADR 0027), so `results[0].chunk.agency` is not standing in for "the
        agency this question is about" — there is no such agency. It is whichever
        of eighteen happened to score highest. Giving that one a second,
        better-supported passage while the other seventeen keep only their bare
        enumeration pick is arbitrary: the model then has retrieved evidence to
        write a fuller, better-cited sentence about one agency for no reason a
        rider could name, and it breaks the one-passage-per-agency guarantee the
        enumeration branch exists to provide.

        Targeting every enumerated agency keeps the asymmetry principled: an
        agency gets a second passage when its own enumeration pick lacks the
        criterion (or application, or child-fare) passage the question is about
        and its corpus has one, which is a fact about the corpus rather than
        about BM25 tie-breaking. The helpers themselves are unchanged: each still
        appends at most one passage per agency and never removes anything.
        """

        if agencies:
            return list(agencies)
        if not results:
            return []
        if _is_enumeration_query(question):
            return sorted({sc.chunk.agency for sc in results})
        return [results[0].chunk.agency]

    def _ensure_eligibility_passage(
        self,
        question: str,
        agencies: list[str],
        results: list[ScoredChunk],
        ranked: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """Issue #150/#138: on a reduced-fare/eligibility query, make sure the
        agency's own age/eligibility-criterion passage survives — not just its
        application/where-to-apply passage (that's `_close_the_loop`'s job).
        The criterion chunk is often short and term-sparse next to the same
        agency's payment-method chunks ("Ways to Pay", "Token Transit"), which
        repeat fare/discount/payment vocabulary densely and routinely outrank
        it on BM25, sometimes out of the per-agency top_k entirely — and on a
        multi-agency question, the per-agency quota (`search()` above)
        compounds it further. Mirrors `_close_the_loop` exactly: append at
        most one best-ranked criterion passage per relevant agency, never
        remove anything."""
        if not _is_reduced_fare_query(question):
            return results
        targets = self._augmentation_targets(question, agencies, results)
        present = {sc.chunk.chunk_id for sc in results}
        additions: list[ScoredChunk] = []
        for ag in targets:
            has_criterion = any(
                _is_eligibility_criterion_passage(sc.chunk)
                for sc in results
                if sc.chunk.agency == ag
            )
            if has_criterion:
                continue  # the agency's own criterion passage is already in hand
            for sc in ranked:
                if (
                    sc.chunk.agency == ag
                    and sc.chunk.chunk_id not in present
                    and _is_eligibility_criterion_passage(sc.chunk)
                ):
                    additions.append(sc)
                    break
        return results + additions

    def _ensure_child_fare_passage(
        self,
        question: str,
        agencies: list[str],
        results: list[ScoredChunk],
        ranked: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """Append the child/youth free-fare provision passage for a child-fare
        query when it fell out of top_k (see _CHILD_FARE_PROVISION, eval
        sens-010a). Mirrors _close_the_loop: appends at most one best-ranked
        passage per relevant agency and never removes anything."""
        if not _is_child_fare_query(question):
            return results
        targets = self._augmentation_targets(question, agencies, results)
        present = {sc.chunk.chunk_id for sc in results}
        additions: list[ScoredChunk] = []
        for ag in targets:
            if any(_is_child_fare_provision(sc.chunk) for sc in results if sc.chunk.agency == ag):
                continue
            for sc in ranked:
                if (
                    sc.chunk.agency == ag
                    and sc.chunk.chunk_id not in present
                    and _is_child_fare_provision(sc.chunk)
                ):
                    additions.append(sc)
                    break
        return results + additions

    def _fare_schedule_ids(self, agency: str) -> frozenset[str]:
        """The chunk ids holding `agency`'s fare schedule: those tied for the
        most fare products priced in one passage. A fact about the corpus, not
        about any query, so it is computed once per retriever."""
        if self._fare_schedules is None:
            best: dict[str, int] = {}
            for chunk in self.chunks:
                n = _priced_products(chunk)
                if n > best.get(chunk.agency, 0):
                    best[chunk.agency] = n
            self._fare_schedules = {
                ag: frozenset(
                    c.chunk_id for c in self.chunks if c.agency == ag and _priced_products(c) == n
                )
                for ag, n in best.items()
                if n >= _FARE_TABLE_MIN_PRICES
            }
        return self._fare_schedules.get(agency, frozenset())

    def _ensure_fare_table_passage(
        self,
        question: str,
        agencies: list[str],
        results: list[ScoredChunk],
        ranked: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """Issue #138: on a price question, make sure each relevant agency's own
        fare schedule survives into the answer prompt. See `_FARE_TABLE_PRICE`
        above for why BM25 loses it — a table names each fare once, the prose
        around it names them repeatedly — and for the measured ranks. Mirrors
        the three helpers above exactly: append at most one passage per
        relevant agency, never remove anything.

        The `_dominated_by_a_better_table` guard is not decoration. Without it
        this helper made ground-024 worse, live and reproducibly: "How much
        does a BeeLine on-demand ride in Woodland cost?" retrieves
        `yolobus-fares#2` first, the BeeLine table that prices Woodland at
        $3.00, and the answer was correct. Appending Yolobus's general
        fixed-route schedule (`yolobus-fares#1`, Local $2.00) put a second,
        off-topic price table in front of the model and the answer became
        "$2.00". That is this repo's own headline defect — a number attributed
        to the wrong table — caused by the fix for it, which makes the guard
        part of the fix rather than a refinement of it."""
        if not _is_fare_price_query(question):
            return results
        targets = self._augmentation_targets(question, agencies, results)
        present = {sc.chunk.chunk_id for sc in results}
        additions: list[ScoredChunk] = []
        for ag in targets:
            schedule = self._fare_schedule_ids(ag)
            if not schedule or any(sc.chunk.chunk_id in schedule for sc in results):
                continue  # this agency has no schedule, or it is already in hand
            for sc in ranked:
                if sc.chunk.chunk_id in schedule and sc.chunk.chunk_id not in present:
                    if not self._dominated_by_a_better_table(ag, sc, results):
                        additions.append(sc)
                    break
        return results + additions

    def _dominated_by_a_better_table(
        self, agency: str, schedule: ScoredChunk, results: list[ScoredChunk]
    ) -> bool:
        """True when BM25 has already found this agency a priced table that
        answers the question far better than its general schedule would.

        The rider who asks about one specific priced product — an on-demand
        zone, a single route — is served by the table for that product, and
        the agency's general schedule is then a competing set of numbers for a
        different service. Appending it is pure risk: the guarantee this helper
        provides is that a missing number arrives, and no number is missing.

        Both conditions have to hold, which is what keeps the guard narrow.
        Measured over the eleven facts this helper recovers, the schedule's
        score as a fraction of the agency's best retrieved passage runs 0.33 to
        0.75, and the three of those whose best passage is itself a priced
        table sit at 0.75, 0.44 and 0.37. ground-024 sits at 0.13 — its best
        passage outscores the schedule nearly eightfold. The floor is set
        between them with roughly a threefold margin to the nearest case it
        must keep.
        """
        best = max(
            (sc for sc in results if sc.chunk.agency == agency),
            key=lambda sc: sc.score,
            default=None,
        )
        if best is None or best.score <= 0:
            return False
        if _priced_products(best.chunk) < _FARE_TABLE_MIN_PRICES:
            return False  # the best passage is prose; the schedule is still needed
        return schedule.score / best.score < _FARE_SCHEDULE_RELEVANCE_FLOOR

    def _ensure_effective_date_passage(
        self,
        question: str,
        agencies: list[str],
        results: list[ScoredChunk],
        ranked: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """Issue #138 (freshness): on a question about when fares took effect or
        how long they last, make sure the agency's effective-date passage
        survives. See `_EFFECTIVE_DATE_PASSAGE` above for why it does not on
        its own. Mirrors the helpers around it: at most one passage per
        relevant agency, never removes anything."""
        if not _is_effective_date_query(question):
            return results
        targets = self._augmentation_targets(question, agencies, results)
        present = {sc.chunk.chunk_id for sc in results}
        additions: list[ScoredChunk] = []
        for ag in targets:
            if any(_is_effective_date_passage(sc.chunk) for sc in results if sc.chunk.agency == ag):
                continue
            for sc in ranked:
                if (
                    sc.chunk.agency == ag
                    and sc.chunk.chunk_id not in present
                    and _is_effective_date_passage(sc.chunk)
                ):
                    additions.append(sc)
                    break
        return results + additions

    def _close_the_loop(
        self,
        question: str,
        agencies: list[str],
        results: list[ScoredChunk],
        ranked: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """R1-2: on a reduced-fare/eligibility query, make sure the answer prompt
        also receives the agency's where-to-apply passage. If the fare and
        eligibility passages already won the top slots but the application/ID-card
        passage fell out of top_k, append the best-ranked one per relevant agency
        so the answer can state where to apply, the ID cost, and office hours."""
        if not _is_reduced_fare_query(question):
            return results
        targets = self._augmentation_targets(question, agencies, results)
        present = {sc.chunk.chunk_id for sc in results}
        additions: list[ScoredChunk] = []
        for ag in targets:
            if any(_is_application_passage(sc.chunk) for sc in results if sc.chunk.agency == ag):
                continue  # the where-to-apply passage is already in hand
            for sc in ranked:
                if (
                    sc.chunk.agency == ag
                    and sc.chunk.chunk_id not in present
                    and _is_application_passage(sc.chunk)
                    and _application_matches_question(question, sc.chunk)
                ):
                    additions.append(sc)
                    break
        return results + additions

    def confidence_signals(self, question: str, results: list[ScoredChunk]) -> ConfidenceSignals:
        """The three normalized signals `confident()` decides on. Exposed
        separately so evals/decline_calibration.py can sweep thresholds
        against them without re-deciding, and so `answer.py` can derive the
        rider-facing confidence band from the same numbers `confident()`
        used (never a second, disagreeing computation)."""
        if not results:
            return ConfidenceSignals(z_score=0.0, margin=0.0, term_coverage=0.0)
        background = [sc.score for sc in self._rank_all(question)]
        top = results[0].score
        mean = statistics.fmean(background)
        stdev = statistics.pstdev(background) or 1e-9
        z = (top - mean) / stdev
        second = results[1].score if len(results) > 1 else 0.0
        margin = (top - second) / (top + 1e-9)
        q_tokens = set(_expand_query(_tokenize(question)))
        top_chunk = results[0].chunk
        chunk_tokens = set(_tokenize(f"{top_chunk.section} {top_chunk.text}"))
        coverage = (len(q_tokens & chunk_tokens) / len(q_tokens)) if q_tokens else 0.0
        return ConfidenceSignals(z_score=z, margin=margin, term_coverage=coverage)

    def confident(self, question: str, results: list[ScoredChunk]) -> bool:
        """Low confidence → the assistant declines and redirects instead of
        guessing (FIX-07 / ADR 0013). Calibrated against a labeled
        should-answer/should-decline question set by
        evals/decline_calibration.py rather than an absolute BM25 constant,
        which silently re-tuned itself every time the corpus grew."""
        if not results:
            return False
        sig = self.confidence_signals(question, results)
        return (
            sig.z_score >= self.cfg.decline_z_threshold
            and sig.term_coverage >= self.cfg.decline_coverage_floor
        )


@lru_cache(maxsize=8)
def _retriever_for(
    profile_name: str,
    retrieval_config: config.RetrievalConfig,
    expected_content_version: str,
) -> Retriever:
    """Build one index for an exact profile, retrieval policy, and corpus.

    The second load is intentional: the caller's content digest is the cache
    key, while this function owns the actual chunks used by the index. If an
    atomic corpus publication lands between those two reads, fail this request
    instead of caching an index under the wrong identity.
    """
    from assistant.identity import content_version

    chunks = load_chunks()
    if content_version(chunks) != expected_content_version:
        raise RuntimeError("corpus changed while constructing the default retriever")
    return Retriever(chunks, retrieval_config)


def default_retriever() -> Retriever:
    """The process-wide retriever for one complete behavior identity."""
    from assistant.identity import content_version

    chunks = load_chunks()
    retrieval_config = config.Config.from_environment().retrieval
    return _retriever_for(
        domain.get_profile().name,
        retrieval_config,
        content_version(chunks),
    )
