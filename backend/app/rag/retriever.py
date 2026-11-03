"""Hybrid retrieval over a single property's knowledge base.

Isolation guarantee: `property_id` is a required argument and is applied as a
WHERE filter inside both the vector and the keyword query, i.e. *before*
ranking. There is no code path that searches across properties.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag.embeddings import _STOP, _SYNONYMS, get_embedder, normalise_terms

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
    keyword_rank: float | None = None
    score: float = 0.0


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    withheld_sensitive: bool = False  # sensitive chunks matched but PIN not verified

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


def _keyword_query(q: str) -> str | None:
    q = normalise_terms(q)
    words = re.findall(r"[a-z0-9]+", q)
    terms: list[str] = []
    for w in words:
        if w in _STOP or len(w) < 2:
            continue
        terms.append(w)
        syn = _SYNONYMS.get(w)
        if syn and syn != w:
            terms.append(syn)
    terms = list(dict.fromkeys(terms))[:16]
    return " | ".join(terms) if terms else None


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
    cand = s.retrieval_candidate_k

    qvec = await get_embedder().embed_query(query)
    vec_literal = "[" + ",".join(f"{v:.7f}" for v in qvec) + "]"

    await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
    vector_rows = (
        await db.execute(
            text(
                """
                SELECT id, property_id, document_id, section, content, is_sensitive, metadata,
                       1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
                FROM knowledge_chunks
                WHERE property_id = :pid
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :k
                """
            ),
            {"qvec": vec_literal, "pid": property_id, "k": cand},
        )
    ).mappings().all()

    keyword_rows = []
    kq = _keyword_query(query) if s.hybrid_search else None
    if kq:
        keyword_rows = (
            await db.execute(
                text(
                    """
                    SELECT id, property_id, document_id, section, content, is_sensitive, metadata,
                           ts_rank_cd(tsv, to_tsquery('english', :kq)) AS rank
                    FROM knowledge_chunks
                    WHERE property_id = :pid AND tsv @@ to_tsquery('english', :kq)
                    ORDER BY rank DESC
                    LIMIT :k
                    """
                ),
                {"kq": kq, "pid": property_id, "k": cand},
            )
        ).mappings().all()

    fused: dict[uuid.UUID, RetrievedChunk] = {}

    def _get(row) -> RetrievedChunk:
        rc = fused.get(row["id"])
        if rc is None:
            rc = RetrievedChunk(
                id=row["id"],
                property_id=row["property_id"],
                document_id=row["document_id"],
                section=row["section"],
                content=row["content"],
                is_sensitive=row["is_sensitive"],
                metadata=row["metadata"] or {},
            )
            fused[row["id"]] = rc
        return rc

    for rank, row in enumerate(vector_rows):
        sim = float(row["similarity"])
        if sim < s.retrieval_min_vector_similarity:
            continue
        rc = _get(row)
        rc.vector_similarity = sim
        rc.score += 1.0 / (_RRF_K + rank + 1)
    for rank, row in enumerate(keyword_rows):
        rc = _get(row)
        rc.keyword_rank = float(row["rank"])
        rc.vector_similarity = rc.vector_similarity  # may stay None
        rc.score += 1.0 / (_RRF_K + rank + 1)

    ranked = sorted(fused.values(), key=lambda c: c.score, reverse=True)
    # Defence in depth: never return a chunk from another property.
    ranked = [c for c in ranked if c.property_id == property_id]

    result = RetrievalResult()
    for position, c in enumerate(ranked):
        if c.is_sensitive and not allow_sensitive:
            # Only signal "withheld" when the sensitive fact was a top match.
            if position < 2:
                result.withheld_sensitive = True
            continue
        result.chunks.append(c)
        if len(result.chunks) >= top_k:
            break
    return result
