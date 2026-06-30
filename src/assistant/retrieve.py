"""Hybrid retrieval over the processed corpus.

BM25 (rank_bm25) is the default and works offline. Dense retrieval via
sentence-transformers is optional (`pip install .[dense]`, FPA_DENSE=1) and is
mixed with BM25 by a fixed weight — see ADR 0001 for why this stays simple.
When a question names a known agency, retrieval is filtered to that agency.
"""

from __future__ import annotations

import re
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
    "pasaje": "fare", "tarifa": "fare", "tarifas": "fares", "boleto": "ticket",
    "mensual": "monthly", "semanal": "weekly",
    "diario": "daily", "descuento": "discount", "reducido": "reduced",
    "reducida": "reduced", "mayores": "seniors senior", "mayor": "senior",
    "niños": "children youth kids", "jóvenes": "youth", "gratis": "free",
    "edad": "age", "años": "years", "veterano": "veteran", "veteranos": "veterans",
    "discapacidad": "disability disabled", "estudiante": "student",
    "estudiantes": "students", "autobús": "bus", "viajan": "ride",
    "viajar": "ride", "cuesta": "cost costs", "costo": "cost", "precio": "price",
    "sencillo": "single", "tarjeta": "card", "prueba": "proof",
    "esposa": "spouse", "esposo": "spouse", "cónyuge": "spouse",
    "mes": "month", "identificación": "identification id",
    "pagan": "pay fare", "paga": "pay fare", "pagar": "pay fare",
    "viaje": "ride trip", "viajes": "rides trips", "personas": "persons",
    # GoPass is MST's product name for passes; the fare tables say "GoPass"
    # where a rider says "pase" (eval case ml-002).
    "pases": "passes gopass", "pase": "pass gopass",
}


def _tokenize(text: str) -> list[str]:
    """Word tokens, minus 1–2 letter noise ("a", "en", "of") that lets BM25
    score unrelated chunks on articles alone. Digits, "$", and "id" stay —
    ages, prices, and ID cards are exactly what riders ask about."""
    raw = re.findall(r"[a-záéíóúüñ0-9$]+", text.lower())
    return [
        t for t in raw
        if len(t) > 2 or t == "id" or any(ch.isdigit() for ch in t) or "$" in t
    ]


# English variants BM25 can't bridge without stemming: riders say "disabled",
# the policies say "Persons with Disabilities" (eval cases refuse-002,
# edge-011).
_EN_SYNONYMS: dict[str, str] = {
    "disabled": "disabilities disability",
    "disability": "disabled disabilities",
    "kid": "youth child", "kids": "youth children",
    "teen": "youth", "teenager": "youth",
}

# Stretch language: Tagalog, a high-demand California language that, unlike
# Chinese, is space-delimited Latin script, so the existing tokenizer handles it
# and only a fare-vocabulary lexicon is needed to bridge a Tagalog query to the
# English-only corpus. This is the retrieval half of stretch-language support
# (R2-3); answering *in* Tagalog and a parity suite need a live model and a
# detect_language extension, and are tracked as live-gated work.
_TL_EN_LEXICON: dict[str, str] = {
    "pamasahe": "fare", "magkano": "how much cost price", "diskwento": "discount reduced",
    "nakatatanda": "senior seniors", "matatanda": "seniors senior", "libre": "free",
    "bata": "youth child children", "estudyante": "student students",
    "beterano": "veteran veterans", "kapansanan": "disability disabled",
    "buwanang": "monthly", "tiket": "ticket", "pasahero": "rider passenger",
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

    def search(self, question: str, agency: str | None = None) -> list[ScoredChunk]:
        from assistant.guards import detect_language

        agencies = [agency] if agency else detect_agencies(question)
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
        ranked = sorted(
            (
                ScoredChunk(chunk=c, score=s)
                for c, s in zip(self.chunks, boosted, strict=True)
            ),
            key=lambda sc: sc.score,
            reverse=True,
        )
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

    def confident(self, results: list[ScoredChunk]) -> bool:
        """Low confidence → the assistant declines and redirects instead of guessing."""
        return bool(results) and results[0].score >= self.cfg.min_confidence


@lru_cache(maxsize=1)
def default_retriever() -> Retriever:
    return Retriever()
