"""Hybrid retrieval over a single property's knowledge base.

* Vector search: the property's own ChromaDB collection (cosine similarity).
* Keyword search: BM25 over the property's chunks in SQLite (catches exact terms
  like "Wi-Fi", "keybox", "B-12" that small embedding models can miss).
* Fusion: reciprocal rank fusion (k=60).

Isolation: `property_id` is required, selects the property's collection, is a
`where` filter in Chroma and in SQL, and every result is re-checked.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import KnowledgeChunk
from app.rag import vectorstore
from app.rag.embeddings import _SYNONYMS, _tokens, get_embedder

_RRF_K = 60


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    property_id: uuid.UUID
    document_id: uuid.UUID
    section: str | None
    content: str
    is_sensitive: bool
    metadata: dict
    vector_similarity: float | None = None
    keyword_score: float | None = None
    score: float = 0.0


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    withheld_sensitive: bool = False  # a sensitive chunk matched but the PIN isn't verified

    @property
    def best_similarity(self) -> float:
        sims = [c.vector_similarity for c in self.chunks if c.vector_similarity is not None]
        return max(sims) if sims else 0.0

    def sources(self) -> list[dict]:
        return [
            {
                "chunk_id": str(c.id),
                "document_id": str(c.document_id),
                "document_name": c.metadata.get("document_name"),
                "section": c.section,
                "score": round(c.score, 4),
                "similarity": round(c.vector_similarity, 4) if c.vector_similarity is not None else None,
            }
            for c in self.chunks
        ]


def _query_terms(q: str) -> list[str]:
    terms = []
    for t in _tokens(q):
        if len(t) < 2:
            continue
        terms.append(t)
        syn = _SYNONYMS.get(t)
        if syn and syn != t:
            terms.append(syn)
    return list(dict.fromkeys(terms))


def _bm25(rows: list[KnowledgeChunk], terms: list[str], k1: float = 1.4, b: float = 0.75) -> list[tuple[KnowledgeChunk, float]]:
    if not rows or not terms:
        return []
    docs = [Counter(_tokens(f"{r.section or ''} {r.content}")) for r in rows]
    avgdl = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
    n = len(docs)
    df = {t: sum(1 for d in docs if t in d) for t in terms}
    scored = []
    for row, d in zip(rows, docs, strict=True):
        dl = sum(d.values()) or 1
        score = 0.0
        for t in terms:
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scored.append((row, score))
    return sorted(scored, key=lambda x: x[1], reverse=True)


async def retrieve(
    db: AsyncSession,
    property_id: uuid.UUID,
    query: str,
    *,
    allow_sensitive: bool = True,
    top_k: int | None = None,
) -> RetrievalResult:
    if not isinstance(property_id, uuid.UUID):
        raise TypeError("property_id must be a UUID")
    s = get_settings()
    top_k = top_k or s.retrieval_top_k

    rows = (await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.property_id == property_id))).scalars().all()
    by_id = {r.id: r for r in rows}
    if not rows:
        return RetrievalResult()

    fused: dict[uuid.UUID, RetrievedChunk] = {}

    def _get(row: KnowledgeChunk) -> RetrievedChunk:
        rc = fused.get(row.id)
        if rc is None:
            rc = RetrievedChunk(
                id=row.id, property_id=row.property_id, document_id=row.document_id, section=row.section,
                content=row.content, is_sensitive=row.is_sensitive, metadata=row.metadata_ or {},
            )
            fused[row.id] = rc
        return rc

    # Vector search in the property's own collection.
    qvec = await get_embedder().embed_query(query)
    hits = await vectorstore.query(property_id, qvec, s.retrieval_candidate_k)
    rank = 0
    for hit in hits:
        row = by_id.get(hit.chunk_id)
        if row is None or hit.similarity < s.retrieval_min_vector_similarity:
            continue  # stale vector or weak match
        rc = _get(row)
        rc.vector_similarity = hit.similarity
        rc.score += 1.0 / (_RRF_K + rank + 1)
        rank += 1

    # Keyword search (BM25) over the same property's chunks.
    for rank, (row, score) in enumerate(_bm25(list(rows), _query_terms(query))[: s.retrieval_candidate_k]):
        rc = _get(row)
        rc.keyword_score = score
        rc.score += 1.0 / (_RRF_K + rank + 1)

    ranked = sorted(fused.values(), key=lambda c: c.score, reverse=True)
    ranked = [c for c in ranked if c.property_id == property_id]  # defence in depth

    result = RetrievalResult()
    for position, c in enumerate(ranked):
        if c.is_sensitive and not allow_sensitive:
            if position < 2:
                result.withheld_sensitive = True
            continue
        result.chunks.append(c)
        if len(result.chunks) >= top_k:
            break
    return result
