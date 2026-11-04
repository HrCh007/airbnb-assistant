"""Query orchestration pipeline.

receive -> load context -> route (deterministic, LLM only when unsure) ->
property RAG (if needed) -> local discovery (if needed) -> answer
(deterministic where possible, otherwise local LLM, streamed + verified) -> persist.

SSE events:
  session      updated session state (after a location switch)
  status       short progress text while searching / generating
  meta         {intent, planner, llm, discovery}
  places       recommendation cards
  token        text delta
  replace      full replacement text (a streamed answer failed fact verification)
  suggestions  follow-up chips
  done         {message_id, latency_ms, intent}
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import ollama
from app.llm.compose import (
    EMERGENCY_LINES,
    NOT_FOUND,
    PIN_REQUIRED,
    build_user_turn,
    offline_answer,
    place_line,
    suggest_followups,
    topic_covered,
    unknown_place_names,
    unsupported_facts,
)
from app.llm.heuristic_planner import heuristic_plan
from app.llm.schemas import PlacesPlan, QueryPlan
from app.location.categories import match_category
from app.location.discovery import DiscoveryRequest, discover
from app.models import Conversation, Message, Property
from app.rag.retriever import RetrievalResult, retrieve
from app.schemas.common import PlaceResult
from app.services import sessions as session_svc
from app.services.properties import find_location, list_locations

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
    # (e.g. its Wi-Fi password) never flow into another property's context.
    if property_id is not None:
        q = q.where(Message.property_id == property_id)
    return list(reversed((await db.execute(q)).scalars().all()))


def _llm_history(messages: list[Message]) -> list[dict]:
    """Alternating user/assistant turns, starting with user, excluding the current message."""
    out: list[dict] = []
    for m in messages:
        if not m.content.strip():
            continue
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content[:1500]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] == "user":
        out.pop()
    return out


def _apply_preferences(prefs, plan: QueryPlan) -> None:
    upd = plan.preference_updates
    for field in ("budget", "dietary", "companions", "travel_mode", "max_distance_km"):
        val = getattr(upd, field)
        if val not in (None, ""):
            setattr(prefs, field, val)
    if upd.interests:
        prefs.interests = list(dict.fromkeys([*(prefs.interests or []), *[i.lower()[:40] for i in upd.interests]]))[:15]


async def _route(message: str, last_plan: dict | None, has_prev: bool, summary: str) -> QueryPlan:
    """Deterministic routing; the local LLM is consulted only for messages the
    rules couldn't place (saves a slow CPU inference on most messages)."""
    s = get_settings()
    plan = heuristic_plan(message, last_plan, has_prev)
    if plan.confident or not (s.llm_enabled and s.llm_routing):
        return plan
    st = await ollama.status()
    if not st["model_ready"]:
        return plan
    try:
        data = await ollama.classify(message, summary)
    except ollama.LLMUnavailable:
        return plan
    intent = data["intent"]
    routed = plan.model_copy(update={"intent": intent, "planner": "llm"})
    if intent in {"LOCAL_DISCOVERY_QUERY", "MIXED_QUERY"}:
        q = (data.get("search_query") or message).strip()[:80]
        cat = match_category(q) or match_category(message)
        routed.places = PlacesPlan(
            action="search", query=cat.label if cat else q, category_key=cat.key if cat else None,
            keyword=None if cat else q, rank_by_distance=bool(cat and cat.rank_by_distance),
        )
    elif intent == "EMERGENCY_QUERY":
        routed.places = PlacesPlan(action="search", query="hospital", category_key="hospital", rank_by_distance=True)
        routed.property_question = "emergency contacts host contact nearby hospital emergency numbers"
    if intent in {"PROPERTY_QUERY", "MIXED_QUERY", "GENERAL_NEIGHBOURHOOD_QUERY"} and not routed.property_question:
        routed.property_question = message
    return routed


