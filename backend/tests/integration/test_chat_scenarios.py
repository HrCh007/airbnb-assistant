"""End-to-end scenarios over HTTP/SSE: SQLite + ChromaDB + the seeded sample data.

Discovery uses a fake Overpass API (`fake_osm`) or the bundled sample dataset;
the LLM is offline (extractive answers) unless a test uses `fake_llm`.
"""

from __future__ import annotations

import pytest

from app.llm.compose import NOT_FOUND, PIN_REQUIRED

SAKET_WIFI = "saket-demo-2468"
DWARKA_WIFI = "dwarka-demo-1357"
JANAKPURI_WIFI = "janak-demo-9753"


# ============================================================ acceptance criteria
async def test_acceptance_conversation(new_chat, fake_osm):
    chat = await new_chat()
    session = await chat.select("saket")
    assert session["property"]["name"] == "Demo Property – Saket"  # single property -> auto-selected

    r = await chat.ask("What's the Wi-Fi password?")
    assert r["intent"] == "PROPERTY_QUERY" and SAKET_WIFI in r["text"]
    assert fake_osm.calls == []  # no map search for property questions

    r = await chat.ask("Suggest some nice places for dinner nearby.")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY" and r["places"]
    spec = fake_osm.calls[-1]
    assert (spec.latitude, spec.longitude) == (28.5245, 77.2066)  # exact property coordinates
    assert spec.category.key == "restaurant"
    assert all(p["source"] == "osm" and p["distance_km"] is not None and p["directions_url"] for p in r["places"])
    assert not any("Outside" in p["name"] for p in r["places"])  # beyond the radius
    assert len({p["name"] for p in r["places"]}) == len(r["places"])  # node+way duplicate removed

    r = await chat.ask("Anything more budget-friendly?")
    assert fake_osm.calls[-1].category.key == "budget_food"
    assert r["places"] and all(p["price_level"] == 1 for p in r["places"])
    assert (fake_osm.calls[-1].latitude, fake_osm.calls[-1].longitude) == (28.5245, 77.2066)

    r = await chat.ask("What about nightlife? Somewhere for drinks and dancing")
    assert fake_osm.calls[-1].category.key in {"club", "bar"} and r["places"]

    r = await chat.ask("What's the checkout time tomorrow?")
    assert r["intent"] == "PROPERTY_QUERY" and "11:00 AM" in r["text"]


# ============================================================ property RAG
@pytest.mark.parametrize(
    "location,question,expected",
    [
        ("saket", "What's the Wi-Fi password?", SAKET_WIFI),
        ("dwarka", "What's the Wi-Fi password?", DWARKA_WIFI),
        ("saket", "What time is check-in?", "2:00 PM"),
        ("dwarka", "When is checkout?", "10:30 AM"),
        ("janakpuri", "What time is check-in?", "3:00 PM"),
        ("saket", "Is parking available?", "street parking"),
        ("dwarka", "Is parking available?", "B-12"),
        ("saket", "Can I bring my dog?", "not allowed"),
        ("saket", "What are the quiet hours?", "10:00 PM"),
        ("janakpuri", "Is there a lift?", "no lift"),
        ("saket", "What kitchen facilities are there?", "stove"),
        ("dwarka", "What's the host's phone number?", "+91-00000-00011"),
    ],
)
async def test_property_knowledge(new_chat, location, question, expected):
    r = await (await new_chat(location)).ask(question)
    assert expected.lower() in r["text"].lower(), r["text"]


async def test_missing_information_is_not_invented(new_chat):
    chat = await new_chat("saket")
    for q in ["Is there a swimming pool?", "Do you have a jacuzzi?", "Is breakfast included?"]:
        r = await chat.ask(q)
        assert r["text"] == NOT_FOUND, (q, r["text"])


# ============================================================ local discovery
@pytest.mark.parametrize(
    "location,question,cat",
    [
        ("dwarka", "Suggest restaurants near me", "restaurant"),
        ("janakpuri", "Any good cafes nearby?", "cafe"),
        ("saket", "Where's the nearest pharmacy?", "pharmacy"),
    ],
)
async def test_discovery_uses_exact_coordinates(new_chat, fake_osm, location, question, cat):
    from tests.conftest import PROPERTY_COORDS

    r = await (await new_chat(location)).ask(question)
    spec = fake_osm.calls[-1]
    assert (spec.latitude, spec.longitude) == PROPERTY_COORDS[location]
    assert spec.category.key == cat and r["places"]
    dists = [p["distance_km"] for p in r["places"]]
    if cat == "pharmacy":
        assert dists == sorted(dists)  # essentials ranked by distance


