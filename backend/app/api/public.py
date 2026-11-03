"""Guest-facing endpoints: locations, properties, chat sessions, chat, places."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import session_token
from app.core.config import get_settings
from app.core.database import get_db, get_sessionmaker
from app.core.rate_limit import rate_limit
from app.location.categories import QUICK_ACTION_QUERIES
from app.location.discovery import DiscoveryRequest, discover, place_details
from app.location.providers import get_places_provider
from app.models import Message
from app.schemas.common import (
    ChatMessageIn,
    MessageOut,
    PinVerifyIn,
    PlaceResult,
    PlacesSearchRequest,
    PlacesSearchResponse,
    SessionCreate,
    SessionOut,
    SessionSelect,
)
from app.schemas.property import LocationOut, PropertySummary
from app.services import sessions as session_svc
from app.services.orchestrator import handle_message
from app.services.properties import get_property, list_locations, list_properties, to_summary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit("api", "rate_limit_default_per_minute"))])


@router.get("/status", tags=["meta"], summary="Feature availability for the frontend")
async def status() -> dict:
    s = get_settings()
    return {
        "llm": "claude" if s.llm_enabled else "offline",
        "llm_model": s.claude_model if s.llm_enabled else None,
        "embeddings": s.resolved_embedding_provider,
        "places": get_places_provider().name,
        "quick_actions": QUICK_ACTION_QUERIES,
    }


# --------------------------------------------------------------------------- properties
@router.get("/locations", response_model=list[LocationOut], tags=["properties"])
async def get_locations(db: AsyncSession = Depends(get_db)):
    return await list_locations(db)


@router.get("/properties", response_model=list[PropertySummary], tags=["properties"])
async def get_properties(location: str | None = None, db: AsyncSession = Depends(get_db)):
    return [to_summary(p) for p in await list_properties(db, location)]


@router.get("/properties/{property_id}", response_model=PropertySummary, tags=["properties"])
async def get_property_public(property_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return to_summary(await get_property(db, property_id))


# --------------------------------------------------------------------------- chat sessions
@router.post("/chat/sessions", response_model=SessionOut, status_code=201, tags=["chat"])
async def create_session(body: SessionCreate | None = None, db: AsyncSession = Depends(get_db)):
    conv, token = await session_svc.create_session(db)
    if body and body.property_id:
        await session_svc.select_property(db, conv, body.property_id)
    elif body and body.location_slug:
        await session_svc.select_location(db, conv, body.location_slug)
    return await session_svc.session_view(db, conv, token)


@router.get("/chat/sessions/{session_id}", response_model=SessionOut, tags=["chat"])
async def get_session(session_id: uuid.UUID, token: str | None = Depends(session_token), db: AsyncSession = Depends(get_db)):
    conv = await session_svc.load_session(db, session_id, token)
    return await session_svc.session_view(db, conv)


@router.put("/chat/sessions/{session_id}/selection", response_model=SessionOut, tags=["chat"],
            summary="Select or change location / property")
async def update_selection(
    session_id: uuid.UUID, body: SessionSelect,
    token: str | None = Depends(session_token), db: AsyncSession = Depends(get_db),
):
    conv = await session_svc.load_session(db, session_id, token)
    if body.property_id:
        await session_svc.select_property(db, conv, body.property_id)
    elif body.location_slug:
        await session_svc.select_location(db, conv, body.location_slug)
    else:
        raise HTTPException(422, "Provide location_slug or property_id.")
    await db.refresh(conv)
    return await session_svc.session_view(db, conv)


@router.post("/chat/sessions/{session_id}/verify-pin", tags=["chat"],
             dependencies=[Depends(rate_limit("pin", "rate_limit_login_per_minute"))])
async def verify_pin(
    session_id: uuid.UUID, body: PinVerifyIn,
    token: str | None = Depends(session_token), db: AsyncSession = Depends(get_db),
):
    conv = await session_svc.load_session(db, session_id, token)
    if not await session_svc.verify_pin(db, conv, body.pin):
        raise HTTPException(403, "That PIN doesn't match. Please check your booking message.")
    return {"verified": True}


@router.get("/chat/sessions/{session_id}/messages", response_model=list[MessageOut], tags=["chat"])
async def get_messages(session_id: uuid.UUID, token: str | None = Depends(session_token), db: AsyncSession = Depends(get_db)):
    conv = await session_svc.load_session(db, session_id, token)
    rows = (
        await db.execute(
            select(Message).where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at)
        )
    ).scalars().all()
    return [
        MessageOut(
            id=m.id, role=m.role, content=m.content, intent=m.intent, created_at=m.created_at,
            places=[PlaceResult.model_validate(p) for p in (m.metadata_ or {}).get("places", [])],
            suggestions=(m.metadata_ or {}).get("suggestions", []),
        )
        for m in rows
    ]


@router.delete("/chat/sessions/{session_id}/messages", status_code=204, tags=["chat"],
               summary="Clear conversation history (keeps the selected property)")
async def clear_messages(session_id: uuid.UUID, token: str | None = Depends(session_token), db: AsyncSession = Depends(get_db)):
    conv = await session_svc.load_session(db, session_id, token)
    from sqlalchemy import delete

    await db.execute(delete(Message).where(Message.conversation_id == conv.id))
    prefs = await session_svc.get_preferences(db, conv)
    for f in session_svc.PREF_FIELDS:
        setattr(prefs, f, [] if f == "interests" else None)
    await db.commit()


@router.post(
    "/chat/message",
    tags=["chat"],
    summary="Send a guest message; response is a Server-Sent Events stream",
    dependencies=[Depends(rate_limit("chat", "rate_limit_chat_per_minute"))],
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat_message(body: ChatMessageIn, token: str | None = Depends(session_token)):
    # Validate the session before starting the stream so errors return proper HTTP codes.
    async with get_sessionmaker()() as db:
        await session_svc.load_session(db, body.session_id, token)

    async def event_stream():
        async with get_sessionmaker()() as db:
            try:
                conv = await session_svc.load_session(db, body.session_id, token)
                async for ev in handle_message(db, conv, body.message):
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], default=str, ensure_ascii=False)}\n\n"
            except Exception as exc:  # stream an understandable error instead of a broken connection
                log.exception("Chat pipeline failed: %s", type(exc).__name__)
                await db.rollback()
                msg = "Sorry, something went wrong on my side. Please try again in a moment."
                yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- places
@router.post("/places/search", response_model=PlacesSearchResponse, tags=["places"],
             dependencies=[Depends(rate_limit("places", "rate_limit_chat_per_minute"))])
async def places_search(body: PlacesSearchRequest, db: AsyncSession = Depends(get_db)):
    prop = await get_property(db, body.property_id)
    result = await discover(
        db, prop,
        DiscoveryRequest(
            query=body.query, included_type=body.included_type, radius_m=body.radius_m,
            open_now=body.open_now, max_price_level=body.max_price_level, min_rating=body.min_rating,
            limit=body.limit,
        ),
    )
    return PlacesSearchResponse(
        provider=result.provider, origin=result.origin, radius_m=result.radius_m,
        results=result.results, notice=result.notice,
    )


@router.get("/places/details/{place_id}", response_model=PlaceResult, tags=["places"])
async def places_details(place_id: str):
    if len(place_id) > 255:
        raise HTTPException(422, "Invalid place id")
    place = await place_details(place_id)
    if place is None:
        raise HTTPException(404, "Place details unavailable.")
    return place
