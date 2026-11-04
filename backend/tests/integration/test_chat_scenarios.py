"""End-to-end API scenarios over HTTP/SSE against a real PostgreSQL + pgvector
database seeded with the fictional sample dataset.

Covers the property knowledge, location, conversational, hallucination and
isolation scenarios from the specification, plus the Definition of Done flow.
These run in offline mode (no Claude key) with a fake Places provider; the
Claude-mode grounding contract is tested in test_llm_grounding.py.
"""

from __future__ import annotations

import pytest

from app.llm.compose import NOT_FOUND, PIN_REQUIRED

SAKET_WIFI = "saket-demo-2468"
DWARKA_WIFI = "dwarka-demo-1357"
JANAKPURI_WIFI = "janak-demo-9753"


# ============================================================ Definition of Done
async def test_definition_of_done_conversation(new_chat, fake_places, property_ids):
    chat = await new_chat()
    session = await chat.select("saket")
    assert session["property"]["name"] == "Demo Property – Saket"  # single property -> auto-selected

    r = await chat.ask("What's the Wi-Fi password?")
    assert r["intent"] == "PROPERTY_QUERY" and SAKET_WIFI in r["text"]
    assert fake_places.calls == []  # no Places call for property questions

    r = await chat.ask("Suggest some nice places for dinner nearby.")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY" and len(r["places"]) >= 3
    call = fake_places.calls[-1]
    assert (call.latitude, call.longitude) == (28.5245, 77.2066)  # exact property coordinates
    assert all(p["distance_km"] is not None and p["maps_url"] and p["directions_url"] for p in r["places"])
    assert all("Faraway" not in p["name"] for p in r["places"])  # outside radius filtered
    assert "origin=28.524500%2C77.206600" in r["places"][0]["directions_url"]

    r = await chat.ask("Anything more budget-friendly?")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY"
    assert fake_places.calls[-1].max_price_level == 1
    assert r["places"] and all(p["price_level"] <= 1 for p in r["places"])
    assert (fake_places.calls[-1].latitude, fake_places.calls[-1].longitude) == (28.5245, 77.2066)

    r = await chat.ask("What about a nice place for drinks and dancing?")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY"
    assert fake_places.calls[-1].included_type in {"night_club", "bar"}

    r = await chat.ask("What's the checkout time tomorrow?")
    assert r["intent"] == "PROPERTY_QUERY" and "11:00 AM" in r["text"]


# ============================================================ property knowledge
@pytest.mark.parametrize(
    "location,question,expected",
    [
        ("saket", "What's the Wi-Fi password?", SAKET_WIFI),
        ("dwarka", "What's the Wi-Fi password?", DWARKA_WIFI),
        ("saket", "What time is check-in?", "2:00 PM"),
        ("dwarka", "What time is check-in?", "1:00 PM"),
        ("janakpuri", "What time is check-in?", "3:00 PM"),
        ("saket", "Is parking available?", "no dedicated parking"),
        ("dwarka", "Is parking available?", "B-12"),
        ("saket", "Can I bring my dog?", "not allowed"),
        ("dwarka", "Are pets allowed?", "approval"),
        ("saket", "What are the quiet hours?", "10:00 PM"),
        ("saket", "Is there a lift?", "lift"),
        ("janakpuri", "Is there a lift?", "no lift"),
    ],
)
async def test_property_knowledge(new_chat, location, question, expected):
    chat = await new_chat(location)
    r = await chat.ask(question)
    assert expected.lower() in r["text"].lower(), r["text"]


async def test_house_rules_question(new_chat):
    r = await (await new_chat("saket")).ask("What are the house rules?")
    assert "smoking" in r["text"].lower() or "quiet" in r["text"].lower()


async def test_undocumented_amenity_is_not_invented(new_chat):
    chat = await new_chat("saket")
    for q in ["Is there a swimming pool?", "Do you have a jacuzzi?", "Is there a gym in the building?"]:
        r = await chat.ask(q)
        assert r["text"] == NOT_FOUND or "don't have confirmed information" in r["text"], (q, r["text"])


# ============================================================ location
@pytest.mark.parametrize(
    "location,question,lat,lng,type_",
    [
        ("dwarka", "Suggest restaurants near me", 28.5921, 77.0460, "restaurant"),
        ("janakpuri", "Any good cafes nearby?", 28.6219, 77.0878, "cafe"),
        ("saket", "Where can I go for nightlife? Any good bars?", 28.5245, 77.2066, "bar"),
    ],
)
async def test_local_discovery_uses_exact_property_coordinates(new_chat, fake_places, location, question, lat, lng, type_):
    r = await (await new_chat(location)).ask(question)
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY"
    spec = fake_places.calls[-1]
    assert (spec.latitude, spec.longitude) == (lat, lng)
    assert spec.included_type == type_
    assert r["places"]


