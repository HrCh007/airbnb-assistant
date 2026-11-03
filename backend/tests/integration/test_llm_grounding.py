"""Claude-mode orchestration contract, with the Claude calls replaced by fakes
that record exactly what the model would receive.

Verifies: grounding context contains only the selected property's knowledge;
conversation history is scoped to the current property after a switch; the
LLM plan drives retrieval/search; and failures degrade to offline answers.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.llm import claude
from app.llm.heuristic_planner import heuristic_plan
from app.llm.schemas import PlacesPlan, QueryPlan


class FakeClaude:
    def __init__(self):
        self.turns: list[dict] = []
        self.plan_override: QueryPlan | None = None
        self.plan_error = False
        self.answer_error = False

    async def plan_with_llm(self, history, message, summary):
        if self.plan_error:
            raise claude.LLMUnavailable("connection_error")
        plan = self.plan_override or heuristic_plan(message)
        plan = plan.model_copy(deep=True)
        plan.planner = "llm"
        return plan

    async def stream_answer(self, history, user_turn):
        self.turns.append({"history": history, "user_turn": user_turn})
        if self.answer_error:
            raise claude.LLMUnavailable("api_status_529")
        for part in ["Grounded ", "answer."]:
            yield part


@pytest.fixture
def fake_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(claude, "plan_with_llm", fake.plan_with_llm)
    monkeypatch.setattr(claude, "stream_answer", fake.stream_answer)
    return fake


async def test_claude_receives_only_selected_property_knowledge(new_chat, fake_claude):
    r = await (await new_chat("saket")).ask("What's the Wi-Fi password?")
    assert r["text"] == "Grounded answer." and r["intent"] == "PROPERTY_QUERY"
    turn = fake_claude.turns[-1]["user_turn"]
    assert "saket-demo-2468" in turn
    assert "dwarka-demo-1357" not in turn and "janak-demo-9753" not in turn
    assert "property_name: Demo Property – Saket" in turn
    assert "<property_knowledge>" in turn and "<local_results>" not in turn


async def test_history_is_scoped_to_current_property_after_switch(new_chat, fake_claude):
    chat = await new_chat("saket")
    await chat.ask("What's the Wi-Fi password?")
    await chat.select("dwarka")
    await chat.ask("And what's the Wi-Fi password here?")
    last = fake_claude.turns[-1]
    flat = " ".join(m["content"] for m in last["history"]) + last["user_turn"]
    assert "saket-demo-2468" not in flat  # Saket facts never reach Dwarka's context
    assert "dwarka-demo-1357" in last["user_turn"]


async def test_history_alternates_and_excludes_current_message(new_chat, fake_claude):
    chat = await new_chat("saket")
    await chat.ask("Hi there")
    await chat.ask("What's the checkout time?")
    hist = fake_claude.turns[-1]["history"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["content"] == "Hi there"


async def test_llm_plan_drives_places_search(new_chat, fake_claude, fake_places):
    fake_claude.plan_override = QueryPlan(
        intent="LOCAL_DISCOVERY_QUERY",
        places=PlacesPlan(action="search", query="quiet cafe good for working on a laptop", included_type="cafe", radius_m=2000),
    )
    r = await (await new_chat("janakpuri")).ask("Suggest a quiet place where I can work on my laptop.")
    spec = fake_places.calls[-1]
    assert spec.query == "quiet cafe good for working on a laptop" and spec.radius_m == 2000
    assert (spec.latitude, spec.longitude) == (28.6219, 77.0878)
    turn = fake_claude.turns[-1]["user_turn"]
    assert "<local_results>" in turn and "distance_from_property" in turn
    assert r["places"]


async def test_previous_results_are_passed_for_follow_ups(new_chat, fake_claude, fake_places):
    chat = await new_chat("saket")
    await chat.ask("Suggest restaurants")
    fake_claude.plan_override = QueryPlan(
        intent="LOCAL_DISCOVERY_QUERY", places=PlacesPlan(action="filter_previous", query="restaurants")
    )
    calls = len(fake_places.calls)
    await chat.ask("Any of these good for families?")
    assert len(fake_places.calls) == calls
    assert "previously_shown: yes" in fake_claude.turns[-1]["user_turn"]


async def test_withheld_sensitive_notice_reaches_claude(new_chat, fake_claude):
    await (await new_chat("janakpuri")).ask("What's the Wi-Fi password?")
    turn = fake_claude.turns[-1]["user_turn"]
    assert "janak-demo-9753" not in turn
    assert "has not verified their booking PIN" in turn


async def test_emergency_is_never_downgraded_by_planner(new_chat, fake_claude, fake_places):
    fake_claude.plan_override = QueryPlan(intent="GENERAL_CONVERSATION")
    r = await (await new_chat("saket")).ask("Emergency! my friend collapsed, nearest hospital?")
    assert r["intent"] == "EMERGENCY_QUERY"
    assert fake_places.calls[-1].included_type == "hospital"


async def test_planner_failure_falls_back_to_heuristics(new_chat, fake_claude):
    fake_claude.plan_error = True
    r = await (await new_chat("saket")).ask("What's the checkout time?")
    assert r["intent"] == "PROPERTY_QUERY"
    assert r["text"] == "Grounded answer."  # answer still generated by Claude


async def test_claude_outage_degrades_to_offline_answer(new_chat, fake_claude):
    fake_claude.answer_error = True
    r = await (await new_chat("saket")).ask("What's the checkout time?")
    assert "11:00 AM" in r["text"]
    assert "AI service is briefly unavailable" in r["text"]


async def test_clarification_question_is_returned(new_chat, fake_claude):
    fake_claude.plan_override = QueryPlan(
        intent="GENERAL_CONVERSATION", clarification_question="Are you after something casual, family-style or a bit fancy?"
    )
    r = await (await new_chat("saket")).ask("Bhai, nearby koi achhi jagah batao")
    assert r["text"] == "Are you after something casual, family-style or a bit fancy?"
    assert fake_claude.turns == []
