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

from assistant import config
from assistant.ingest import Chunk, load_chunks

# Aliases riders actually use, mapped to manifest agency keys.
AGENCY_ALIASES: dict[str, str] = {
    "mst": "MST",
    "monterey": "MST",
    "monterey-salinas": "MST",
    "salinas": "MST",
    "sbmtd": "SBMTD",
    "santa barbara": "SBMTD",
    "mtd": "SBMTD",
    "yolobus": "Yolobus",
    "yolo": "Yolobus",
    "sacrt": "SacRT",
    "sacramento": "SacRT",
}


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


def detect_agency(question: str) -> str | None:
    q = question.lower()
    for alias, agency in AGENCY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", q):
            return agency
    return None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúüñ0-9$]+", text.lower())


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
        agency = agency or detect_agency(question)
        scores = self._bm25.get_scores(_tokenize(question))
        if self._dense is not None:
            model, embeddings = self._dense
            q_emb = model.encode([question], normalize_embeddings=True)[0]
            dense_scores = embeddings @ q_emb
            # Scale dense cosine ([-1,1]) into BM25's rough range before mixing.
            bm25_max = max(float(scores.max()), 1.0)
            w = self.cfg.dense_weight
            scores = (1 - w) * scores + w * dense_scores * bm25_max

        ranked = sorted(
            (
                ScoredChunk(chunk=c, score=float(s))
                for c, s in zip(self.chunks, scores, strict=True)
            ),
            key=lambda sc: sc.score,
            reverse=True,
        )
        if agency:
            ranked = [sc for sc in ranked if sc.chunk.agency == agency]
        return ranked[: self.cfg.top_k]

    def confident(self, results: list[ScoredChunk]) -> bool:
        """Low confidence → the assistant declines and redirects instead of guessing."""
        return bool(results) and results[0].score >= self.cfg.min_confidence


@lru_cache(maxsize=1)
def default_retriever() -> Retriever:
    return Retriever()
