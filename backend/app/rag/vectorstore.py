"""ChromaDB vector store.

Isolation by construction: every property gets its *own* collection
(`prop_<uuid-hex>`), so a similarity search physically cannot return another
property's chunks. Each chunk additionally carries `property_id` metadata and
the query applies a `where` filter on it, plus a post-filter in the retriever.

Modes (CHROMA_MODE):
* http        - the `chromadb` Docker service (default in docker-compose)
* persistent  - embedded on-disk store in <DATA_DIR>/chroma (no container)
* memory      - ephemeral, for tests
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from functools import partial

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError

log = logging.getLogger(__name__)

_client = None


def _make_client():
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    s = get_settings()
    cs = ChromaSettings(anonymized_telemetry=False, allow_reset=s.environment == "test")
    if s.chroma_mode == "http":
        return chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port, settings=cs)
    if s.chroma_mode == "memory":
        return chromadb.EphemeralClient(settings=cs)
    return chromadb.PersistentClient(path=str(s.data_path / "chroma"), settings=cs)


def get_client():
    global _client
    if _client is None:
        _client = _make_client()
    return _client


def reset_client() -> None:
    global _client
    _client = None


def collection_name(property_id: uuid.UUID) -> str:
    return f"prop_{property_id.hex}"


async def _run(fn, *args, **kwargs):
    """Run a blocking Chroma call in a thread; map failures to a 503-style error."""
    try:
        return await asyncio.to_thread(partial(fn, *args, **kwargs))
    except ServiceUnavailableError:
        raise
    except Exception as exc:  # connection refused, timeouts, server errors
        log.error("Vector store error: %s: %s", type(exc).__name__, str(exc)[:200])
        raise ServiceUnavailableError("The knowledge base is temporarily unavailable.") from exc


def _collection(property_id: uuid.UUID, embedder_name: str | None = None):
    meta = {"hnsw:space": "cosine", "property_id": str(property_id)}
    if embedder_name:
        meta["embedder"] = embedder_name
    return get_client().get_or_create_collection(name=collection_name(property_id), metadata=meta)


@dataclass
class VectorHit:
    chunk_id: uuid.UUID
    similarity: float
    metadata: dict


async def heartbeat() -> bool:
    try:
        await asyncio.to_thread(get_client().heartbeat)
        return True
    except Exception:
        return False


async def collection_embedder(property_id: uuid.UUID) -> str | None:
    def _get():
        try:
            col = get_client().get_collection(collection_name(property_id))
        except Exception:
            return None
        return (col.metadata or {}).get("embedder")

    return await _run(_get)


async def upsert_chunks(
    property_id: uuid.UUID, embedder_name: str, ids: list[uuid.UUID], embeddings: list[list[float]],
    documents: list[str], metadatas: list[dict],
) -> None:
    if not ids:
        return

    def _upsert():
        col = _collection(property_id, embedder_name)
        if (col.metadata or {}).get("embedder") not in (None, embedder_name):
            # Embedder changed: this collection's vectors are incompatible. Recreate it.
            get_client().delete_collection(collection_name(property_id))
            col = _collection(property_id, embedder_name)
        clean = [{k: v for k, v in m.items() if v is not None and isinstance(v, (str, int, float, bool))} for m in metadatas]
        col.upsert(ids=[str(i) for i in ids], embeddings=embeddings, documents=documents, metadatas=clean)

    await _run(_upsert)


async def delete_document(property_id: uuid.UUID, document_id: uuid.UUID) -> None:
    def _delete():
        try:
            col = get_client().get_collection(collection_name(property_id))
        except Exception:
            return
        col.delete(where={"document_id": str(document_id)})

    await _run(_delete)


async def delete_property(property_id: uuid.UUID) -> None:
    def _delete():
        try:
            get_client().delete_collection(collection_name(property_id))
        except Exception:
            pass

    await _run(_delete)


async def query(property_id: uuid.UUID, embedding: list[float], k: int) -> list[VectorHit]:
    def _query():
        try:
            col = get_client().get_collection(collection_name(property_id))
        except Exception:
            return []  # no documents indexed for this property yet
        n = col.count()
        if n == 0:
            return []
        res = col.query(
            query_embeddings=[embedding],
            n_results=min(k, n),
            where={"property_id": str(property_id)},
            include=["metadatas", "distances"],
        )
        hits = []
        for cid, meta, dist in zip(res["ids"][0], res["metadatas"][0], res["distances"][0], strict=True):
            hits.append(VectorHit(chunk_id=uuid.UUID(cid), similarity=1.0 - float(dist), metadata=meta or {}))
        return hits

    return await _run(_query)
