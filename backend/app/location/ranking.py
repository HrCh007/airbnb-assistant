"""Configurable recommendation ranking.

score = w_rel * relevance + w_dist * proximity + w_rating * quality
      + w_open * open_status + w_pref * preference_fit      (weights normalised)

* relevance  - provider's own relevance order + query-term overlap
* proximity  - linear decay to 0 at the search radius
* quality    - Bayesian-smoothed rating (few reviews pull towards the prior)
* open       - open now = 1, unknown = 0.5, closed = 0
* preference - match to the guest's budget preference (price level)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.common import PlaceResult

_PRIOR_RATING = 3.9
_PRIOR_WEIGHT = 40


@dataclass
class RankingContext:
    query: str
    radius_km: float
    budget: str | None = None  # budget / moderate / premium
    wants_open_now: bool = False


def _bayes_rating(rating: float | None, count: int | None) -> float:
    if rating is None:
        return 0.4
    n = count or 0
    smoothed = (n * rating + _PRIOR_WEIGHT * _PRIOR_RATING) / (n + _PRIOR_WEIGHT)
    return max(0.0, min(1.0, (smoothed - 3.0) / 2.0))


def _budget_fit(price: int | None, budget: str | None) -> float:
    if budget is None or price is None:
        return 0.5
    if budget == "budget":
        return {0: 1.0, 1: 1.0, 2: 0.5}.get(price, 0.0)
    if budget == "moderate":
        return {2: 1.0, 1: 0.7, 3: 0.6}.get(price, 0.3)
    if budget == "premium":
        return {4: 1.0, 3: 1.0, 2: 0.5}.get(price, 0.1)
    return 0.5


def _term_overlap(query: str, place: PlaceResult) -> float:
    terms = {t for t in re.findall(r"[a-z]{3,}", query.lower())} - {"near", "nearby", "good", "best", "the", "some", "place", "places"}
    if not terms:
        return 0.0
    hay = " ".join([place.name, place.category or "", " ".join(place.types), place.summary or ""]).lower()
    return sum(1 for t in terms if t in hay) / len(terms)


def rank_places(places: list[PlaceResult], ctx: RankingContext, s: Settings) -> list[PlaceResult]:
    weights = {
        "rel": s.rank_weight_relevance,
        "dist": s.rank_weight_distance,
        "rating": s.rank_weight_rating,
        "open": s.rank_weight_open,
        "pref": s.rank_weight_preference,
    }
    total = sum(weights.values()) or 1.0
    n = max(len(places), 1)
    for i, p in enumerate(places):
        # Provider order is a soft relevance signal; it must not swamp quality.
        relevance = 0.6 * (1 - 0.6 * i / n) + 0.4 * _term_overlap(ctx.query, p)
        proximity = (
            max(0.0, 1 - p.distance_km / ctx.radius_km) if p.distance_km is not None and ctx.radius_km else 0.3
        )
        quality = _bayes_rating(p.rating, p.user_rating_count)
        open_score = 1.0 if p.open_now else (0.5 if p.open_now is None else 0.0)
        pref = _budget_fit(p.price_level, ctx.budget)
        score = (
            weights["rel"] * relevance
            + weights["dist"] * proximity
            + weights["rating"] * quality
            + weights["open"] * open_score
            + weights["pref"] * pref
        ) / total
        p.score = round(score, 4)
    return sorted(places, key=lambda p: p.score or 0, reverse=True)
