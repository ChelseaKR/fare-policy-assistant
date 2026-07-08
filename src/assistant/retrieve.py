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

# Aliases users actually type, mapped to scope keys. Sourced from the active
# domain profile (src/assistant/domain.py).
AGENCY_ALIASES: dict[str, str] = domain.get_profile().aliases


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
    found: list[str] = []
    for alias, agency in (aliases or AGENCY_ALIASES).items():
        if agency not in found and re.search(rf"\b{re.escape(alias)}\b", q):
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
            ranked = [sc for sc in ranked if sc.chunk.agency == agencies[0]]
            return ranked[: self.cfg.top_k]
        if agencies:
            # A question comparing agencies needs passages from each; a plain
            # union lets one agency's stronger lexical matches take every slot
            # (eval cases edge-004, edge-011). Give each agency an equal quota.
            quota = max(2, self.cfg.top_k // len(agencies))
            picked: list[ScoredChunk] = []
            for ag in agencies:
                picked.extend([sc for sc in ranked if sc.chunk.agency == ag][:quota])
            return sorted(picked, key=lambda sc: sc.score, reverse=True)
        return ranked[: self.cfg.top_k]

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
        guessing (FIX-07 / ADR 0009). Calibrated against a labeled
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


@lru_cache(maxsize=1)
def default_retriever() -> Retriever:
    return Retriever()
