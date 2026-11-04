"""Location intelligence: search around a property's exact coordinates.

Data source order (first that works wins):
1. Live OpenStreetMap via Overpass (DISCOVERY_LIVE=true)          source "osm"
2. Local OSM snapshot (data/discovery/osm_snapshot.json, if built) source "osm_snapshot"
3. Fictional sample dataset (DISCOVERY_SAMPLE_DATA=true)            source "sample"
Host-curated picks are merged on top                               source "host_curated"

Every result's distance is computed (haversine) from the property's coordinates.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.location import opening_hours
from app.location.categories import Category, category_by_key, match_category, matches_category
from app.location.geo import coords_directions_url, haversine_km, osm_url
from app.location.providers import (
    ATTRIBUTION,
    DiscoveryError,
    RawPlace,
    SearchSpec,
    get_overpass,
    get_sample,
    get_snapshot,
)
from app.location.ranking import RankingContext, rank_places
from app.models import CuratedRecommendation, Property
from app.schemas.common import PlaceResult

log = logging.getLogger(__name__)

_FOOD_TO_BUDGET = {"restaurant", "family_restaurant", "fine_dining", "north_indian", "late_night_food", "street_food"}


@dataclass
class DiscoveryRequest:
    query: str
    category_key: str | None = None
    keyword: str | None = None
    radius_m: int | None = None
    open_now: bool | None = None
    late_night: bool = False
    budget: str | None = None
    dietary: str | None = None
    companions: str | None = None
    rank_by_distance: bool = False
    limit: int | None = None
    exclude_ids: list[str] = field(default_factory=list)
    travel_mode: str | None = None


@dataclass
class DiscoveryResult:
    provider: str
    radius_m: int
    results: list[PlaceResult]
    notice: str | None = None
    error: bool = False
    origin: dict = field(default_factory=dict)
    attribution: str | None = None


_cache: dict[str, tuple[float, list[RawPlace]]] = {}
_live_down_until = 0.0


def clear_cache() -> None:
    global _live_down_until
    _cache.clear()
    _live_down_until = 0.0


def snap_radius(radius_m: int | None, default: int) -> int:
    s = get_settings()
    r = radius_m or default or s.default_discovery_radius_m
    return max(200, min(int(r), s.max_discovery_radius_m))


# ------------------------------------------------------------------ OSM tags -> PlaceResult
_PRIMARY_KEYS = ("amenity", "shop", "leisure", "tourism", "historic", "healthcare", "railway", "office", "craft")


def _pretty(v: str) -> str:
    return v.replace("_", " ").replace(";", ", ").strip().title()


def _address(tags: dict) -> str | None:
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x)
    parts = [street, tags.get("addr:place") or tags.get("addr:neighbourhood") or tags.get("addr:suburb"), tags.get("addr:city")]
    parts = [p for p in parts if p]
    return ", ".join(dict.fromkeys(parts)) if parts else None


def to_place_result(raw: RawPlace, origin_lat: float, origin_lon: float, travel_mode: str | None) -> PlaceResult:
    t = raw.tags
    types = [t[k] for k in _PRIMARY_KEYS if t.get(k)]
    price, note = None, None
    if t.get("amenity") in {"fast_food", "food_court"} or re.search(r"dhaba|bhojnalaya|canteen", raw.name, re.I):
        price, note = 1, "Usually budget-friendly (fast food / dhaba / food court)"
    diet = {"only": "Pure vegetarian", "yes": "Vegetarian options"}.get(str(t.get("diet:vegetarian", "")).lower())
    hours = t.get("opening_hours")
    st = opening_hours.status(hours)
    is_sample = raw.source == "sample"
    return PlaceResult(
        osm_id=raw.osm_id if not is_sample else None,
        name=raw.name,
        category=_pretty(types[0]) if types else None,
        cuisine=_pretty(t["cuisine"]) if t.get("cuisine") else None,
        types=types,
        address=_address(t),
        latitude=raw.lat,
        longitude=raw.lon,
        distance_km=round(haversine_km(origin_lat, origin_lon, raw.lat, raw.lon), 2),
        opening_hours=hours,
        open_now=st.open_now,
        open_late=st.late_night,
        price_level=price,
        price_note=note,
        diet=diet,
        phone=t.get("phone") or t.get("contact:phone"),
        website=t.get("website") or t.get("contact:website"),
        summary=t.get("description") or (t.get("sample_note") if is_sample else None),
        maps_url=None if is_sample else osm_url(raw.osm_id, raw.lat, raw.lon),
        osm_url=None if is_sample else osm_url(raw.osm_id, raw.lat, raw.lon),
        directions_url=None if is_sample else coords_directions_url(origin_lat, origin_lon, raw.lat, raw.lon, travel_mode),
        source=raw.source,  # type: ignore[arg-type]
    )


def _dedupe(places: list[PlaceResult]) -> list[PlaceResult]:
    seen_ids: set[str] = set()
    kept: list[PlaceResult] = []
    for p in places:
        if p.osm_id and p.osm_id in seen_ids:
            continue
        key = re.sub(r"[^a-z0-9]", "", p.name.lower())
        dup = next(
            (k for k in kept if re.sub(r"[^a-z0-9]", "", k.name.lower()) == key
             and k.latitude is not None and p.latitude is not None
             and haversine_km(k.latitude, k.longitude, p.latitude, p.longitude) < 0.1),
            None,
        )
        if dup:
            continue
        if p.osm_id:
            seen_ids.add(p.osm_id)
        kept.append(p)
    return kept


def _keyword(query: str, category: Category | None) -> str | None:
    """Free-text term for name/cuisine search when the query isn't a known category."""
    if category:
        return None
    words = [w for w in re.findall(r"[a-zA-Z]{3,}", query.lower()) if w not in {
        "near", "nearby", "good", "best", "the", "some", "place", "places", "find", "where", "can", "get",
        "suggest", "recommend", "any", "around", "here", "me", "for", "with", "and", "want", "looking",
    }]
    return " ".join(words[:2]) or None


