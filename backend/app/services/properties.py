"""Property and location service functions."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import hash_password
from app.models import KnowledgeChunk, Location, Property, PropertyDocument
from app.rag.ingestion import sync_profile_document
from app.schemas.property import (
    LocationOut,
    PropertyAdminOut,
    PropertyCreate,
    PropertyDetails,
    PropertySummary,
    PropertyUpdate,
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70] or "property"


def to_summary(p: Property) -> PropertySummary:
    return PropertySummary(
        id=p.id,
        slug=p.slug,
        name=p.name,
        location_slug=p.location.slug,
        location_name=p.location.name,
        neighbourhood=p.neighbourhood,
        address=p.address,
        bedrooms=p.bedrooms,
        bathrooms=p.bathrooms,
        max_guests=p.max_guests,
        description=p.description,
        amenities=list(p.amenities or []),
        has_coordinates=p.latitude is not None and p.longitude is not None,
        requires_pin=bool(p.guest_pin_hash),
        is_placeholder=p.is_placeholder,
    )


async def to_admin_out(db: AsyncSession, p: Property) -> PropertyAdminOut:
    doc_count = await db.scalar(
        select(func.count()).select_from(PropertyDocument).where(PropertyDocument.property_id == p.id, PropertyDocument.is_system.is_(False))
    )
    chunk_count = await db.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.property_id == p.id)
    )
    base = to_summary(p).model_dump()
    return PropertyAdminOut(
        **base,
        latitude=p.latitude,
        longitude=p.longitude,
        discovery_radius_m=p.discovery_radius_m,
        details=PropertyDetails.model_validate(p.details or {}),
        is_active=p.is_active,
        created_at=p.created_at,
        updated_at=p.updated_at,
        document_count=doc_count or 0,
        chunk_count=chunk_count or 0,
    )


async def list_locations(db: AsyncSession) -> list[LocationOut]:
    rows = (
        await db.execute(
            select(Location, func.count(Property.id))
            .outerjoin(Property, (Property.location_id == Location.id) & Property.is_active.is_(True))
            .where(Location.is_active.is_(True))
            .group_by(Location.id)
            .order_by(Location.sort_order, Location.name)
        )
    ).all()
    out = []
    for loc, count in rows:
        item = LocationOut.model_validate(loc)
        item.property_count = count
        out.append(item)
    return out


async def get_location_by_slug(db: AsyncSession, slug: str) -> Location:
    loc = (await db.execute(select(Location).where(Location.slug == slug.lower().strip()))).scalar_one_or_none()
    if loc is None:
        raise NotFoundError("Location")
    return loc


async def find_location(db: AsyncSession, text: str) -> Location | None:
    t = text.lower().strip()
    for loc in (await db.execute(select(Location).where(Location.is_active.is_(True)))).scalars():
        if loc.slug in t or loc.name.lower() in t:
            return loc
    return None


async def list_properties(db: AsyncSession, location_slug: str | None = None, include_inactive: bool = False) -> list[Property]:
    q = select(Property).join(Location).order_by(Location.sort_order, Property.name)
    if not include_inactive:
        q = q.where(Property.is_active.is_(True))
    if location_slug:
        q = q.where(Location.slug == location_slug)
    return list((await db.execute(q)).scalars().unique())


async def get_property(db: AsyncSession, property_id: uuid.UUID, include_inactive: bool = False) -> Property:
    p = await db.get(Property, property_id)
    if p is None or (not p.is_active and not include_inactive):
        raise NotFoundError("Property")
    return p


async def _unique_slug(db: AsyncSession, base: str, exclude: uuid.UUID | None = None) -> str:
    slug, n = base, 2
    while True:
        q = select(Property.id).where(Property.slug == slug)
        existing = (await db.execute(q)).scalar_one_or_none()
        if existing is None or existing == exclude:
            return slug
        slug, n = f"{base}-{n}", n + 1


async def create_property(db: AsyncSession, data: PropertyCreate) -> Property:
    loc = await get_location_by_slug(db, data.location_slug)
    payload = data.model_dump(exclude={"location_slug", "guest_pin", "details", "slug"})
    prop = Property(
        **payload,
        location_id=loc.id,
        slug=await _unique_slug(db, data.slug or slugify(data.name)),
        details=data.details.model_dump(exclude_none=True),
        guest_pin_hash=hash_password(data.guest_pin) if data.guest_pin else None,
    )
    db.add(prop)
    await db.flush()
    await db.refresh(prop, ["location"])
    await sync_profile_document(db, prop)
    await db.commit()
    return prop


async def update_property(db: AsyncSession, prop: Property, data: PropertyUpdate) -> Property:
    changes = data.model_dump(exclude_unset=True)
    if "location_slug" in changes:
        loc = await get_location_by_slug(db, changes.pop("location_slug"))
        prop.location_id = loc.id
    if "guest_pin" in changes:
        pin = changes.pop("guest_pin")
        if pin is not None:
            prop.guest_pin_hash = hash_password(pin) if pin else None
    if "details" in changes:
        changes.pop("details")
        prop.details = data.details.model_dump(exclude_none=True) if data.details else {}
    for key, value in changes.items():
        setattr(prop, key, value)
    await db.flush()
    await db.refresh(prop, ["location", "updated_at"])
    await sync_profile_document(db, prop)
    await db.commit()
    await db.refresh(prop)
    return prop
