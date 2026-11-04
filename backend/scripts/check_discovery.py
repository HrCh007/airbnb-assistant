"""Diagnose live OpenStreetMap search from inside the backend container.

    docker compose exec backend python -m scripts.check_discovery

Tries every configured Overpass server with a small query (cafes within 1 km
of the first property) and prints the result or the exact error.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker, init_db
from app.location.categories import category_by_key
from app.location.providers import DiscoveryError, NominatimProvider, OverpassProvider, SearchSpec
from app.models import Property


async def main() -> None:
    s = get_settings()
    await init_db()
    async with get_sessionmaker()() as db:
        prop = next((p for p in (await db.execute(select(Property))).scalars().unique() if p.latitude is not None), None)
    lat, lon = (prop.latitude, prop.longitude) if prop else (28.5245, 77.2066)
    print(f"Live search enabled: {s.discovery_live} | sample data fallback: {s.discovery_sample_data}")
    print(f"User-Agent: {s.osm_user_agent}")
    if "example.com" in s.osm_user_agent or "set OSM_USER_AGENT" in s.osm_user_agent:
        print("  WARNING: the User-Agent still has placeholder contact details. Some OpenStreetMap servers refuse")
        print("  such requests (HTTP 406). Set OSM_USER_AGENT in .env, e.g.:")
        print("  OSM_USER_AGENT=HideoutStudiosGuestAssistant/1.0 (contact: your.real.email@gmail.com)")
    print(f"Test origin: {prop.name if prop else 'Saket'} ({lat:.5f}, {lon:.5f})\n")

    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://www.openstreetmap.org/api/capabilities")
            print(f"Internet / openstreetmap.org: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            print(f"Internet / openstreetmap.org: FAILED ({type(exc).__name__}: {exc}) - check Docker's internet access")

    spec = SearchSpec(latitude=lat, longitude=lon, radius_m=1000, category=category_by_key("cafe"), limit=20)
    ok = False
    for url in s.overpass_endpoints:
        provider = OverpassProvider()
        t0 = time.perf_counter()
        try:
            elements = await provider._query(url, provider.build_query(spec))
            named = [e.get("tags", {}).get("name") for e in elements if e.get("tags", {}).get("name")]
            print(f"OK    {url}  ({time.perf_counter() - t0:.1f}s) {len(named)} named cafes, e.g. {named[:3]}")
            ok = True
        except DiscoveryError as exc:
            print(f"FAIL  {url}  ({time.perf_counter() - t0:.1f}s) {exc}")
    t0 = time.perf_counter()
    try:
        found = await NominatimProvider().search(spec)
        print(f"OK    Nominatim fallback  ({time.perf_counter() - t0:.1f}s) {len(found)} cafes, e.g. {[p.name for p in found[:3]]}")
        nominatim_ok = True
    except DiscoveryError as exc:
        print(f"FAIL  Nominatim fallback  ({time.perf_counter() - t0:.1f}s) {exc}")
        nominatim_ok = False
    if ok:
        print("\nResult: live search works (Overpass).")
    elif nominatim_ok:
        print("\nResult: Overpass is unavailable, but the Nominatim fallback works - guests will still get real places.")
    else:
        print("\nResult: no live source answered. Fix OSM_USER_AGENT (see above), try again later, or add servers to OVERPASS_URLS.")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