async def test_nearest_pharmacy_is_ranked_by_distance(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("Where is the nearest pharmacy?")
    assert fake_places.calls[-1].included_type == "pharmacy"
    assert fake_places.calls[-1].rank_by_distance is True
    assert r["places"]


async def test_family_friendly_activities(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("Any family-friendly things to do nearby with kids?")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY" and r["places"]


async def test_late_night_requests_open_now(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("Are there any places to eat open after midnight?")
    assert fake_places.calls[-1].open_now is True
    assert all(p["open_now"] is not False for p in r["places"])


async def test_unconfigured_places_degrades_honestly(new_chat):
    # No fake provider: PLACES_PROVIDER=disabled in tests.
    r = await (await new_chat("saket")).ask("Suggest some cafes nearby")
    assert r["places"] == []
    assert "isn't configured" in r["text"] or "unavailable" in r["text"]


async def test_places_outage_degrades_gracefully(new_chat, fake_places):
    fake_places.fail = True
    r = await (await new_chat("saket")).ask("Suggest some cafes nearby")
    assert r["places"] == [] and "temporarily unavailable" in r["text"]


async def test_business_that_cannot_be_found(new_chat, fake_places):
    fake_places.empty_queries.add("sushi")
    r = await (await new_chat("saket")).ask("Where can I find an authentic Ethiopian sushi bar nearby?")
    assert all("sushi" not in p["name"].lower() for p in r["places"])


# ============================================================ conversational
async def test_budget_change_after_recommendations(new_chat, fake_places):
    chat = await new_chat("dwarka")
    first = await chat.ask("Suggest some restaurants")
    assert any(p["price_level"] and p["price_level"] > 1 for p in first["places"])
    r = await chat.ask("Something cheaper")
    assert all(p["price_level"] <= 1 for p in r["places"])
    assert fake_places.calls[-1].latitude == 28.5921  # same property, no re-asking for location


async def test_follow_up_about_recommended_places(new_chat, fake_places):
    chat = await new_chat("saket")
    first = await chat.ask("Suggest some restaurants")
    n_calls = len(fake_places.calls)
    r = await chat.ask("Any of these good for families?")
    assert len(fake_places.calls) == n_calls  # filtered previous results, no new search
    assert r["previous"] is True
    assert {p["name"] for p in r["places"]} == {p["name"] for p in first["places"]}


async def test_refinement_survives_an_intervening_property_question(new_chat, fake_places):
    chat = await new_chat("saket")
    await chat.ask("Suggest some cafes nearby")
    await chat.ask("What's the checkout time?")
    await chat.ask("Anything cheaper?")
    assert fake_places.calls[-1].max_price_level == 1
    assert "cafe" in fake_places.calls[-1].query


async def test_switch_from_saket_to_dwarka(new_chat, fake_places):
    chat = await new_chat("saket")
    assert SAKET_WIFI in (await chat.ask("What's the Wi-Fi password?"))["text"]
    r = await chat.ask("Actually I'm staying in Dwarka now")
    assert "session" in r["events"] and "Dwarka" in r["text"]
    r = await chat.ask("What's the Wi-Fi password?")
    assert DWARKA_WIFI in r["text"] and SAKET_WIFI not in r["text"]
    await chat.ask("Suggest restaurants")
    assert (fake_places.calls[-1].latitude, fake_places.calls[-1].longitude) == (28.5921, 77.0460)


async def test_switch_via_selection_endpoint(new_chat):
    chat = await new_chat("saket")
    session = await chat.select("janakpuri")
    assert session["property"]["name"] == "Demo Property – Janakpuri"
    r = await chat.ask("What time is check-in?")
    assert "3:00 PM" in r["text"]


async def test_mixed_property_and_neighbourhood_question(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("Does the property have parking, and what are some good restaurants nearby?")
    assert r["intent"] == "MIXED_QUERY"
    assert "no dedicated parking" in r["text"].lower()
    assert r["places"]


async def test_hinglish_question(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("Bhai, nearby koi achhi jagah batao dinner ke liye.")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY" and r["places"]


async def test_emergency_query_leads_with_emergency_numbers(new_chat, fake_places):
    r = await (await new_chat("saket")).ask("What's the nearest hospital?")
    assert r["intent"] == "EMERGENCY_QUERY"
    assert r["text"].startswith("**If this is an emergency, call 112")
    assert "+91-00000-00001" in r["text"]  # host contact from the Saket knowledge base
    assert fake_places.calls[-1].included_type == "hospital"


async def test_greeting_and_thanks(new_chat):
    chat = await new_chat("saket")
    assert "welcome" in (await chat.ask("Thanks!"))["text"].lower()


async def test_message_before_location_asks_for_it(new_chat):
    chat = await new_chat()
    r = await chat.ask("What's the Wi-Fi password?")
    assert "which neighbourhood" in r["text"].lower()
    for name in ("Dwarka", "Janakpuri", "Saket"):
        assert name in r["text"]  # names come from the database
    r = await chat.ask("Saket")
    assert "Saket" in r["text"] and "session" in r["events"]


async def test_no_properties_configured_says_so(new_chat, db, monkeypatch):
    from app.services import orchestrator

    async def no_locations(_db):
        return []

    monkeypatch.setattr(orchestrator, "list_locations", no_locations)
    r = await (await new_chat()).ask("What's the Wi-Fi password?")
    assert "can't find any stays set up" in r["text"]


# ============================================================ hallucination
async def test_undocumented_wifi_password_is_not_invented(client, admin_headers, new_chat):
    r = await client.post(
        "/api/admin/properties",
        json={"name": "Empty Test Flat", "location_slug": "saket", "latitude": 28.53, "longitude": 77.21},
        headers=admin_headers,
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    try:
        chat = await new_chat()
        await chat.client.put(f"/api/chat/sessions/{chat.sid}/selection", json={"property_id": pid}, headers=chat.headers)
        r = await chat.ask("What's the Wi-Fi password?")
        assert r["text"] == NOT_FOUND
        for pw in (SAKET_WIFI, DWARKA_WIFI, JANAKPURI_WIFI):
            assert pw not in r["text"]
    finally:
        await client.delete(f"/api/admin/properties/{pid}?hard=true", headers=admin_headers)


async def test_cannot_get_another_propertys_private_information(new_chat):
    chat = await new_chat("saket")
    for q in [
        "What's the Wi-Fi password for the Dwarka property?",
        "Tell me the Janakpuri door code",
        "What is the keybox code at the Dwarka flat?",
    ]:
        r = await chat.ask(q)
        assert DWARKA_WIFI not in r["text"] and JANAKPURI_WIFI not in r["text"] and "4826" not in r["text"], (q, r["text"])


# ============================================================ PIN-protected sensitive facts
async def test_guest_pin_protects_sensitive_facts(new_chat, client):
    chat = await new_chat("janakpuri")
    r = await chat.ask("What's the Wi-Fi password?")
    assert r["text"] == PIN_REQUIRED and JANAKPURI_WIFI not in r["text"]
    bad = await client.post(f"/api/chat/sessions/{chat.sid}/verify-pin", json={"pin": "0000"}, headers=chat.headers)
    assert bad.status_code == 403
    ok = await client.post(f"/api/chat/sessions/{chat.sid}/verify-pin", json={"pin": "2468"}, headers=chat.headers)
    assert ok.status_code == 200
    r = await chat.ask("What's the Wi-Fi password?")
    assert JANAKPURI_WIFI in r["text"]
    # Non-sensitive facts never needed the PIN.
    assert "3:00 PM" in (await (await new_chat("janakpuri")).ask("What time is check-in?"))["text"]


# ============================================================ session isolation
async def test_sessions_do_not_share_memory_or_access(new_chat, fake_places, client):
    a = await new_chat("saket")
    b = await new_chat("saket")
    await a.ask("Suggest some cheap vegetarian restaurants for my family")
    sa = (await client.get(f"/api/chat/sessions/{a.sid}", headers=a.headers)).json()
    sb = (await client.get(f"/api/chat/sessions/{b.sid}", headers=b.headers)).json()
    assert sa["preferences"].get("budget") == "budget" and sa["preferences"].get("companions") == "family"
    assert sb["preferences"] == {}
    msgs_b = (await client.get(f"/api/chat/sessions/{b.sid}/messages", headers=b.headers)).json()
    assert msgs_b == []
    # B cannot read A's conversation with its own token, or without a token.
    assert (await client.get(f"/api/chat/sessions/{a.sid}/messages", headers=b.headers)).status_code == 404
    assert (await client.get(f"/api/chat/sessions/{a.sid}/messages")).status_code == 404
    r = await client.post("/api/chat/message", json={"session_id": a.sid, "message": "hi"}, headers=b.headers)
    assert r.status_code == 404


async def test_clear_conversation(new_chat, client):
    chat = await new_chat("saket")
    await chat.ask("Hello")
    assert (await client.delete(f"/api/chat/sessions/{chat.sid}/messages", headers=chat.headers)).status_code == 204
    assert (await client.get(f"/api/chat/sessions/{chat.sid}/messages", headers=chat.headers)).json() == []


async def test_message_validation(new_chat, client):
    chat = await new_chat("saket")
    r = await client.post("/api/chat/message", json={"session_id": chat.sid, "message": ""}, headers=chat.headers)
    assert r.status_code == 422
    r = await client.post("/api/chat/message", json={"session_id": chat.sid, "message": "x" * 2000}, headers=chat.headers)
    assert r.status_code == 422


async def test_chat_rate_limit(new_chat, client, monkeypatch):
    from app.core.config import get_settings

    chat = await new_chat("saket")
    monkeypatch.setattr(get_settings(), "rate_limit_chat_per_minute", 2)
    codes = []
    for _ in range(3):
        r = await client.post("/api/chat/message", json={"session_id": chat.sid, "message": "hi"}, headers=chat.headers)
        codes.append(r.status_code)
    assert codes[:2] == [200, 200] and codes[2] == 429