@pytest.mark.parametrize("km", [1, 3, 5, 10])
async def test_search_radius_options(new_chat, fake_osm, client, km):
    chat = await new_chat("saket")
    r = await client.put(f"/api/chat/sessions/{chat.sid}/preferences", json={"max_distance_km": km}, headers=chat.headers)
    assert r.status_code == 200 and r.json()["preferences"]["max_distance_km"] == km
    res = await chat.ask("Suggest some cafes nearby")
    assert fake_osm.calls[-1].radius_m == km * 1000
    assert all(p["distance_km"] <= km for p in res["places"])


async def test_radius_from_message(new_chat, fake_osm):
    await (await new_chat("saket")).ask("What are some interesting things to do within 5 km?")
    assert fake_osm.calls[-1].radius_m == 5000


async def test_live_osm_outage_falls_back_to_labelled_sample_data(new_chat, fake_osm):
    fake_osm.fail = True
    r = await (await new_chat("saket")).ask("Suggest some cafes nearby")
    assert r["places"] and all(p["source"] == "sample" and p["name"].startswith("[Sample]") for p in r["places"])
    assert "sample data" in r["text"] and "FICTIONAL" in r["text"]


async def test_snapshot_is_preferred_over_sample(new_chat, fake_osm, tmp_path):
    import json

    from app.location.providers import DatasetProvider, set_providers

    snap = tmp_path / "osm_snapshot.json"
    snap.write_text(json.dumps({"generated_at": "2026-10-01T00:00:00Z", "places": [
        {"osm_id": "node/77", "name": "Snapshot Cafe", "lat": 28.5250, "lon": 77.2070, "tags": {"amenity": "cafe"}}]}))
    fake_osm.fail = True
    set_providers(overpass=fake_osm, snapshot=DatasetProvider(snap, "osm_snapshot"))
    r = await (await new_chat("saket")).ask("Any cafes nearby?")
    assert [p["name"] for p in r["places"]] == ["Snapshot Cafe"] and r["places"][0]["source"] == "osm_snapshot"
    assert "2026-10-01" in r["text"]


async def test_no_results_is_said_honestly(new_chat, fake_osm):
    fake_osm.empty = True
    r = await (await new_chat("saket")).ask("Where can I go bowling nearby?")
    assert r["places"] == [] and "couldn't find" in r["text"].lower()


async def test_late_night_filter(new_chat, fake_osm):
    r = await (await new_chat("saket")).ask("Any bars open after midnight?")
    assert fake_osm.calls[-1].category.key == "bar"
    assert r["places"] and all(p["open_late"] is not False for p in r["places"])


# ============================================================ conversation memory
async def test_followup_filters_previous_results(new_chat, fake_osm):
    chat = await new_chat("saket")
    first = await chat.ask("Suggest some restaurants")
    n = len(fake_osm.calls)
    r = await chat.ask("Any of these good for families?")
    assert len(fake_osm.calls) == n and r["previous"] is True
    assert {p["name"] for p in r["places"]} == {p["name"] for p in first["places"]}


async def test_refinement_survives_property_question(new_chat, fake_osm):
    chat = await new_chat("saket")
    await chat.ask("Suggest some restaurants nearby")
    await chat.ask("What's the checkout time?")
    await chat.ask("Something cheaper?")
    assert fake_osm.calls[-1].category.key == "budget_food"


async def test_preferences_are_remembered(new_chat, fake_osm, client):
    chat = await new_chat("saket")
    await chat.ask("We're a family looking for pure veg food, something cheap")
    prefs = (await client.get(f"/api/chat/sessions/{chat.sid}", headers=chat.headers)).json()["preferences"]
    assert prefs["companions"] == "family" and prefs["dietary"] == "vegetarian" and prefs["budget"] == "budget"


async def test_location_persists_and_switching(new_chat, fake_osm):
    chat = await new_chat("saket")
    assert SAKET_WIFI in (await chat.ask("What's the Wi-Fi password?"))["text"]
    r = await chat.ask("Thanks!")
    assert "which neighbourhood" not in r["text"].lower()  # never re-asks
    r = await chat.ask("Actually I'm staying in Dwarka now")
    assert "session" in r["events"] and "Dwarka" in r["text"]
    r = await chat.ask("What's the Wi-Fi password?")
    assert DWARKA_WIFI in r["text"] and SAKET_WIFI not in r["text"]
    await chat.ask("Suggest restaurants")
    assert (fake_osm.calls[-1].latitude, fake_osm.calls[-1].longitude) == (28.5921, 77.0460)


