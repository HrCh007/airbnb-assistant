"""Document ingestion: extract -> clean -> chunk -> embed -> store (pgvector)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models import KnowledgeChunk, Property, PropertyDocument
from app.rag.chunking import Chunk, chunk_sections
from app.rag.embeddings import get_embedder
from app.rag.extractors import ExtractionError, extract_sections
from app.rag.profile import PROFILE_FILENAME, build_profile_chunks

log = logging.getLogger(__name__)

# Keep strong references to background tasks so they aren't garbage collected.
_background: set[asyncio.Task] = set()


async def _store_chunks(
    db: AsyncSession, prop: Property, doc: PropertyDocument, chunks: list[Chunk]
) -> int:
    embedder = get_embedder()
    vectors = await embedder.embed_documents([c.text for c in chunks]) if chunks else []
    now = datetime.now(UTC)
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    for chunk, vec in zip(chunks, vectors, strict=True):
        chunk_id = uuid.uuid4()
        db.add(
            KnowledgeChunk(
                id=chunk_id,
                property_id=prop.id,
                document_id=doc.id,
                chunk_index=chunk.index,
                section=(chunk.section or None) and chunk.section[:255],
                content=chunk.text,
                is_sensitive=chunk.is_sensitive,
                embedding=vec,
                metadata_={
                    "property_id": str(prop.id),
                    "location": prop.location.slug if prop.location else None,
                    "document_name": doc.filename,
                    "document_type": doc.document_type,
                    "section": chunk.section,
                    "chunk_id": str(chunk_id),
                    "last_updated": now.isoformat(),
                    "embedding_provider": embedder.name,
                },
            )
        )
    doc.chunk_count = len(chunks)
    doc.last_indexed_at = now
    doc.status = "ready"
    doc.error_message = None
    return len(chunks)


async def process_document(document_id: uuid.UUID) -> None:
    """Index one uploaded document. Never raises: failures are recorded on the row."""
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
                data = (
                    await db.execute(
                        select(PropertyDocument.content).where(PropertyDocument.id == doc.id)
                    )
                ).scalar_one()
                if not data:
                    raise ExtractionError("Original file content is missing; please re-upload.")
                sections = extract_sections(doc.document_type, data)
                chunks = chunk_sections(sections, s.chunk_size, s.chunk_overlap)
            count = await _store_chunks(db, prop, doc, chunks)
            await db.commit()
            log.info("Indexed document %s (%s chunks)", doc.id, count)
        except Exception as exc:  # record any failure, keep the app running
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
    """Await outstanding ingestion tasks (used by tests and scripts)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


async def create_document(
    db: AsyncSession, prop: Property, filename: str, doc_type: str, data: bytes, mime: str | None
) -> PropertyDocument:
    doc = PropertyDocument(
        property_id=prop.id,
        filename=filename[:255],
        document_type=doc_type,
        mime_type=mime,
        size_bytes=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        status="pending",
    )
    db.add(doc)
    await db.flush()
    return doc


async def sync_profile_document(db: AsyncSession, prop: Property) -> PropertyDocument:
    """(Re)build the auto-generated profile document for a property, synchronously."""
    doc = (
        await db.execute(
            select(PropertyDocument).where(
                PropertyDocument.property_id == prop.id, PropertyDocument.is_system.is_(True)
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        doc = PropertyDocument(
            property_id=prop.id,
            filename=PROFILE_FILENAME,
            document_type="profile",
            mime_type="application/json",
            is_system=True,
            status="processing",
        )
        db.add(doc)
        await db.flush()
    await db.refresh(prop, ["location"])
    await _store_chunks(db, prop, doc, build_profile_chunks(prop))
    return doc
