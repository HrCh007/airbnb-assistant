"""Query orchestration pipeline.

receive -> load context -> plan (intent) -> property RAG (if needed) ->
Places (if needed) -> combine -> generate (Claude, streamed) -> persist.

Yields events consumed by the SSE endpoint:
  session      updated session state (after a location switch)
  meta         {intent, planner, llm, places_provider}
  places       list of recommendation cards
  token        text delta
  suggestions  follow-up chips
  done         {message_id, latency_ms}
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import claude
from app.llm.compose import (
    NOT_FOUND,
    PIN_REQUIRED,
    build_user_turn,
    offline_answer,
    suggest_followups,
)
from app.llm.heuristic_planner import heuristic_plan
from app.llm.schemas import QueryPlan
from app.location.discovery import DiscoveryRequest, discover
from app.location.providers import get_places_provider
from app.models import Conversation, Message, Property
from app.rag.retriever import RetrievalResult, retrieve
from app.schemas.common import PlaceResult
from app.services import sessions as session_svc
from app.services.properties import find_location

log = logging.getLogger(__name__)

Event = dict


def _ev(event: str, data) -> Event:
    return {"event": event, "data": data}


async def _history(db: AsyncSession, conv: Conversation, property_id: uuid.UUID | None, limit: int) -> list[Message]:
    q = (
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    # Memory is scoped to the current property so one property's facts
    # (e.g. Wi-Fi password) never flow into another property's context.
    if property_id is not None:
        q = q.where(Message.property_id == property_id)
    return list(reversed((await db.execute(q)).scalars().all()))


def _llm_history(messages: list[Message]) -> list[dict]:
    """Alternating user/assistant turns, starting with user, for the Messages API."""
    out: list[dict] = []
    for m in messages:
        if not m.content.strip():
            continue
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] == "user":
        out.pop()  # the current message is appended separately
    return out


def _apply_preferences(prefs, plan: QueryPlan) -> None:
    upd = plan.preference_updates
    for field in ("budget", "dietary", "companions", "travel_mode", "max_distance_km"):
        val = getattr(upd, field)
        if val not in (None, ""):
            setattr(prefs, field, val)
    if upd.interests:
        prefs.interests = list(dict.fromkeys([*(prefs.interests or []), *[i.lower()[:40] for i in upd.interests]]))[:15]


async def _plan(message: str, history: list[Message], last_plan: dict | None, has_prev: bool, summary: str) -> QueryPlan:
    s = get_settings()
    fallback = heuristic_plan(message, last_plan, has_prev)
    if not s.llm_enabled:
        return fallback
    try:
        plan = await claude.plan_with_llm(
            [{"role": m.role, "content": m.content} for m in history], message, summary
        )
    except claude.LLMUnavailable:
        return fallback
    # Safety net: never downgrade a detected emergency.
    if fallback.intent == "EMERGENCY_QUERY" and plan.intent not in {"EMERGENCY_QUERY", "LOCAL_DISCOVERY_QUERY"}:
        return fallback
    if plan.places and plan.places.action == "filter_previous" and not has_prev:
        plan.places.action = "search"
    return plan


async def handle_message(db: AsyncSession, conv: Conversation, message: str) -> AsyncIterator[Event]:
    s = get_settings()
    t0 = time.perf_counter()
    message = message.strip()[: s.max_message_chars]
    prefs = await session_svc.get_preferences(db, conv)
    prop: Property | None = await db.get(Property, conv.property_id) if conv.property_id else None

    # ---- 1. Persist the guest message.
    user_msg = Message(conversation_id=conv.id, property_id=conv.property_id, role="user", content=message)
    db.add(user_msg)
    await db.flush()

    history = await _history(db, conv, conv.property_id, s.history_messages_for_llm + 1)
    # Most recent local-search context (survives intervening property questions).
    last_with_plan = next(
        (m for m in reversed(history) if m.role == "assistant" and m.metadata_.get("places_plan")), None
    )
    last_plan = last_with_plan.metadata_.get("places_plan") if last_with_plan else None
    last_with_places = next(
        (m for m in reversed(history) if m.role == "assistant" and m.metadata_.get("places")), None
    )
    prev_places = [PlaceResult.model_validate(p) for p in (last_with_places.metadata_["places"] if last_with_places else [])]

    summary = (
        f"Selected property: {prop.name} ({prop.location.name})" if prop else "No property selected yet."
    ) + f"\nGuest profile: {session_svc.preferences_dict(prefs) or 'unknown'}"
    if last_plan:
        summary += f"\nPrevious local search: {last_plan}"
    if prev_places:
        summary += "\nPlaces shown last turn: " + ", ".join(p.name for p in prev_places[:8])

    # ---- 2. Plan (intent classification + retrieval/search plan).
    plan = await _plan(message, history[:-1], last_plan, bool(prev_places), summary)
    _apply_preferences(prefs, plan)

    # ---- 3. Location/property switching.
    if plan.switch_location or prop is None:
        loc = await find_location(db, plan.switch_location or message)
        if loc is not None and (prop is None or loc.id != prop.location_id):
            _, props = await session_svc.select_location(db, conv, loc.slug)
            await db.refresh(conv)
            prop = await db.get(Property, conv.property_id) if conv.property_id else None
            user_msg.property_id = conv.property_id
            yield _ev("session", await session_svc.session_view(db, conv))
            if prop is None:
                text = (
                    f"Got it — {loc.name}! I have {len(props)} stays there. Which one are you in?"
                    if props else f"Sorry, there are no active stays in {loc.name} right now."
                )
            else:
                text = (
                    f"Done — I've switched to **{prop.name}** in {loc.name}. "
                    "Ask me anything about the stay or the neighbourhood!"
                )
            async for ev in _finish(db, conv, prop, plan, text, [], False, {}, t0, None):
                yield ev
            return
        if prop is None:
            text = "Before I can help, which neighbourhood are you staying in — Dwarka, Janakpuri or Saket?"
            async for ev in _finish(db, conv, None, plan, text, [], False, {}, t0, None):
                yield ev
            return

    yield _ev(
        "meta",
        {
            "intent": plan.intent,
            "planner": plan.planner,
            "llm": s.llm_enabled,
            "places_provider": get_places_provider().name,
        },
    )

    notices: list[str] = []
    trace: dict = {"planner": plan.planner}

    # ---- 4. Property knowledge retrieval (strictly this property).
    retrieval: RetrievalResult | None = None
    if plan.needs_property_rag:
        allow_sensitive = not prop.guest_pin_hash or conv.pin_verified_property_id == prop.id
        retrieval = await retrieve(
            db, prop.id, plan.property_question or message, allow_sensitive=allow_sensitive
        )
        if retrieval.withheld_sensitive:
            notices.append(
                "A sensitive detail (e.g. Wi-Fi password or access instructions) matched but was withheld: "
                "the guest has not verified their booking PIN yet."
            )
        trace["sources"] = retrieval.sources()

    # ---- 5. Local discovery (only when the plan needs it).
    places: list[PlaceResult] = []
    places_previous = False
    if plan.places is not None:
        pp = plan.places
        if pp.action == "filter_previous" and prev_places:
            places, places_previous = prev_places, True
        else:
            radius = pp.radius_m or (int(prefs.max_distance_km * 1000) if prefs.max_distance_km else None)
            result = await discover(
                db,
                prop,
                DiscoveryRequest(
                    query=pp.query,
                    included_type=pp.included_type,
                    radius_m=radius,
                    open_now=pp.open_now,
                    max_price_level=pp.max_price_level,
                    min_rating=pp.min_rating,
                    budget=prefs.budget,
                    category_key=pp.category_key,
                    rank_by_distance=pp.rank_by_distance,
                    travel_mode={"walking": "walking", "metro": "transit", "cab": "driving", "car": "driving"}.get(prefs.travel_mode or ""),
                ),
            )
            places = result.results
            if result.notice:
                notices.append(result.notice)
            trace["places_search"] = {"provider": result.provider, "radius_m": result.radius_m, "count": len(places), "error": result.error}
        if places:
            yield _ev("places", {"previous": places_previous, "items": [p.model_dump(mode="json") for p in places]})

    # ---- 6. Generate the answer.
    property_info = {"name": prop.name, "location_name": prop.location.name, "neighbourhood": prop.neighbourhood}
    prefs_dict = session_svc.preferences_dict(prefs)
    answer_parts: list[str] = []
    llm_used = False

    async def _offline(note: str | None = None) -> AsyncIterator[str]:
        text = offline_answer(
            message=message, plan=plan, property_name=prop.name, retrieval=retrieval,
            places=places, places_previous=places_previous, notices=notices,
        )
        if note:
            text = f"{text}\n\n_{note}_"
        yield text

    if plan.clarification_question and plan.places is None and plan.intent not in {"PROPERTY_QUERY", "EMERGENCY_QUERY"}:
        stream = _single(plan.clarification_question)
    elif s.llm_enabled:
        user_turn = build_user_turn(
            message=message, plan=plan, property_info=property_info, preferences=prefs_dict,
            retrieval=retrieval, places=places, places_previous=places_previous, notices=notices,
        )
        stream = claude.stream_answer(_llm_history(history), user_turn)
        llm_used = True
    else:
        stream = _offline()

    try:
        async for delta in stream:
            answer_parts.append(delta)
            yield _ev("token", delta)
    except claude.LLMUnavailable as exc:
        trace["llm_error"] = str(exc)
        llm_used = False
        note = "My AI service is briefly unavailable, so here's the information I found directly."
        async for delta in _offline(note):
            answer_parts.append(delta)
            yield _ev("token", delta)

    answer = "".join(answer_parts).strip() or NOT_FOUND
    trace["llm_used"] = llm_used
    trace["notices"] = notices
    # Carry the search context forward so "something cheaper" works later.
    new_plan = plan.places.model_dump() if plan.places and plan.places.action != "filter_previous" else last_plan
    async for ev in _finish(db, conv, prop, plan, answer, places, places_previous, trace, t0, new_plan, stream_text=False):
        yield ev


async def _single(text: str) -> AsyncIterator[str]:
    yield text


async def _finish(
    db: AsyncSession, conv: Conversation, prop: Property | None, plan: QueryPlan, text: str,
    places: list[PlaceResult], places_previous: bool, trace: dict, t0: float, places_plan: dict | None,
    stream_text: bool = True,
) -> AsyncIterator[Event]:
    if stream_text:
        yield _ev("token", text)
    prefs = await session_svc.get_preferences(db, conv)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        **trace,
        "places": [p.model_dump(mode="json") for p in places],
        "places_previous": places_previous,
        "places_plan": places_plan,
        "plan": plan.model_dump(exclude={"preference_updates"}),
        "latency_ms": latency_ms,
    }
    suggestions = suggest_followups(plan, session_svc.preferences_dict(prefs), bool(places)) if prop else []
    meta["suggestions"] = suggestions
    msg = Message(
        conversation_id=conv.id, property_id=conv.property_id, role="assistant",
        content=text, intent=plan.intent, metadata_=meta,
    )
    db.add(msg)
    await db.commit()
    if suggestions:
        yield _ev("suggestions", suggestions)
    yield _ev("done", {"message_id": str(msg.id), "latency_ms": latency_ms, "intent": plan.intent})


__all__ = ["handle_message", "PIN_REQUIRED"]
