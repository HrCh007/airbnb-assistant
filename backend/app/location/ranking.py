"""Configurable recommendation ranking (OpenStreetMap has no ratings or prices,
so ranking relies on signals the data actually contains).

score = Σ wᵢ·signalᵢ / Σ wᵢ
* relevance    - matched the category's primary tags / query terms in name & cuisine
* distance     - linear decay to 0 at the search radius
* open         - open now 1, unknown 0.5, closed 0 (from OSM opening_hours)
* preference   - budget / diet / companions fit, where tags allow
* completeness - has address, opening hours, phone/website (better guest experience)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.common import PlaceResult

_STOP = {"near", "nearby", "good", "best", "the", "some", "place", "places", "food", "around", "nice"}


@dataclass
class RankingContext:
    query: str
    radius_km: float
    budget: str | None = None  # budget / moderate / premium
    dietary: str | None = None
    companions: str | None = None
    wants_open_now: bool = False
    wants_late: bool = False


def _term_overlap(query: str, place: PlaceResult) -> float:
    terms = {t for t in re.findall(r"[a-z]{3,}", query.lower())} - _STOP
    if not terms:
        return 0.0
    hay = " ".join([place.name, place.category or "", place.cuisine or "", " ".join(place.types)]).lower()
    return sum(1 for t in terms if t in hay) / len(terms)


def _preference_fit(p: PlaceResult, ctx: RankingContext) -> float:
    score, n = 0.0, 0
    if ctx.budget:
        n += 1
        if p.price_level is None:
            score += 0.5
        elif ctx.budget == "budget":
            score += {1: 1.0, 2: 0.5}.get(p.price_level, 0.0)
        elif ctx.budget == "premium":
            score += {3: 1.0, 4: 1.0, 2: 0.5}.get(p.price_level, 0.1)
        else:
            score += {2: 1.0, 1: 0.6, 3: 0.6}.get(p.price_level, 0.3)
    if ctx.dietary in {"vegetarian", "vegan", "jain"}:
        n += 1
        score += 1.0 if (p.diet or "").lower().startswith(("vegetarian", "pure veg", "vegan")) else 0.3
    if ctx.companions == "family":
        n += 1
        score += 0.2 if any(t in {"bar", "pub", "nightclub"} for t in p.types) else 0.8
    return score / n if n else 0.5


def _completeness(p: PlaceResult) -> float:
    return sum([bool(p.address), bool(p.opening_hours), bool(p.phone or p.website)]) / 3


def rank_places(scored: list[tuple[PlaceResult, float]], ctx: RankingContext, s: Settings) -> list[PlaceResult]:
    """`scored` pairs each place with its base relevance (0..1) from tag matching."""
    w = {
        "rel": s.rank_weight_relevance, "dist": s.rank_weight_distance, "open": s.rank_weight_open,
        "pref": s.rank_weight_preference, "comp": s.rank_weight_completeness,
    }
    total = sum(w.values()) or 1.0
    out = []
    for p, base in scored:
        relevance = min(1.0, 0.7 * base + 0.3 * _term_overlap(ctx.query, p))
        proximity = max(0.0, 1 - p.distance_km / ctx.radius_km) if p.distance_km is not None and ctx.radius_km else 0.3
        if ctx.wants_late:
            open_score = 1.0 if p.open_late else (0.4 if p.open_late is None else 0.0)
        else:
            open_score = 1.0 if p.open_now else (0.5 if p.open_now is None else 0.0)
        score = (
            w["rel"] * relevance + w["dist"] * proximity + w["open"] * open_score
            + w["pref"] * _preference_fit(p, ctx) + w["comp"] * _completeness(p)
        ) / total
        p.score = round(score, 4)
        out.append(p)
    return sorted(out, key=lambda p: p.score or 0, reverse=True)