async def _fetch(spec: SearchSpec, cache_key: str) -> tuple[list[RawPlace], str, str | None, bool]:
    """Returns (places, provider_name, notice, error)."""
    global _live_down_until
    s = get_settings()
    notice = None
    error = False
    if s.discovery_live and time.monotonic() >= _live_down_until:
        key = f"osm|{cache_key}"
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < s.discovery_cache_ttl_seconds:
            return cached[1], "osm", None, False
        try:
            places = await get_overpass().search(spec)
            _cache[key] = (time.monotonic(), places)
            return places, "osm", None, False
        except DiscoveryError as exc:
            log.warning("Live OSM search failed on every server: %s", exc)
            _live_down_until = time.monotonic() + s.discovery_failure_cooldown_seconds
            error = True
            notice = ("Live map search (OpenStreetMap) didn't respond just now, so I can't look up current places. "
                      "Please try again in a minute.")
    snapshot = get_snapshot()
    if snapshot.available:
        places = await snapshot.search(spec)
        date = snapshot.meta.get("generated_at", "")[:10]
        snap_note = f"Using saved OpenStreetMap data{f' from {date}' if date else ''}; details may have changed."
        return places, "osm_snapshot", (notice + " " + snap_note) if notice else snap_note, error
    if s.discovery_sample_data:
        places = await get_sample().search(spec)
        sample_note = "These are FICTIONAL sample places for testing, not real businesses."
        if notice:
            notice = notice.replace("so I can't look up current places. Please try again in a minute.", "so I'm showing sample data.")
        return places, "sample", (f"{notice} {sample_note}" if notice else sample_note), error
    return [], "none", (notice or "Live map search is turned off and no saved map data is available."), error


