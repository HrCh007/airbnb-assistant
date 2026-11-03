"""Local discovery providers.

* GooglePlacesProvider - Google Places API (New): Text Search, Nearby Search,
  Place Details. https://developers.google.com/maps/documentation/places/web-service/op-overview
* MockPlacesProvider   - development-only adapter returning obviously fictional,
  clearly labelled results ("[DEV MOCK] ..."). Only active when
  PLACES_PROVIDER=mock. Never used in production.
* DisabledPlacesProvider - used when no API key is configured: returns no
  results with an explanatory notice, so the assistant says live search is
  unavailable instead of inventing businesses.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.schemas.common import PlaceResult

log = logging.getLogger(__name__)

PLACES_BASE = "https://places.googleapis.com/v1"
_PLACE_FIELDS = [
    "id",
    "displayName",
    "formattedAddress",
    "shortFormattedAddress",
    "location",
    "rating",
    "userRatingCount",
    "priceLevel",
    "currentOpeningHours.openNow",
    "regularOpeningHours.weekdayDescriptions",
    "primaryType",
    "primaryTypeDisplayName",
    "types",
    "googleMapsUri",
    "editorialSummary",
    "businessStatus",
]
_DETAIL_EXTRA = ["nationalPhoneNumber", "internationalPhoneNumber", "websiteUri"]
_PRICE = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
_PRICE_REV = {v: k for k, v in _PRICE.items()}


class PlacesError(Exception):
    """Raised when the upstream places provider fails."""


@dataclass
class SearchSpec:
    query: str
    latitude: float
    longitude: float
    radius_m: int
    included_type: str | None = None
    open_now: bool | None = None
    max_price_level: int | None = None
    min_rating: float | None = None
    max_results: int = 12
    rank_by_distance: bool = False
    extra: dict = field(default_factory=dict)


class PlacesProvider(Protocol):
    name: str

    async def search(self, spec: SearchSpec) -> list[PlaceResult]: ...

    async def details(self, place_id: str) -> PlaceResult | None: ...


def _parse_place(p: dict) -> PlaceResult | None:
    if p.get("businessStatus") in {"CLOSED_PERMANENTLY"}:
        return None
    loc = p.get("location") or {}
    name = (p.get("displayName") or {}).get("text")
    if not name:
        return None
    return PlaceResult(
        place_id=p.get("id"),
        name=name,
        category=(p.get("primaryTypeDisplayName") or {}).get("text")
        or (p.get("primaryType") or "").replace("_", " ").title()
        or None,
        types=p.get("types") or [],
        address=p.get("shortFormattedAddress") or p.get("formattedAddress"),
        latitude=loc.get("latitude"),
        longitude=loc.get("longitude"),
        rating=p.get("rating"),
        user_rating_count=p.get("userRatingCount"),
        price_level=_PRICE.get(p.get("priceLevel", ""), None),
        open_now=(p.get("currentOpeningHours") or {}).get("openNow"),
        opening_hours=(p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [],
        phone=p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber"),
        website=p.get("websiteUri"),
        summary=(p.get("editorialSummary") or {}).get("text"),
        maps_url=p.get("googleMapsUri"),
        source="google_places",
    )


class GooglePlacesProvider:
    name = "google_places"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self._client = client or httpx.AsyncClient(timeout=settings.places_timeout_seconds)

    async def _request(self, method: str, url: str, field_mask: str, json: dict | None = None) -> dict:
        headers = {
            "X-Goog-Api-Key": self.s.google_maps_api_key,
            "X-Goog-FieldMask": field_mask,
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(self.s.places_max_retries + 1):
            try:
                resp = await self._client.request(method, url, headers=headers, json=json)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise PlacesError(f"Places API transient error {resp.status_code}")
                if resp.status_code >= 400:
                    # Don't retry client errors (bad key, bad request). Log status only.
                    status = (resp.json().get("error") or {}).get("status", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
                    log.error("Places API error %s %s", resp.status_code, status)
                    raise PlacesError(f"Places API rejected the request ({resp.status_code} {status})")
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError, PlacesError) as exc:
                last_exc = exc
                if isinstance(exc, PlacesError) and "rejected" in str(exc):
                    break
                if attempt < self.s.places_max_retries:
                    await asyncio.sleep(0.4 * (2**attempt))
        raise PlacesError(str(last_exc) if last_exc else "Places API unavailable")

    async def search(self, spec: SearchSpec) -> list[PlaceResult]:
        mask = ",".join(f"places.{f}" for f in _PLACE_FIELDS)
        circle = {
            "center": {"latitude": spec.latitude, "longitude": spec.longitude},
            "radius": float(min(spec.radius_m, 50000)),
        }
        if spec.rank_by_distance and spec.included_type and not spec.query.strip():
            body: dict = {
                "includedTypes": [spec.included_type],
                "maxResultCount": min(spec.max_results, 20),
                "locationRestriction": {"circle": circle},
                "rankPreference": "DISTANCE",
                "languageCode": self.s.places_language,
                "regionCode": self.s.places_region,
            }
            data = await self._request("POST", f"{PLACES_BASE}/places:searchNearby", mask, body)
        else:
            body = {
                "textQuery": spec.query,
                "locationBias": {"circle": circle},
                "pageSize": min(spec.max_results, 20),
                "languageCode": self.s.places_language,
                "regionCode": self.s.places_region,
                "rankPreference": "DISTANCE" if spec.rank_by_distance else "RELEVANCE",
            }
            if spec.included_type:
                body["includedType"] = spec.included_type
                body["strictTypeFiltering"] = False
            if spec.open_now:
                body["openNow"] = True
            if spec.min_rating:
                # API accepts 0.5 increments.
                body["minRating"] = math.floor(spec.min_rating * 2) / 2
            if spec.max_price_level is not None and spec.max_price_level >= 1:
                body["priceLevels"] = [_PRICE_REV[i] for i in range(1, spec.max_price_level + 1)]
            data = await self._request("POST", f"{PLACES_BASE}/places:searchText", mask, body)
        places = [_parse_place(p) for p in data.get("places", [])]
        return [p for p in places if p is not None]

    async def details(self, place_id: str) -> PlaceResult | None:
        if not place_id or not all(c.isalnum() or c in "-_" for c in place_id):
            return None
        mask = ",".join(_PLACE_FIELDS + _DETAIL_EXTRA)
        data = await self._request("GET", f"{PLACES_BASE}/places/{place_id}", mask)
        return _parse_place(data)


class MockPlacesProvider:
    """Deterministic, obviously fictional results for local UI development and tests.

    Names are prefixed with "[DEV MOCK]" and the frontend shows a banner, so they
    can never be mistaken for real businesses.
    """

    name = "mock"

    async def search(self, spec: SearchSpec) -> list[PlaceResult]:
        seed = int(hashlib.sha256(f"{spec.query}|{spec.latitude:.4f}|{spec.longitude:.4f}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        label = (spec.included_type or spec.query or "place").replace("_", " ").strip().title()[:40]
        results: list[PlaceResult] = []
        for i in range(min(spec.max_results, 8)):
            dist_m = rng.uniform(150, spec.radius_m * 0.95)
            bearing = rng.uniform(0, 2 * math.pi)
            dlat = (dist_m * math.cos(bearing)) / 111_320
            dlng = (dist_m * math.sin(bearing)) / (111_320 * math.cos(math.radians(spec.latitude)))
            price = rng.choice([1, 1, 2, 2, 3, 4])
            if spec.max_price_level is not None and price > spec.max_price_level:
                price = spec.max_price_level
            open_now = rng.random() > 0.25
            if spec.open_now and not open_now:
                continue
            pid = f"mock-{seed % 100000}-{i}"
            results.append(
                PlaceResult(
                    place_id=pid,
                    name=f"[DEV MOCK] {label} #{i + 1}",
                    category=label,
                    types=[spec.included_type] if spec.included_type else [],
                    address="Fictional address (mock data)",
                    latitude=spec.latitude + dlat,
                    longitude=spec.longitude + dlng,
                    rating=round(rng.uniform(3.4, 4.8), 1),
                    user_rating_count=rng.randint(15, 4000),
                    price_level=price,
                    open_now=open_now,
                    summary="Mock result for development - not a real business.",
                    maps_url=None,
                    source="mock",
                )
            )
        return results

    async def details(self, place_id: str) -> PlaceResult | None:
        return None


class DisabledPlacesProvider:
    name = "disabled"

    async def search(self, spec: SearchSpec) -> list[PlaceResult]:
        return []

    async def details(self, place_id: str) -> PlaceResult | None:
        return None


_provider: PlacesProvider | None = None


def get_places_provider() -> PlacesProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        kind = s.resolved_places_provider
        if kind == "google":
            if not s.google_maps_api_key:
                raise RuntimeError("PLACES_PROVIDER=google requires GOOGLE_MAPS_API_KEY")
            _provider = GooglePlacesProvider(s)
        elif kind == "mock":
            _provider = MockPlacesProvider()
        else:
            _provider = DisabledPlacesProvider()
        log.info("Places provider: %s", _provider.name)
    return _provider


def set_places_provider(provider: PlacesProvider | None) -> None:
    global _provider
    _provider = provider
