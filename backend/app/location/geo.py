"""Geographic helpers."""

from __future__ import annotations

import math
from urllib.parse import quote_plus, urlencode

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def directions_url(
    origin_lat: float | None,
    origin_lng: float | None,
    name: str,
    address: str | None = None,
    place_id: str | None = None,
    travel_mode: str | None = None,
) -> str:
    params: dict[str, str] = {"api": "1", "destination": f"{name}, {address}" if address else name}
    if origin_lat is not None and origin_lng is not None:
        params["origin"] = f"{origin_lat:.6f},{origin_lng:.6f}"
    if place_id:
        params["destination_place_id"] = place_id
    if travel_mode in {"driving", "walking", "bicycling", "transit"}:
        params["travelmode"] = travel_mode
    return "https://www.google.com/maps/dir/?" + urlencode(params, quote_via=quote_plus)


def search_url(name: str, address: str | None = None, place_id: str | None = None) -> str:
    params = {"api": "1", "query": f"{name}, {address}" if address else name}
    if place_id:
        params["query_place_id"] = place_id
    return "https://www.google.com/maps/search/?" + urlencode(params, quote_via=quote_plus)


def osm_url(osm_id: str | None, lat: float | None = None, lon: float | None = None) -> str | None:
    if osm_id and "/" in osm_id:
        return f"https://www.openstreetmap.org/{osm_id}"
    if lat is not None and lon is not None:
        return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=18/{lat:.6f}/{lon:.6f}"
    return None


def coords_directions_url(origin_lat: float, origin_lng: float, lat: float, lon: float, travel_mode: str | None = None) -> str:
    """Google Maps directions *link* (opens the app/website; no API key involved)."""
    params = {"api": "1", "origin": f"{origin_lat:.6f},{origin_lng:.6f}", "destination": f"{lat:.6f},{lon:.6f}"}
    if travel_mode in {"driving", "walking", "bicycling", "transit"}:
        params["travelmode"] = travel_mode
    return "https://www.google.com/maps/dir/?" + urlencode(params, quote_via=quote_plus)


def format_distance(km: float | None) -> str | None:
    if km is None:
        return None
    if km < 1:
        return f"{int(round(km * 1000, -1))} m"
    return f"{km:.1f} km"