async def discover(db: AsyncSession, prop: Property, req: DiscoveryRequest) -> DiscoveryResult:
    s = get_settings()
    radius = snap_radius(req.radius_m, prop.discovery_radius_m)
    limit = req.limit or s.places_results_to_show
    if prop.latitude is None or prop.longitude is None:
        return DiscoveryResult(
            provider="none", radius_m=radius, results=[], error=True,
            notice=("This property's exact coordinates haven't been set yet, so I can't search around it. "
                    "The host can add them in the admin dashboard."),
        )
    origin = {"latitude": prop.latitude, "longitude": prop.longitude, "property_id": str(prop.id)}

    category = category_by_key(req.category_key) or match_category(req.query)
    if req.budget == "budget" and category and category.key in _FOOD_TO_BUDGET:
        category = category_by_key("budget_food")
    keyword = req.keyword or _keyword(req.query, category)
    if not category and not keyword:
        category = category_by_key("attraction")

    def _spec(cat: Category | None, kw: str | None) -> tuple[SearchSpec, str]:
        key = f"{prop.latitude:.5f},{prop.longitude:.5f}|{radius}|{cat.key if cat else ''}|{kw or ''}"
        return SearchSpec(latitude=prop.latitude, longitude=prop.longitude, radius_m=radius, category=cat, keyword=kw), key

    if keyword and req.keyword:
        # Specific term first ("tibetan"); fall back to the category if nothing matches.
        raw, provider, notice, error = await _fetch(*_spec(None, keyword))
        if not raw and category:
            raw, provider, notice, error = await _fetch(*_spec(category, None))
    else:
        raw, provider, notice, error = await _fetch(*_spec(category, keyword))

    places: list[tuple[PlaceResult, float]] = []
    for r in raw:
        p = to_place_result(r, prop.latitude, prop.longitude, req.travel_mode)
        if p.distance_km is not None and p.distance_km * 1000 > radius:
            continue
        if (p.osm_id and p.osm_id in req.exclude_ids) or p.name in req.exclude_ids:
            continue
        if req.open_now and p.open_now is False:
            continue
        if req.late_night and p.open_late is False:
            continue
        base = 1.0 if category and matches_category({**r.tags, "name": r.name}, category) else 0.6
        places.append((p, base))
    deduped = _dedupe([p for p, _ in places])
    base_by_id = {id(p): b for p, b in places}
    ctx = RankingContext(
        query=req.query, radius_km=radius / 1000, budget=req.budget, dietary=req.dietary,
        companions=req.companions, wants_open_now=bool(req.open_now), wants_late=req.late_night,
    )
    ranked = rank_places([(p, base_by_id.get(id(p), 0.6)) for p in deduped], ctx, s)
    if req.rank_by_distance:
        ranked.sort(key=lambda p: p.distance_km if p.distance_km is not None else 1e9)
    ranked = ranked[:limit]

    curated = await _curated(db, prop, req, category, radius)
    live_names = {re.sub(r"[^a-z0-9]", "", p.name.lower()) for p in ranked}
    for c in curated:
        for p in ranked:
            if (c.osm_id and c.osm_id == p.osm_id) or re.sub(r"[^a-z0-9]", "", c.name.lower()) in live_names:
                p.host_note = c.host_note
    curated = [c for c in curated if re.sub(r"[^a-z0-9]", "", c.name.lower()) not in live_names][:2]

    if not ranked and not curated and not notice:
        notice = f"I couldn't find matching places within {radius / 1000:g} km in the map data."
    return DiscoveryResult(
        provider=provider, radius_m=radius, results=curated + ranked, notice=notice, error=error,
        origin=origin, attribution=ATTRIBUTION if provider in {"osm", "osm_snapshot"} else None,
    )


async def _curated(db: AsyncSession, prop: Property, req: DiscoveryRequest, category: Category | None, radius: int) -> list[PlaceResult]:
    rows = (await db.execute(
        select(CuratedRecommendation).where(
            CuratedRecommendation.property_id == prop.id, CuratedRecommendation.is_active.is_(True)
        )
    )).scalars().all()
    out = []
    terms = [t for t in re.findall(r"[a-z]{4,}", req.query.lower())]
    for rec in rows:
        hay = " ".join([rec.name, rec.category, rec.note or "", " ".join(rec.tags or [])]).lower()
        match = (category and (category.key.replace("_", " ") in hay or category.group in hay or rec.category == category.key)) or any(
            t in hay for t in terms
        )
        if not match:
            continue
        dist = (
            round(haversine_km(prop.latitude, prop.longitude, rec.latitude, rec.longitude), 2)
            if rec.latitude is not None and rec.longitude is not None else None
        )
        if dist is not None and dist * 1000 > radius:
            continue
        out.append(PlaceResult(
            osm_id=rec.osm_id, name=rec.name, category=_pretty(rec.category), address=rec.address,
            latitude=rec.latitude, longitude=rec.longitude, distance_km=dist, price_level=rec.price_level,
            host_note=rec.note, source="host_curated",
            maps_url=osm_url(rec.osm_id, rec.latitude, rec.longitude),
            osm_url=osm_url(rec.osm_id, rec.latitude, rec.longitude),
            directions_url=(coords_directions_url(prop.latitude, prop.longitude, rec.latitude, rec.longitude, req.travel_mode)
                            if rec.latitude is not None and rec.longitude is not None else None),
        ))
    return out
