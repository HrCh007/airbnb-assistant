"""Document ingestion: extract -> clean -> chunk -> embed locally -> store.

* Original files are written to the uploads volume (<DATA_DIR>/uploads/<property>/<document>.<ext>).
* Chunk text + metadata go to SQLite (`knowledge_chunks`, used for keyword
  search and the admin chunk viewer).
* Vectors go to the property's own ChromaDB collection, keyed by the same chunk id.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models import KnowledgeChunk, Property, PropertyDocument
from app.rag import vectorstore
from app.rag.chunking import Chunk, chunk_sections
from app.rag.embeddings import get_embedder
from app.rag.extractors import ExtractionError, extract_sections
from app.rag.profile import PROFILE_FILENAME, build_profile_chunks

log = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()


def _storage_path(property_id: uuid.UUID, document_id: uuid.UUID, doc_type: str) -> Path:
    folder = get_settings().uploads_path / str(property_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{document_id}.{doc_type}"


async def _store_chunks(db: AsyncSession, prop: Property, doc: PropertyDocument, chunks: list[Chunk]) -> int:
    embedder = get_embedder()
    vectors = await embedder.embed_documents([c.text for c in chunks]) if chunks else []
    now = datetime.now(UTC)
    # Replace any previous chunks of this document (re-indexing).
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    await vectorstore.delete_document(prop.id, doc.id)
    ids, metas, texts = [], [], []
    for chunk in chunks:
        chunk_id = uuid.uuid4()
        meta = {
            "property_id": str(prop.id),
            "location": prop.location.slug if prop.location else None,
            "document_id": str(doc.id),
            "document_name": doc.filename,
            "document_type": doc.document_type,
            "section": chunk.section,
            "chunk_id": str(chunk_id),
            "chunk_index": chunk.index,
            "source": "property_profile" if doc.is_system else f"upload:{doc.filename}",
            "is_sensitive": chunk.is_sensitive,
            "last_updated": now.isoformat(),
            "embedder": embedder.name,
        }
        db.add(
            KnowledgeChunk(
                id=chunk_id, property_id=prop.id, document_id=doc.id, chunk_index=chunk.index,
                section=(chunk.section or None) and chunk.section[:255], content=chunk.text,
                is_sensitive=chunk.is_sensitive, metadata_=meta,
            )
        )
        ids.append(chunk_id)
        metas.append(meta)
        texts.append(chunk.text)
    await vectorstore.upsert_chunks(prop.id, embedder.name, ids, vectors, texts, metas)
    doc.chunk_count = len(chunks)
    doc.last_indexed_at = now
    doc.status = "ready"
    doc.error_message = None
    return len(chunks)


async def process_document(document_id: uuid.UUID) -> None:
    """Index one document. Never raises: failures are recorded on the row."""
    s = get_settings()
    async with get_sessionmaker()() as db:
        doc = await db.get(PropertyDocument, document_id)
        if doc is None:
            return
        doc.status = "processing"
        await db.commit()
        try:
            prop = await db.get(Property, doc.property_id)
            if prop is None:
                raise ExtractionError("Property no longer exists.")
            if doc.is_system:
                chunks = build_profile_chunks(prop)
            else:
                path = Path(doc.storage_path or "")
                if not await asyncio.to_thread(path.is_file):
                    raise ExtractionError("Original file is missing from storage; please re-upload it.")
                data = await asyncio.to_thread(path.read_bytes)
                sections = await asyncio.to_thread(extract_sections, doc.document_type, data)
                chunks = chunk_sections(sections, s.chunk_size, s.chunk_overlap)
            count = await _store_chunks(db, prop, doc, chunks)
            await db.commit()
            log.info("Indexed document %s (%s chunks)", doc.id, count)
        except Exception as exc:
            await db.rollback()
            doc = await db.get(PropertyDocument, document_id)
            if doc is not None:
                doc.status = "failed"
                doc.error_message = (
                    str(exc) if isinstance(exc, ExtractionError) else f"Processing failed: {type(exc).__name__}"
                )[:1000]
                await db.commit()
            log.warning("Document %s failed to index: %s", document_id, type(exc).__name__)


def schedule_processing(document_id: uuid.UUID) -> None:
    task = asyncio.create_task(process_document(document_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def wait_for_background() -> None:
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


async def create_document(
    db: AsyncSession, prop: Property, filename: str, doc_type: str, data: bytes, mime: str | None
) -> PropertyDocument:
    doc_id = uuid.uuid4()
    path = _storage_path(prop.id, doc_id, doc_type)
    await asyncio.to_thread(path.write_bytes, data)
    doc = PropertyDocument(
        id=doc_id, property_id=prop.id, filename=filename[:255], document_type=doc_type, mime_type=mime,
        size_bytes=len(data), checksum_sha256=hashlib.sha256(data).hexdigest(), storage_path=str(path),
        status="pending",
    )
    db.add(doc)
    await db.flush()
    return doc


async def delete_document_files(doc: PropertyDocument) -> None:
    await vectorstore.delete_document(doc.property_id, doc.id)
    if doc.storage_path:
        await asyncio.to_thread(Path(doc.storage_path).unlink, missing_ok=True)


async def sync_profile_document(db: AsyncSession, prop: Property) -> PropertyDocument:
    """(Re)build the auto-generated profile document for a property."""
    doc = (
        await db.execute(
            select(PropertyDocument).where(PropertyDocument.property_id == prop.id, PropertyDocument.is_system.is_(True))
        )
    ).scalar_one_or_none()
    if doc is None:
        doc = PropertyDocument(
            property_id=prop.id, filename=PROFILE_FILENAME, document_type="profile",
            mime_type="application/json", is_system=True, status="processing",
        )
        db.add(doc)
        await db.flush()
    await db.refresh(prop, ["location"])
    await _store_chunks(db, prop, doc, build_profile_chunks(prop))
    return doc


async def reindex_property(db: AsyncSession, prop: Property) -> int:
    """Re-embed every document of a property (e.g. after the embedding model changed)."""
    docs = (await db.execute(select(PropertyDocument).where(PropertyDocument.property_id == prop.id))).scalars().all()
    await vectorstore.delete_property(prop.id)
    for doc in docs:
        if doc.is_system:
            await sync_profile_document(db, prop)
        else:
            doc.status = "pending"
    await db.commit()
    for doc in docs:
        if not doc.is_system:
            await process_document(doc.id)
    return len(docs)
