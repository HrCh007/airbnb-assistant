"""Admin endpoints (JWT protected): auth, dashboard, properties, documents, curated picks."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import create_access_token, verify_password
from app.location.geocode import GeocodeError, geocode
from app.models import (
    AdminUser,
    Conversation,
    CuratedRecommendation,
    KnowledgeChunk,
    Location,
    Message,
    Property,
    PropertyDocument,
)
from app.rag import vectorstore
from app.rag.extractors import ExtractionError, detect_type, validate_magic
from app.rag.ingestion import (
    create_document,
    delete_document_files,
    reindex_property,
    schedule_processing,
    sync_profile_document,
)
from app.schemas.common import CuratedIn, CuratedOut, DocumentOut, LoginIn, TokenOut
from app.schemas.property import LocationCreate, LocationOut, PropertyAdminOut, PropertyCreate, PropertyUpdate
from app.services.properties import create_property, get_property, list_properties, to_admin_out, update_property
from app.services.runtime import runtime_status

log = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/admin", tags=["admin"])
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# --------------------------------------------------------------------------- auth
@auth_router.post("/login", response_model=TokenOut,
                  dependencies=[Depends(rate_limit("login", "rate_limit_login_per_minute"))])
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    admin = (await db.execute(select(AdminUser).where(func.lower(AdminUser.email) == body.email.lower().strip()))).scalar_one_or_none()
    # Verify against a dummy hash when the user doesn't exist to keep timing similar.
    ok = verify_password(body.password, admin.password_hash if admin else "$2b$12$" + "x" * 53)
    if not admin or not ok or not admin.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    admin.last_login_at = datetime.now(UTC)
    await db.commit()
    token, expires = create_access_token(str(admin.id))
    return TokenOut(access_token=token, expires_at=expires)


@router.get("/me")
async def me(admin: AdminUser = Depends(require_admin)):
    return {"id": str(admin.id), "email": admin.email, "full_name": admin.full_name}


# --------------------------------------------------------------------------- dashboard
@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    since = datetime.now(UTC) - timedelta(days=7)
    by_status = dict((await db.execute(select(PropertyDocument.status, func.count()).where(PropertyDocument.is_system.is_(False)).group_by(PropertyDocument.status))).all())
    intents = dict((await db.execute(select(Message.intent, func.count()).where(Message.role == "assistant", Message.created_at >= since, Message.intent.is_not(None)).group_by(Message.intent))).all())
    return {
        "properties": await db.scalar(select(func.count()).select_from(Property).where(Property.is_active.is_(True))),
        "inactive_properties": await db.scalar(select(func.count()).select_from(Property).where(Property.is_active.is_(False))),
        "placeholder_properties": await db.scalar(select(func.count()).select_from(Property).where(Property.is_placeholder.is_(True))),
        "missing_coordinates": await db.scalar(select(func.count()).select_from(Property).where(Property.latitude.is_(None))),
        "documents": sum(by_status.values()),
        "documents_by_status": by_status,
        "chunks": await db.scalar(select(func.count()).select_from(KnowledgeChunk)),
        "curated_recommendations": await db.scalar(select(func.count()).select_from(CuratedRecommendation)),
        "conversations_7d": await db.scalar(select(func.count()).select_from(Conversation).where(Conversation.created_at >= since)),
        "messages_7d": await db.scalar(select(func.count()).select_from(Message).where(Message.created_at >= since)),
        "intents_7d": intents,
        "integrations": await runtime_status(),
    }


# --------------------------------------------------------------------------- locations
@router.get("/locations", response_model=list[LocationOut])
async def admin_locations(db: AsyncSession = Depends(get_db)):
    from app.services.properties import list_locations

    return await list_locations(db)


@router.post("/locations", response_model=LocationOut, status_code=201)
async def create_location(body: LocationCreate, db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(Location).where(Location.slug == body.slug))).scalar_one_or_none():
        raise HTTPException(409, "A location with this slug already exists.")
    max_order = await db.scalar(select(func.max(Location.sort_order))) or 0
    loc = Location(**body.model_dump(), sort_order=max_order + 1)
    db.add(loc)
    await db.commit()
    return LocationOut.model_validate(loc)


# --------------------------------------------------------------------------- properties
@router.get("/properties", response_model=list[PropertyAdminOut])
async def admin_list_properties(db: AsyncSession = Depends(get_db)):
    return [await to_admin_out(db, p) for p in await list_properties(db, include_inactive=True)]


@router.get("/properties/{property_id}", response_model=PropertyAdminOut)
async def admin_get_property(property_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await to_admin_out(db, await get_property(db, property_id, include_inactive=True))


@router.post("/properties", response_model=PropertyAdminOut, status_code=201)
async def admin_create_property(body: PropertyCreate, db: AsyncSession = Depends(get_db)):
    prop = await create_property(db, body)
    return await to_admin_out(db, prop)


@router.put("/properties/{property_id}", response_model=PropertyAdminOut)
async def admin_update_property(property_id: uuid.UUID, body: PropertyUpdate, db: AsyncSession = Depends(get_db)):
    prop = await get_property(db, property_id, include_inactive=True)
    prop = await update_property(db, prop, body)
    return await to_admin_out(db, prop)


@router.delete("/properties/{property_id}", status_code=204,
               summary="Deactivate a property (or permanently delete with ?hard=true)")
async def admin_delete_property(property_id: uuid.UUID, hard: bool = False, db: AsyncSession = Depends(get_db)):
    prop = await get_property(db, property_id, include_inactive=True)
    if hard:
        docs = (await db.execute(select(PropertyDocument).where(PropertyDocument.property_id == prop.id))).scalars().all()
        for doc in docs:
            await delete_document_files(doc)
        await vectorstore.delete_property(prop.id)
        await db.delete(prop)  # cascades to documents, chunks and curated picks
    else:
        prop.is_active = False
    await db.commit()


# --------------------------------------------------------------------------- documents
def _doc_out(doc: PropertyDocument, name: str | None) -> DocumentOut:
    out = DocumentOut.model_validate(doc)
    out.property_name = name
    return out


@router.post("/documents/upload", response_model=DocumentOut, status_code=202)
async def upload_document(
    property_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    s = get_settings()
    prop = await get_property(db, property_id, include_inactive=True)
    filename = (file.filename or "document").replace("\\", "/").split("/")[-1][:255]
    try:
        doc_type = detect_type(filename)
    except ExtractionError as exc:
        raise HTTPException(415, str(exc)) from exc
    limit = s.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File too large (max {s.max_upload_mb} MB).")
    if not data:
        raise HTTPException(422, "File is empty.")
    try:
        validate_magic(doc_type, data)
    except ExtractionError as exc:
        raise HTTPException(415, str(exc)) from exc
    doc = await create_document(db, prop, filename, doc_type, data, file.content_type)
    await db.commit()
    schedule_processing(doc.id)
    return _doc_out(doc, prop.name)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    property_id: uuid.UUID | None = Query(default=None),
    include_system: bool = False,
    db: AsyncSession = Depends(get_db),
):
    q = select(PropertyDocument, Property.name).join(Property).order_by(PropertyDocument.uploaded_at.desc())
    if property_id:
        q = q.where(PropertyDocument.property_id == property_id)
    if not include_system:
        q = q.where(PropertyDocument.is_system.is_(False))
    return [_doc_out(d, n) for d, n in (await db.execute(q)).all()]


@router.get("/documents/{document_id}/chunks")
async def document_chunks(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id).order_by(KnowledgeChunk.chunk_index)
    )).scalars().all()
    return [
        {"id": str(c.id), "index": c.chunk_index, "section": c.section, "content": c.content,
         "is_sensitive": c.is_sensitive, "metadata": c.metadata_}
        for c in rows
    ]


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    doc = await db.get(PropertyDocument, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    if doc.is_system:
        raise HTTPException(400, "The auto-generated profile document is managed from the property form.")
    await delete_document_files(doc)  # vectors + stored file
    await db.delete(doc)  # chunk rows cascade
    await db.commit()


@router.post("/documents/{document_id}/reindex", response_model=DocumentOut, status_code=202)
async def reindex_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    doc = await db.get(PropertyDocument, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    prop = await db.get(Property, doc.property_id)
    if doc.is_system:
        await sync_profile_document(db, prop)
        await db.commit()
        await db.refresh(doc)
    else:
        doc.status = "pending"
        await db.commit()
        schedule_processing(doc.id)
    return _doc_out(doc, prop.name if prop else None)


@router.post("/documents/reindex-all", status_code=202,
             summary="Re-embed every document of every property (e.g. after changing the embedding model)")
async def reindex_all(db: AsyncSession = Depends(get_db)):
    props = (await db.execute(select(Property))).scalars().unique().all()
    count = 0
    for prop in props:
        count += await reindex_property(db, prop)
    return {"reindexed_documents": count}


# --------------------------------------------------------------------------- geocoding
@router.get("/geocode", summary="Find coordinates for an address (OpenStreetMap Nominatim)")
async def geocode_address(q: str = Query(min_length=3, max_length=300)):
    try:
        return {"results": await geocode(q), "attribution": "© OpenStreetMap contributors"}
    except GeocodeError as exc:
        raise HTTPException(503, str(exc)) from exc


# --------------------------------------------------------------------------- curated recommendations
@router.get("/curated", response_model=list[CuratedOut])
async def list_curated(property_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)):
    q = select(CuratedRecommendation).order_by(CuratedRecommendation.created_at.desc())
    if property_id:
        q = q.where(CuratedRecommendation.property_id == property_id)
    return list((await db.execute(q)).scalars())


@router.post("/curated", response_model=CuratedOut, status_code=201)
async def create_curated(body: CuratedIn, db: AsyncSession = Depends(get_db)):
    await get_property(db, body.property_id, include_inactive=True)
    rec = CuratedRecommendation(**body.model_dump())
    db.add(rec)
    await db.commit()
    return rec


@router.put("/curated/{rec_id}", response_model=CuratedOut)
async def update_curated(rec_id: uuid.UUID, body: CuratedIn, db: AsyncSession = Depends(get_db)):
    rec = await db.get(CuratedRecommendation, rec_id)
    if rec is None:
        raise HTTPException(404, "Recommendation not found")
    for k, v in body.model_dump().items():
        setattr(rec, k, v)
    await db.commit()
    await db.refresh(rec)
    return rec


@router.delete("/curated/{rec_id}", status_code=204)
async def delete_curated(rec_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(CuratedRecommendation).where(CuratedRecommendation.id == rec_id))
    await db.commit()
