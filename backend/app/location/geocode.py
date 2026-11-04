"""Address -> coordinates via OpenStreetMap Nominatim (admin use only).

Nominatim's usage policy: max 1 request/second, an identifying User-Agent, no
bulk geocoding. This is only called when the host clicks "Find coordinates".
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import get_settings

_lock = asyncio.Lock()
_last = 0.0


class GeocodeError(Exception):
    pass


async def geocode(address: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    global _last
    s = get_settings()
    params = {"q": address, "format": "jsonv2", "limit": 5, "countrycodes": "in", "addressdetails": 0}
    async with _lock:
        wait = 1.1 - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        try:
            c = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": s.osm_user_agent})
            r = await c.get(f"{s.nominatim_url.rstrip('/')}/search", params=params)
        except httpx.HTTPError as exc:
            raise GeocodeError("The OpenStreetMap geocoder is unreachable.") from exc
    if r.status_code >= 400:
        raise GeocodeError(f"Geocoder error {r.status_code}")
    return [
        {"display_name": i.get("display_name"), "latitude": float(i["lat"]), "longitude": float(i["lon"])}
        for i in r.json()
        if i.get("lat") and i.get("lon")
    ]
