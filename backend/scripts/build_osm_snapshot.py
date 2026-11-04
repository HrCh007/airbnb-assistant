"""Download real OpenStreetMap places around every property into a local snapshot,
so neighbourhood recommendations keep working offline / when Overpass is busy.

    docker compose exec backend python -m scripts.build_osm_snapshot [--radius-km 5]

Writes <DATA_DIR>/discovery/osm_snapshot.json (persistent volume). One Overpass
request per property, with a pause between them (fair use). Re-run monthly.
Data © OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker, init_db
from app.location.categories import CATEGORIES, Category
from app.location.providers import ATTRIBUTION, OverpassProvider, SearchSpec
from app.models import Property


async def main(radius_km: float) -> None:
    s = get_settings()
    await init_db()
    async with get_sessionmaker()() as db:
        props = [p for p in (await db.execute(select(Property).where(Property.is_active.is_(True)))).scalars().unique()
                 if p.latitude is not None and p.longitude is not None]
    if not props:
        raise SystemExit("No active properties with coordinates.")
    selectors = tuple(dict.fromkeys(sel for c in CATEGORIES for sel in c.osm))
    everything = Category("all", "all", "all", selectors, ())
    provider = OverpassProvider()
    places: dict[str, dict] = {}
    for i, prop in enumerate(props):
        if i:
            await asyncio.sleep(5)  # be gentle with the public server
        spec = SearchSpec(latitude=prop.latitude, longitude=prop.longitude, radius_m=int(radius_km * 1000), category=everything, limit=5000)
        print(f"Querying OpenStreetMap around {prop.name} ({radius_km:g} km)...", flush=True)
        found = await provider.search(spec)
        for p in found:
            places[p.osm_id or f"{p.name}|{p.lat}|{p.lon}"] = {"osm_id": p.osm_id, "name": p.name, "lat": p.lat, "lon": p.lon, "tags": p.tags}
        print(f"  {len(found)} places")
    out_dir = s.data_path / "discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "osm_snapshot.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "license": "ODbL 1.0 - https://www.openstreetmap.org/copyright",
        "radius_km": radius_km,
        "properties": [p.name for p in props],
        "places": list(places.values()),
    }, ensure_ascii=False))
    print(f"Saved {len(places)} places to {out}. Restart the backend to load it.")
    await dispose_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius-km", type=float, default=5.0)
    asyncio.run(main(ap.parse_args().radius_km))
