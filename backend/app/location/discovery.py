"""Location intelligence service: search around a property's exact coordinates,
compute distances, filter, rank and merge host-curated recommendations."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.location.geo import directions_url, haversine_km, search_url
from app.location.providers import PlacesError, SearchSpec, get_places_provider
from app.location.ranking import RankingContext, rank_places
from app.models import CuratedRecommendation, Property
from app.schemas.common import PlaceResult

log = logging.getLogger(__name__)


@dataclass
class DiscoveryRequest:
    query: str
    included_type: str | None = None
    radius_m: int | None = None
    open_now: bool | None = None
    max_price_level: int | None = None
    min_rating: float | None = None
    budget: str | None = None
    category_key: str | None = None
    rank_by_distance: bool = False
    limit: int | None = None
    exclude_place_ids: list[str] = field(default_factory=list)
    travel_mode: str | None = None


@dataclass
class DiscoveryResult:
    provider: str
    radius_m: int
    results: list[PlaceResult]
    notice: str | None = None
    error: bool = False
    origin: dict = field(default_factory=dict)


_cache: dict[str, tuple[float, list[PlaceResult]]] = {}


def clear_cache() -> None:
    _cache.clear()


def _curated_matches(rec: CuratedRecommendation, req: DiscoveryRequest) -> bool:
    hay = " ".join([rec.name, rec.category, rec.note or "", " ".join(rec.tags or [])]).lower()
    if req.category_key and req.category_key.replace("_", " ") in hay:
        return True
    terms = [t for t in req.query.lower().split() if len(t) > 3]
    return any(t in hay for t in terms)


async def discover(db: AsyncSession, prop: Property, req: DiscoveryRequest) -> DiscoveryResult:
    s = get_settings()
    provider = get_places_provider()
    radius = min(req.radius_m or prop.discovery_radius_m or s.default_discovery_radius_m, s.max_discovery_radius_m)
    limit = req.limit or s.places_results_to_show

    if prop.latitude is None or prop.longitude is None:
        return DiscoveryResult(
            provider=provider.name,
            radius_m=radius,
            results=[],
            error=True,
            notice=(
                "This property's exact coordinates have not been configured yet, so I can't "
                "measure distances or search around it. The host can add them in the admin dashboard."
            ),
        )

    origin = {"latitude": prop.latitude, "longitude": prop.longitude, "property_id": str(prop.id)}
    spec = SearchSpec(
        query=req.query,
        latitude=prop.latitude,
        longitude=prop.longitude,
        radius_m=radius,
        included_type=req.included_type,
        open_now=req.open_now,
        max_price_level=req.max_price_level,
        min_rating=req.min_rating,
        max_results=s.places_max_results,
        rank_by_distance=req.rank_by_distance,
    )

    notice: str | None = None
    error = False
    cache_key = f"{provider.name}|{prop.id}|{spec.query}|{spec.included_type}|{radius}|{spec.open_now}|{spec.max_price_level}|{spec.min_rating}|{spec.rank_by_distance}"
    cached = _cache.get(cache_key)
    places: list[PlaceResult]
    if cached and time.monotonic() - cached[0] < s.places_cache_ttl_seconds:
        places = [p.model_copy(deep=True) for p in cached[1]]
    else:
        try:
            places = await provider.search(spec)
            _cache[cache_key] = (time.monotonic(), [p.model_copy(deep=True) for p in places])
        except PlacesError as exc:
            log.warning("Places search failed: %s", exc)
            places, error = [], True
            notice = "Live local search is temporarily unavailable, so I can't fetch current places right now."

    if provider.name == "disabled":
        notice = (
            "Live local search isn't configured yet (no Google Maps API key), so I can't look up "
            "current businesses. I can only share the host's own recommendations."
        )
    elif provider.name == "mock":
        notice = "DEV MODE: local results are fictional mock data, not real businesses."

    # Distance from the exact property coordinates + post-filters.
    filtered: list[PlaceResult] = []
    for p in places:
        if p.place_id and p.place_id in req.exclude_place_ids:
            continue
        if p.latitude is not None and p.longitude is not None:
            p.distance_km = round(haversine_km(prop.latitude, prop.longitude, p.latitude, p.longitude), 2)
            # Text search uses a location *bias*; drop anything far outside the radius.
            if p.distance_km > (radius / 1000) * 1.5:
                continue
        if req.min_rating and p.rating is not None and p.rating < req.min_rating:
            continue
        if req.max_price_level is not None and p.price_level is not None and p.price_level > req.max_price_level:
            continue
        if req.open_now and p.open_now is False:
            continue
        p.maps_url = p.maps_url or (search_url(p.name, p.address, p.place_id) if p.source != "mock" else None)
        p.directions_url = (
            directions_url(prop.latitude, prop.longitude, p.name, p.address, p.place_id, req.travel_mode)
            if p.source != "mock"
            else None
        )
        filtered.append(p)

    ranked = rank_places(
        filtered,
        RankingContext(query=req.query, radius_km=radius / 1000, budget=req.budget, wants_open_now=bool(req.open_now)),
        s,
    )[:limit]

    # Host-curated picks complement (never replace) live results.
    curated_rows = (
        await db.execute(
            select(CuratedRecommendation).where(
                CuratedRecommendation.property_id == prop.id, CuratedRecommendation.is_active.is_(True)
            )
        )
    ).scalars().all()
    curated: list[PlaceResult] = []
    for rec in curated_rows:
        if not _curated_matches(rec, req):
            continue
        dist = (
            round(haversine_km(prop.latitude, prop.longitude, rec.latitude, rec.longitude), 2)
            if rec.latitude is not None and rec.longitude is not None
            else None
        )
        curated.append(
            PlaceResult(
                place_id=rec.google_place_id,
                name=rec.name,
                category=rec.category.replace("_", " ").title(),
                address=rec.address,
                latitude=rec.latitude,
                longitude=rec.longitude,
                distance_km=dist,
                host_note=rec.note,
                source="host_curated",
                maps_url=search_url(rec.name, rec.address, rec.google_place_id),
                directions_url=directions_url(prop.latitude, prop.longitude, rec.name, rec.address, rec.google_place_id, req.travel_mode),
            )
        )
    # De-duplicate: if a curated pick is also a live result, annotate the live one.
    live_ids = {p.place_id for p in ranked if p.place_id}
    for c in curated:
        if c.place_id and c.place_id in live_ids:
            for p in ranked:
                if p.place_id == c.place_id:
                    p.host_note = c.host_note
    curated = [c for c in curated if not (c.place_id and c.place_id in live_ids)][:2]

    return DiscoveryResult(
        provider=provider.name,
        radius_m=radius,
        results=curated + ranked,
        notice=notice,
        error=error,
        origin=origin,
    )


async def place_details(place_id: str) -> PlaceResult | None:
    try:
        return await get_places_provider().details(place_id)
    except PlacesError:
        return None


def property_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
