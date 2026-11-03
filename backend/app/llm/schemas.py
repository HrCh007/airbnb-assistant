"""Structured query plan produced by the planner (LLM or heuristic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IntentLiteral = Literal[
    "PROPERTY_QUERY",
    "LOCAL_DISCOVERY_QUERY",
    "GENERAL_NEIGHBOURHOOD_QUERY",
    "MIXED_QUERY",
    "GENERAL_CONVERSATION",
    "EMERGENCY_QUERY",
]


class PlacesPlan(BaseModel):
    action: Literal["search", "refine_previous", "filter_previous"]
    query: str
    category_key: str | None = None
    included_type: str | None = None
    radius_m: int | None = None
    open_now: bool | None = None
    max_price_level: int | None = None
    min_rating: float | None = None
    rank_by_distance: bool = False


class PreferenceUpdates(BaseModel):
    budget: Literal["budget", "moderate", "premium"] | None = None
    dietary: str | None = None
    companions: str | None = None
    interests: list[str] | None = None
    travel_mode: str | None = None
    max_distance_km: float | None = None


class QueryPlan(BaseModel):
    intent: IntentLiteral
    property_question: str | None = None
    places: PlacesPlan | None = None
    preference_updates: PreferenceUpdates = Field(default_factory=PreferenceUpdates)
    clarification_question: str | None = None
    switch_location: str | None = None
    language: Literal["english", "hinglish"] = "english"
    planner: str = "heuristic"  # set by the backend: "llm" or "heuristic"

    @property
    def needs_property_rag(self) -> bool:
        return self.intent in {
            "PROPERTY_QUERY",
            "MIXED_QUERY",
            "EMERGENCY_QUERY",
            "GENERAL_NEIGHBOURHOOD_QUERY",
        }
