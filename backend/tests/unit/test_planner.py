"""Unit tests for the heuristic query planner (intent routing + search plans)."""

from __future__ import annotations

import pytest

from app.llm.heuristic_planner import heuristic_plan

INTENT_CASES = [
    ("What's the Wi-Fi password?", "PROPERTY_QUERY"),
    ("What time is check-in?", "PROPERTY_QUERY"),
    ("Is parking available?", "PROPERTY_QUERY"),
    ("Can I smoke on the balcony?", "PROPERTY_QUERY"),
    ("Do you have a swimming pool?", "PROPERTY_QUERY"),  # unknown -> safest: knowledge base
    ("Suggest restaurants nearby.", "LOCAL_DISCOVERY_QUERY"),
    ("Suggest good cafes nearby", "LOCAL_DISCOVERY_QUERY"),
    ("Where can I get good momos nearby?", "LOCAL_DISCOVERY_QUERY"),
    ("Where can I watch the India cricket match?", "LOCAL_DISCOVERY_QUERY"),
    ("Suggest a quiet place where I can work on my laptop.", "LOCAL_DISCOVERY_QUERY"),
    ("Where can I find a good gym?", "LOCAL_DISCOVERY_QUERY"),
    ("Where can I get a cake delivered?", "LOCAL_DISCOVERY_QUERY"),
    ("Is there a restaurant nearby that offers parking?", "LOCAL_DISCOVERY_QUERY"),
    ("Is Saket a good area for shopping?", "LOCAL_DISCOVERY_QUERY"),
    ("How far is the airport?", "GENERAL_NEIGHBOURHOOD_QUERY"),
    ("Does the property have parking, and what are some good restaurants nearby?", "MIXED_QUERY"),
    ("Thanks!", "GENERAL_CONVERSATION"),
    ("hello", "GENERAL_CONVERSATION"),
    ("Hi there", "GENERAL_CONVERSATION"),
    ("Are you a rag bot?", "GENERAL_CONVERSATION"),
    ("are u a real person", "GENERAL_CONVERSATION"),
    ("What's the nearest hospital?", "EMERGENCY_QUERY"),
    ("My friend is bleeding, help", "EMERGENCY_QUERY"),
    ("Bhai, nearby koi achhi jagah batao dinner ke liye.", "LOCAL_DISCOVERY_QUERY"),
    # Property cues outrank weak / category-only local cues.
    ("Is breakfast included?", "PROPERTY_QUERY"),
    ("How do I get in? Where is the key?", "PROPERTY_QUERY"),
    ("Where can I park my car at the flat?", "PROPERTY_QUERY"),
    ("Do you provide airport pickup?", "PROPERTY_QUERY"),
    ("Is there a gym in the building?", "PROPERTY_QUERY"),
    ("Is there a good breakfast place nearby?", "LOCAL_DISCOVERY_QUERY"),
    ("Which metro station is closest?", "MIXED_QUERY"),
]


@pytest.mark.parametrize("message,intent", INTENT_CASES)
def test_intent_routing(message, intent):
    assert heuristic_plan(message).intent == intent


def test_property_queries_do_not_trigger_places():
    for q in ["What's the Wi-Fi password?", "What's the checkout time tomorrow?", "Is there parking?"]:
        assert heuristic_plan(q).places is None


def test_emergency_plans_nearest_hospital_by_distance():
    plan = heuristic_plan("What's the nearest hospital?")
    assert plan.places and plan.places.category_key == "hospital" and plan.places.rank_by_distance


def test_essentials_rank_by_distance():
    plan = heuristic_plan("Where is the nearest pharmacy?")
    assert plan.places.category_key == "pharmacy" and plan.places.rank_by_distance


def test_late_night_requests_late_places():
    plan = heuristic_plan("Are there any places open after midnight?")
    assert plan.places.late_night is True


@pytest.mark.parametrize("q,km", [("cafes within 1 km", 1), ("things to do in 5 km radius", 5), ("restaurants within 10 km", 10), ("cafes 3km away", 3)])
def test_radius_phrases(q, km):
    plan = heuristic_plan(q)
    assert plan.places.radius_m == km * 1000


def test_radius_from_message():
    plan = heuristic_plan("What are some interesting things to do within 5 km?")
    assert plan.places.radius_m == 5000
    assert plan.preference_updates.max_distance_km == 5


def test_hinglish_detected():
    assert heuristic_plan("Bhai, nearby koi achhi jagah batao dinner ke liye.").language == "hinglish"
    assert heuristic_plan("Suggest some restaurants").language == "english"


def test_refine_previous_cheaper_keeps_query_and_lowers_price():
    first = heuristic_plan("Suggest some nice places for dinner nearby.")
    plan = heuristic_plan("Anything more budget-friendly?", first.places.model_dump(), True)
    assert plan.intent == "LOCAL_DISCOVERY_QUERY"
    assert plan.places.action == "refine_previous"
    assert plan.places.budget == "budget"
    assert plan.places.category_key == "restaurant"
    assert plan.preference_updates.budget == "budget"


def test_refine_closer_and_premium():
    first = heuristic_plan("Suggest cafes").places.model_dump()
    assert heuristic_plan("anything closer?", first, True).places.rank_by_distance
    assert heuristic_plan("something fancier", first, True).places.budget == "premium"


def test_filter_previous_results():
    first = heuristic_plan("Suggest some restaurants").places.model_dump()
    plan = heuristic_plan("Any of these good for families?", first, True)
    assert plan.places.action == "filter_previous"
    assert plan.preference_updates.companions == "family"


def test_new_category_is_new_search_not_refinement():
    first = heuristic_plan("Suggest some restaurants").places.model_dump()
    plan = heuristic_plan("What about a nice place for drinks and dancing?", first, True)
    assert plan.places.action == "search"
    assert plan.places.category_key in {"club", "bar"}


def test_specific_cuisine_becomes_keyword():
    plan = heuristic_plan("tibetan food nearby")
    assert plan.places.keyword == "tibetan"
    assert heuristic_plan("Where can I get good momos nearby?").places.keyword is None  # covered by a category


def test_unrecognised_message_is_low_confidence():
    assert heuristic_plan("hmm what do you reckon about the vibe").confident is False
    assert heuristic_plan("What's the Wi-Fi password?").confident is True


def test_switch_location():
    assert heuristic_plan("Actually I'm staying in Dwarka now").switch_location == "dwarka"
    assert heuristic_plan("switch to saket").switch_location == "saket"


def test_preferences_extracted():
    p = heuristic_plan("Pure veg family dinner place, walking distance please").preference_updates
    assert p.dietary == "vegetarian" and p.companions == "family" and p.travel_mode == "walking"