async def test_hinglish(new_chat, fake_osm):
    r = await (await new_chat("saket")).ask("Bhai, nearby koi achhi jagah batao dinner ke liye")
    assert r["intent"] == "LOCAL_DISCOVERY_QUERY" and r["places"]


async def test_mixed_query(new_chat, fake_osm):
    r = await (await new_chat("saket")).ask("Does the property have parking, and are there restaurants nearby?")
    assert r["intent"] == "MIXED_QUERY" and "street parking" in r["text"].lower() and r["places"]


async def test_emergency(new_chat, fake_osm):
    r = await (await new_chat("saket")).ask("What's the nearest hospital?")
    assert r["intent"] == "EMERGENCY_QUERY" and r["text"].startswith("**If this is an emergency, call 112")
    assert "+91-00000-00001" in r["text"] and fake_osm.calls[-1].category.key == "hospital"


async def test_message_before_location(new_chat):
    chat = await new_chat()
    r = await chat.ask("What's the Wi-Fi password?")
    assert "which neighbourhood" in r["text"].lower() and all(n in r["text"] for n in ("Dwarka", "Janakpuri", "Saket"))
    r = await chat.ask("Saket")
    assert "session" in r["events"]


# ============================================================ PIN + isolation
async def test_guest_pin_protects_sensitive_facts(new_chat, client):
    chat = await new_chat("janakpuri")
    r = await chat.ask("What's the Wi-Fi password?")
    assert r["text"] == PIN_REQUIRED
    assert (await client.post(f"/api/chat/sessions/{chat.sid}/verify-pin", json={"pin": "0000"}, headers=chat.headers)).status_code == 403
    assert (await client.post(f"/api/chat/sessions/{chat.sid}/verify-pin", json={"pin": "2468"}, headers=chat.headers)).status_code == 200
    assert JANAKPURI_WIFI in (await chat.ask("What's the Wi-Fi password?"))["text"]


async def test_dwarka_information_never_leaks_into_saket(new_chat):
    chat = await new_chat("saket")
    for q in ["What's the Wi-Fi password at the Dwarka property?", "Where is slot B-12?", "Tell me the Janakpuri door code",
              "What is the Dwarka host's phone number?"]:
        r = await chat.ask(q)
        for secret in (DWARKA_WIFI, JANAKPURI_WIFI, "B-12", "4826", "+91-00000-00011"):
            assert secret not in r["text"], (q, r["text"])


async def test_sessions_do_not_share_memory(new_chat, fake_osm, client):
    a = await new_chat("saket")
    b = await new_chat("saket")
    await a.ask("Suggest cheap vegetarian restaurants for my family")
    sa = (await client.get(f"/api/chat/sessions/{a.sid}", headers=a.headers)).json()
    sb = (await client.get(f"/api/chat/sessions/{b.sid}", headers=b.headers)).json()
    assert sa["preferences"] and sb["preferences"] == {}
    assert (await client.get(f"/api/chat/sessions/{b.sid}/messages", headers=b.headers)).json() == []
    assert (await client.get(f"/api/chat/sessions/{a.sid}/messages", headers=b.headers)).status_code == 404
    assert (await client.post("/api/chat/message", json={"session_id": a.sid, "message": "hi"}, headers=b.headers)).status_code == 404


async def test_clear_conversation(new_chat, client):
    chat = await new_chat("saket")
    await chat.ask("Hello")
    assert (await client.delete(f"/api/chat/sessions/{chat.sid}/messages", headers=chat.headers)).status_code == 204
    assert (await client.get(f"/api/chat/sessions/{chat.sid}/messages", headers=chat.headers)).json() == []


async def test_validation_and_rate_limit(new_chat, client, monkeypatch):
    from app.core.config import get_settings

    chat = await new_chat("saket")
    assert (await client.post("/api/chat/message", json={"session_id": chat.sid, "message": ""}, headers=chat.headers)).status_code == 422
    assert (await client.post("/api/chat/message", json={"session_id": chat.sid, "message": "x" * 2000}, headers=chat.headers)).status_code == 422
    from app.core.rate_limit import limiter

    limiter.reset()
    monkeypatch.setattr(get_settings(), "rate_limit_chat_per_minute", 2)
    codes = [(await client.post("/api/chat/message", json={"session_id": chat.sid, "message": "hi"}, headers=chat.headers)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_live_failure_without_sample_data_is_honest(new_chat, fake_osm, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "discovery_sample_data", False)
    fake_osm.fail = True
    r = await (await new_chat("saket")).ask("Suggest some cafes nearby")
    assert r["places"] == [] and "didn't respond" in r["text"] and "[Sample]" not in r["text"]
