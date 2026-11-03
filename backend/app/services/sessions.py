"""Guest chat sessions.

A session is created with a random token returned once to the browser; only its
SHA-256 hash is stored. Every read/write requires the token, so knowing a
session UUID is not enough to read another guest's conversation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import verify_password
from app.models import Conversation, GuestPreferences, Location, Property
from app.services.properties import get_location_by_slug, list_properties, to_summary

PREF_FIELDS = ("budget", "dietary", "companions", "interests", "travel_mode", "max_distance_km")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: AsyncSession) -> tuple[Conversation, str]:
    token = secrets.token_urlsafe(32)
    conv = Conversation(client_token_hash=_hash_token(token))
    db.add(conv)
    await db.flush()
    db.add(GuestPreferences(conversation_id=conv.id))
    await db.commit()
    return conv, token


async def load_session(db: AsyncSession, session_id: uuid.UUID, token: str | None) -> Conversation:
    conv = await db.get(Conversation, session_id)
    if conv is None or not token or not hmac.compare_digest(conv.client_token_hash, _hash_token(token)):
        # Same error for "missing" and "wrong token" to avoid leaking existence.
        raise NotFoundError("Session")
    return conv


async def get_preferences(db: AsyncSession, conv: Conversation) -> GuestPreferences:
    prefs = (
        await db.execute(select(GuestPreferences).where(GuestPreferences.conversation_id == conv.id))
    ).scalar_one_or_none()
    if prefs is None:
        prefs = GuestPreferences(conversation_id=conv.id)
        db.add(prefs)
        await db.flush()
    return prefs


def preferences_dict(prefs: GuestPreferences | None) -> dict:
    if prefs is None:
        return {}
    return {f: getattr(prefs, f) for f in PREF_FIELDS if getattr(prefs, f) not in (None, [], "")}


async def select_location(db: AsyncSession, conv: Conversation, location_slug: str) -> tuple[Location, list[Property]]:
    loc = await get_location_by_slug(db, location_slug)
    props = await list_properties(db, loc.slug)
    conv.location_id = loc.id
    # Auto-select when exactly one property exists in the location.
    conv.property_id = props[0].id if len(props) == 1 else None
    await db.commit()
    return loc, props


async def select_property(db: AsyncSession, conv: Conversation, property_id: uuid.UUID) -> Property:
    prop = await db.get(Property, property_id)
    if prop is None or not prop.is_active:
        raise NotFoundError("Property")
    conv.property_id = prop.id
    conv.location_id = prop.location_id
    await db.commit()
    return prop


async def verify_pin(db: AsyncSession, conv: Conversation, pin: str) -> bool:
    if conv.property_id is None:
        return False
    prop = await db.get(Property, conv.property_id)
    if prop is None or not prop.guest_pin_hash:
        return True
    if verify_password(pin, prop.guest_pin_hash):
        conv.pin_verified_property_id = prop.id
        await db.commit()
        return True
    return False


async def session_view(db: AsyncSession, conv: Conversation, token: str | None = None) -> dict:
    loc = await db.get(Location, conv.location_id) if conv.location_id else None
    prop = await db.get(Property, conv.property_id) if conv.property_id else None
    available = [to_summary(p).model_dump(mode="json") for p in await list_properties(db, loc.slug)] if loc else []
    prefs = await get_preferences(db, conv)
    return {
        "session_id": conv.id,
        "session_token": token,
        "location_slug": loc.slug if loc else None,
        "location_name": loc.name if loc else None,
        "property": to_summary(prop).model_dump(mode="json") if prop else None,
        "available_properties": available,
        "preferences": preferences_dict(prefs),
        "pin_verified": bool(prop and (not prop.guest_pin_hash or conv.pin_verified_property_id == prop.id)),
        "created_at": conv.created_at,
    }