async def handle_message(db: AsyncSession, conv: Conversation, message: str) -> AsyncIterator[Event]:
    s = get_settings()
    t0 = time.perf_counter()
    message = message.strip()[: s.max_message_chars]
    prefs = await session_svc.get_preferences(db, conv)
    prop: Property | None = await db.get(Property, conv.property_id) if conv.property_id else None

    user_msg = Message(conversation_id=conv.id, property_id=conv.property_id, role="user", content=message)
    db.add(user_msg)
    await db.flush()

    history = await _history(db, conv, conv.property_id, s.history_messages_for_llm + 1)
    last_with_plan = next((m for m in reversed(history) if m.role == "assistant" and m.metadata_.get("places_plan")), None)
    last_plan = last_with_plan.metadata_.get("places_plan") if last_with_plan else None
    last_with_places = next((m for m in reversed(history) if m.role == "assistant" and m.metadata_.get("places")), None)
    prev_places = [PlaceResult.model_validate(p) for p in (last_with_places.metadata_["places"] if last_with_places else [])]

    summary = (f"Property: {prop.name} ({prop.location.name})" if prop else "No property selected.") + (
        f" Last search: {last_plan.get('query')}" if last_plan else ""
    )
    plan = await _route(message, last_plan, bool(prev_places), summary)
    _apply_preferences(prefs, plan)

    # ---- location / property switching
    if plan.switch_location or prop is None:
        loc = await find_location(db, plan.switch_location or message)
        if loc is not None and (prop is None or loc.id != prop.location_id):
            _, props = await session_svc.select_location(db, conv, loc.slug)
            await db.refresh(conv)
            prop = await db.get(Property, conv.property_id) if conv.property_id else None
            user_msg.property_id = conv.property_id
            yield _ev("session", await session_svc.session_view(db, conv))
            if prop is None:
                text = (f"Got it — {loc.name}! I have {len(props)} stays there. Which one are you in?"
                        if props else f"Sorry, there are no active stays in {loc.name} right now.")
            else:
                text = f"Done — I've switched to **{prop.name}** in {loc.name}. Ask me anything about the stay or the neighbourhood!"
            async for ev in _finish(db, conv, prop, plan, text, [], False, {}, t0, None):
                yield ev
            return
        if prop is None:
            names = [lc.name for lc in await list_locations(db) if lc.property_count]
            if not names:
                text = ("I can't find any stays set up yet, so I can't help with a property right now. "
                        "If you're the host, add a property in the admin console.")
            else:
                options = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
                text = f"Before I can help, which neighbourhood are you staying in — {options}? Tap one of the cards above or just type the name."
            async for ev in _finish(db, conv, None, plan, text, [], False, {}, t0, None):
                yield ev
            return

    llm = await ollama.status() if s.llm_enabled else {"model_ready": False}
    notices: list[str] = []
    trace: dict = {"planner": plan.planner}

    # ---- property RAG (strictly this property)
    retrieval: RetrievalResult | None = None
    if plan.needs_property_rag:
        allow_sensitive = not prop.guest_pin_hash or conv.pin_verified_property_id == prop.id
        retrieval = await retrieve(db, prop.id, plan.property_question or message, allow_sensitive=allow_sensitive)
        if retrieval.withheld_sensitive:
            notices.append("A sensitive detail (Wi-Fi password or access instructions) was withheld: the guest hasn't verified their booking PIN yet.")
        trace["sources"] = retrieval.sources()

    # ---- local discovery
    places: list[PlaceResult] = []
    places_previous = False
    discovery_provider = None
    if plan.places is not None:
        pp = plan.places
        if pp.action == "filter_previous" and prev_places:
            places, places_previous = prev_places, True
        else:
            from app.location.categories import category_by_key

            cat = category_by_key(pp.category_key)
            what = pp.keyword or (cat.label if cat else pp.query)
            yield _ev("status", f"Searching OpenStreetMap for {what} near your stay…")
            radius = pp.radius_m or (int(prefs.max_distance_km * 1000) if prefs.max_distance_km else None)
            result = await discover(db, prop, DiscoveryRequest(
                query=pp.query, category_key=pp.category_key, keyword=pp.keyword, radius_m=radius,
                open_now=pp.open_now, late_night=pp.late_night, budget=pp.budget or prefs.budget,
                dietary=prefs.dietary, companions=prefs.companions, rank_by_distance=pp.rank_by_distance,
                travel_mode={"walking": "walking", "metro": "transit", "cab": "driving", "car": "driving"}.get(prefs.travel_mode or ""),
            ))
            places = result.results
            discovery_provider = result.provider
            if result.notice:
                notices.append(result.notice)
            trace["discovery"] = {"provider": result.provider, "radius_m": result.radius_m, "count": len(places), "error": result.error}

    yield _ev("meta", {
        "intent": plan.intent, "planner": plan.planner,
        "llm": bool(llm.get("model_ready")), "discovery": discovery_provider,
    })
    if places:
        yield _ev("places", {"previous": places_previous, "items": [p.model_dump(mode="json") for p in places]})

    # ---- answer
    offline_text = offline_answer(
        message=message, plan=plan, property_name=prop.name, retrieval=retrieval,
        places=places, places_previous=places_previous, notices=notices,
    )
    property_only = plan.intent == "PROPERTY_QUERY"
    q = plan.property_question or message
    deterministic = (
        plan.intent == "GENERAL_CONVERSATION"
        or (property_only and (retrieval is None or not retrieval.chunks or retrieval.withheld_sensitive
                               or not topic_covered(q, retrieval)))
        or (plan.places is not None and not places and plan.intent == "LOCAL_DISCOVERY_QUERY")
    )
    use_llm = bool(llm.get("model_ready")) and not deterministic
    answer_parts: list[str] = []
    llm_used = False
    replaced = False

    if use_llm:
        yield _ev("status", "Writing your answer…")
        user_turn = build_user_turn(
            message=message, plan=plan,
            property_info={"name": prop.name, "location_name": prop.location.name, "neighbourhood": prop.neighbourhood},
            preferences=session_svc.preferences_dict(prefs), retrieval=retrieval, places=places,
            places_previous=places_previous, notices=notices,
        )
        try:
            async for delta in ollama.stream_chat(_llm_history(history), user_turn):
                answer_parts.append(delta)
                yield _ev("token", delta)
            llm_used = True
        except ollama.LLMUnavailable as exc:
            trace["llm_error"] = str(exc)
            answer_parts = []

    if llm_used and s.verify_llm_answers:
        answer = "".join(answer_parts)
        context = "\n".join([c.content for c in (retrieval.chunks if retrieval else [])] + [place_line(p) for p in places] + notices + [EMERGENCY_LINES])
        bad_facts = unsupported_facts(answer, context)
        bad_names = unknown_place_names(answer, places) if places else []
        if bad_facts or bad_names or not answer.strip():
            trace["verification_failed"] = {"facts": bad_facts[:10], "places": bad_names[:10]}
            log.info("LLM answer failed verification (%d facts, %d places); using extractive answer", len(bad_facts), len(bad_names))
            answer_parts = [offline_text]
            replaced = True
            yield _ev("replace", offline_text)

    if not llm_used:
        text = offline_text
        if use_llm and trace.get("llm_error"):
            text += "\n\n_(The local AI model is busy or unavailable, so here's what I found directly.)_"
        answer_parts = [text]
        yield _ev("token", text)

    answer = "".join(answer_parts).strip() or NOT_FOUND
    trace.update(llm_used=llm_used and not replaced, notices=notices)
    new_plan = plan.places.model_dump() if plan.places and plan.places.action != "filter_previous" else last_plan
    async for ev in _finish(db, conv, prop, plan, answer, places, places_previous, trace, t0, new_plan, stream_text=False):
        yield ev


async def _finish(
    db: AsyncSession, conv: Conversation, prop: Property | None, plan: QueryPlan, text: str,
    places: list[PlaceResult], places_previous: bool, trace: dict, t0: float, places_plan: dict | None,
    stream_text: bool = True,
) -> AsyncIterator[Event]:
    if stream_text:
        yield _ev("token", text)
    prefs = await session_svc.get_preferences(db, conv)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    suggestions = suggest_followups(plan, session_svc.preferences_dict(prefs), bool(places)) if prop else []
    meta = {
        **trace,
        "places": [p.model_dump(mode="json") for p in places],
        "places_previous": places_previous,
        "places_plan": places_plan,
        "plan": plan.model_dump(exclude={"preference_updates"}),
        "latency_ms": latency_ms,
        "suggestions": suggestions,
    }
    msg = Message(conversation_id=conv.id, property_id=conv.property_id, role="assistant", content=text, intent=plan.intent, metadata_=meta)
    db.add(msg)
    await db.commit()
    if suggestions:
        yield _ev("suggestions", suggestions)
    yield _ev("done", {"message_id": str(msg.id), "latency_ms": latency_ms, "intent": plan.intent})


__all__ = ["handle_message", "PIN_REQUIRED"]
