"""Hybrid retrieval over the processed corpus.

BM25 (rank_bm25) is the default and works offline. Dense retrieval via
sentence-transformers is optional (`pip install .[dense]`, FPA_DENSE=1) and is
mixed with BM25 by a fixed weight — see ADR 0001 for why this stays simple.
When a question names a known agency, retrieval is filtered to that agency.
"""

from __future__ import annotations

import re
import statistics
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


def detect_agencies(question: str, aliases: dict[str, str] | None = None) -> list[str]:
    """All known scopes named in the question, in order of first mention. The
    alias map defaults to the active profile's but can be injected (a different
    domain, or a test) without touching this logic."""
    q = question.lower()
    selected_aliases = domain.get_profile().aliases if aliases is None else aliases
    matches: list[tuple[int, int, str]] = []
    for alias, agency in selected_aliases.items():
        match = re.search(rf"\b{re.escape(alias)}\b", q)
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
_CHILD_FARE_QUERY = re.compile(
    r"\b(child|children|kid|kids|toddler|toddlers|infant|infants|baby|babies|"
    r"son|daughter|\d+[- ]?year[- ]?olds?|years? old)\b",
    re.I,
)
_CHILD_FARE_PROVISION = re.compile(
    r"child(ren)?\s+under\s+\d+\s*inch"  # SBMTD: "Children under 45 inches tall"
    r"|(child(ren)?|youth)[^.]{0,40}\brides?\s+free"  # "youth … ride free"
    r"|youth\s+ages?\s+0",  # Yolobus: "Youth ages 0-18 ride free!"
    re.I,
)


def _is_child_fare_query(question: str) -> bool:
    return bool(_CHILD_FARE_QUERY.search(question))


def _is_child_fare_provision(chunk: Chunk) -> bool:
    return bool(_CHILD_FARE_PROVISION.search(f"{chunk.section} {chunk.text}"))


def _is_application_passage(chunk: Chunk) -> bool:
    """A passage that tells the rider how to apply for / obtain a reduced-fare
    program or ID card (where, cost, hours) — the 'close the loop' next step."""
    return bool(_APPLICATION_PASSAGE.search(f"{chunk.section} {chunk.text}"))


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
        return self._ensure_child_fare_passage(question, agencies, results, ranked)

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
        targets = list(agencies) or ([results[0].chunk.agency] if results else [])
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
        targets = list(agencies)
        if not targets and results:
            targets = [results[0].chunk.agency]
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
