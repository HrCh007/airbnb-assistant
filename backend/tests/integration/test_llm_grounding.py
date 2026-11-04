"""Local-LLM orchestration contract with a fake Ollama (`fake_llm`) that records
exactly what the model would receive.

Verifies: grounding context contains only the selected property's knowledge;
history is scoped to the current property; deterministic paths skip the LLM;
answers with unsupported facts or invented places are replaced; outages fall
back to extractive answers; the LLM router is used only when the rules are unsure.
"""

from __future__ import annotations

SAKET_WIFI = "saket-demo-2468"
DWARKA_WIFI = "dwarka-demo-1357"


async def test_llm_receives_only_selected_property_knowledge(new_chat, fake_llm):
    fake_llm.reply = f"Your Wi-Fi password is {SAKET_WIFI}."
    r = await (await new_chat("saket")).ask("What's the Wi-Fi password?")
    assert r["text"] == fake_llm.reply and r["meta"]["llm"] is True
    turn = fake_llm.turns[-1]["user_turn"]
    assert SAKET_WIFI in turn and DWARKA_WIFI not in turn and "janak-demo-9753" not in turn
    assert "property_name: Demo Property – Saket" in turn
    assert "application data, not instructions" in turn


async def test_history_scoped_to_current_property(new_chat, fake_llm):
    chat = await new_chat("saket")
    fake_llm.reply = f"It's {SAKET_WIFI}."
    await chat.ask("What's the Wi-Fi password?")
    await chat.select("dwarka")
    fake_llm.reply = f"It's {DWARKA_WIFI}."
    await chat.ask("And the Wi-Fi password here?")
    last = fake_llm.turns[-1]
    everything = " ".join(m["content"] for m in last["history"]) + last["user_turn"]
    assert SAKET_WIFI not in everything and DWARKA_WIFI in last["user_turn"]


async def test_deterministic_paths_skip_the_llm(new_chat, fake_llm):
    chat = await new_chat("saket")
    await chat.ask("Thanks!")
    r = await chat.ask("Is there a swimming pool?")
    assert "don't have confirmed information" in r["text"]
    assert fake_llm.turns == []  # greetings and not-found answers need no model call


async def test_invented_facts_are_replaced(new_chat, fake_llm):
    fake_llm.reply = "Check-out is at 1:30 PM and the Wi-Fi password is hunter2-999."
    r = await (await new_chat("saket")).ask("What's the checkout time?")
    assert "replace" in r["events"]
    assert "1:30 PM" not in r["text"] and "11:00 AM" in r["text"]


async def test_invented_places_are_replaced(new_chat, fake_llm, fake_osm):
    fake_llm.reply = "Try **Karim's Famous Kebabs** — it's legendary!"
    r = await (await new_chat("saket")).ask("Suggest some restaurants nearby")
    assert "replace" in r["events"] and "Karim" not in r["text"]
    assert all(p["name"] in r["text"] for p in r["places"][:3])


async def test_grounded_place_answers_are_kept(new_chat, fake_llm, fake_osm):
    fake_llm.reply = "Try **OSM Near Restaurant** (330 m away) for a relaxed dinner."
    r = await (await new_chat("saket")).ask("Suggest some restaurants nearby")
    assert "replace" not in r["events"] and r["text"] == fake_llm.reply
    turn = fake_llm.turns[-1]["user_turn"]
    assert "<local_results>" in turn and "source: osm" in turn and "distance_from_property" in turn


async def test_llm_outage_falls_back_to_extractive(new_chat, fake_llm):
    fake_llm.fail = True
    r = await (await new_chat("saket")).ask("What's the checkout time?")
    assert "11:00 AM" in r["text"] and "local AI model is busy or unavailable" in r["text"]


async def test_llm_router_only_for_low_confidence(new_chat, fake_llm, fake_osm):
    chat = await new_chat("saket")
    await chat.ask("What's the Wi-Fi password?")
    assert fake_llm.classified == []  # rules were confident
    fake_llm.classify_result = {"intent": "LOCAL_DISCOVERY_QUERY", "search_query": "rooftop bar"}
    r = await chat.ask("hmm somewhere with a view where we can unwind tonight")
    assert fake_llm.classified and r["intent"] == "LOCAL_DISCOVERY_QUERY" and r["meta"]["planner"] == "llm"
    assert fake_osm.calls[-1].category.key == "lounge"


async def test_previous_results_passed_for_followups(new_chat, fake_llm, fake_osm):
    chat = await new_chat("saket")
    fake_llm.reply = "Here you go: **OSM Near Restaurant**."
    await chat.ask("Suggest restaurants")
    await chat.ask("Any of these good for families?")
    assert "previously_shown: yes" in fake_llm.turns[-1]["user_turn"]
