"""Save real OpenStreetMap places around every property into a local snapshot.

The chatbot then answers neighbourhood questions from this file first: instant,
offline, and independent of the busy public Overpass servers.

    docker compose exec backend python -m scripts.build_osm_snapshot              # auto
    docker compose exec backend python -m scripts.build_osm_snapshot --source overpass
    docker compose exec backend python -m scripts.build_osm_snapshot --source extract
    docker compose exec backend python -m scripts.build_osm_snapshot --file /data/osm/delhi.osm.pbf

Sources
* overpass - query the public Overpass servers, retrying patiently when they
             are "at capacity" (rotates servers, backs off up to a few minutes).
* extract  - download an OpenStreetMap extract file covering Delhi (free, no
             account) and read it locally. Tries OSM_EXTRACT_URLS in order.
* --file   - read a .osm.pbf / .osm file you downloaded yourself (put it in the
             app_data volume, e.g. `docker compose cp delhi.osm.pbf backend:/data/osm/`).
* auto     - overpass first, then extract.

Writes <DATA_DIR>/discovery/osm_snapshot.json. Re-run monthly.
Data © OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker, init_db
from app.location.categories import CATEGORIES, matches_category
from app.location.geo import haversine_km
from app.location.providers import ATTRIBUTION, element_to_place, osm_headers
from app.models import Property

DEFAULT_EXTRACT_URLS = (
    "https://download.bbbike.org/osm/bbbike/Delhi/Delhi.osm.pbf,"
    "https://download.geofabrik.de/asia/india/northern-zone-latest.osm.pbf"
)
# Broad selectors; results are filtered locally against the category catalogue.
BROAD = ['["amenity"]', '["shop"]', '["leisure"]', '["tourism"]', '["historic"]', '["healthcare"]',
         '["railway"="station"]', '["highway"="bus_stop"]', '["office"="coworking"]', '["craft"="brewery"]']
PRIMARY_KEYS = ("amenity", "shop", "leisure", "tourism", "historic", "healthcare", "railway", "highway", "office", "craft")


def relevant(tags: dict) -> bool:
    return bool(tags.get("name")) and any(matches_category(tags, c) for c in CATEGORIES)


def near_any(lat: float, lon: float, props: list[Property], radius_km: float) -> bool:
    return any(haversine_km(lat, lon, p.latitude, p.longitude) <= radius_km for p in props)


# ------------------------------------------------------------------ Overpass
async def from_overpass(props: list[Property], radius_km: float, max_wait_s: float = 300) -> dict[str, dict]:
    s = get_settings()
    places: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), headers=osm_headers()) as client:
        for prop in props:
            around = f"(around:{int(radius_km * 1000)},{prop.latitude:.6f},{prop.longitude:.6f})"
            query = "[out:json][timeout:90];\n(\n" + "\n".join(f"  nwr{sel}{around};" for sel in BROAD) + "\n);\nout center tags;"
            deadline, delay, attempt = time.monotonic() + max_wait_s, 10.0, 0
            elements = None
            while elements is None:
                for url in s.overpass_endpoints:
                    attempt += 1
                    print(f"  {prop.name}: asking {httpx.URL(url).host} (attempt {attempt})...", flush=True)
                    try:
                        r = await client.post(url, data={"data": query})
                        if r.status_code == 200:
                            elements = r.json().get("elements", [])
                            break
                        print(f"    HTTP {r.status_code}: {r.text[:100].strip()!r}")
                    except (httpx.HTTPError, ValueError) as exc:
                        print(f"    {type(exc).__name__}: {str(exc)[:100]}")
                if elements is None:
                    if time.monotonic() + delay > deadline:
                        raise RuntimeError("Overpass servers stayed busy; try --source extract or run again later.")
                    print(f"    all servers busy - waiting {delay:.0f}s before retrying", flush=True)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
            kept = 0
            for e in elements:
                p = element_to_place(e, "osm")
                if p and relevant({**p.tags, "name": p.name}):
                    places[p.osm_id or f"{p.name}|{p.lat}|{p.lon}"] = {"osm_id": p.osm_id, "name": p.name, "lat": p.lat, "lon": p.lon, "tags": p.tags}
                    kept += 1
            print(f"    {kept} useful places")
            await asyncio.sleep(5)  # be gentle with the shared servers
    return places


# ------------------------------------------------------------------ extract files
def download_extract(urls: list[str], dest_dir: Path, refresh: bool) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for url in urls:
        dest = dest_dir / url.rsplit("/", 1)[-1]
        if dest.exists() and not refresh and time.time() - dest.stat().st_mtime < 30 * 86400:
            print(f"Using previously downloaded {dest.name}")
            return dest
        print(f"Downloading {url} ...", flush=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with httpx.stream("GET", url, headers=osm_headers(), follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as r:
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code} - trying the next source")
                    continue
                total, done, next_report = int(r.headers.get("content-length", 0)), 0, 0
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if done >= next_report:
                            print(f"  {done / 1e6:.0f} MB" + (f" of {total / 1e6:.0f} MB" if total else ""), flush=True)
                            next_report += 20_000_000
            tmp.replace(dest)
            return dest
        except httpx.HTTPError as exc:
            print(f"  failed ({type(exc).__name__}) - trying the next source")
            tmp.unlink(missing_ok=True)
    raise RuntimeError("Could not download any extract. Download a Delhi .osm.pbf yourself and use --file.")


def from_extract(path: Path, props: list[Property], radius_km: float) -> dict[str, dict]:
    """Two passes over the file so memory stays small even for large regional extracts."""
    import osmium

    pad = radius_km / 111 + 0.01
    lat_min, lat_max = min(p.latitude for p in props) - pad, max(p.latitude for p in props) + pad
    lon_min, lon_max = min(p.longitude for p in props) - pad * 1.2, max(p.longitude for p in props) + pad * 1.2

    places: dict[str, dict] = {}
    way_refs: dict[int, tuple[dict, list[int]]] = {}

    class Pass1(osmium.SimpleHandler):
        def node(self, n):
            if not n.tags or "name" not in n.tags or not any(k in n.tags for k in PRIMARY_KEYS):
                return
            loc = n.location
            if not loc.valid() or not (lat_min <= loc.lat <= lat_max and lon_min <= loc.lon <= lon_max):
                return
            tags = {t.k: t.v for t in n.tags}
            if relevant(tags) and near_any(loc.lat, loc.lon, props, radius_km):
                places[f"node/{n.id}"] = {"osm_id": f"node/{n.id}", "name": tags["name"], "lat": loc.lat, "lon": loc.lon, "tags": tags}

        def way(self, w):
            if not w.tags or "name" not in w.tags or not any(k in w.tags for k in PRIMARY_KEYS):
                return
            tags = {t.k: t.v for t in w.tags}
            if relevant(tags):
                way_refs[w.id] = (tags, [nd.ref for nd in w.nodes][:60])

    print(f"Reading {path.name} (pass 1/2: places)...", flush=True)
    Pass1().apply_file(str(path))
    needed = {ref for _, refs in way_refs.values() for ref in refs}
    coords: dict[int, tuple[float, float]] = {}

    class Pass2(osmium.SimpleHandler):
        def node(self, n):
            if n.id in needed and n.location.valid():
                coords[n.id] = (n.location.lat, n.location.lon)

    if needed:
        print(f"Reading {path.name} (pass 2/2: building outlines)...", flush=True)
        Pass2().apply_file(str(path))
    for wid, (tags, refs) in way_refs.items():
        pts = [coords[r] for r in refs if r in coords]
        if not pts:
            continue
        lat, lon = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        if near_any(lat, lon, props, radius_km):
            places[f"way/{wid}"] = {"osm_id": f"way/{wid}", "name": tags["name"], "lat": lat, "lon": lon, "tags": tags}
    return places


# ------------------------------------------------------------------ main
async def main(source: str, file: str | None, radius_km: float, refresh: bool) -> None:
    s = get_settings()
    await init_db()
    async with get_sessionmaker()() as db:
        props = [p for p in (await db.execute(select(Property).where(Property.is_active.is_(True)))).scalars().unique()
                 if p.latitude is not None and p.longitude is not None]
    await dispose_engine()
    if not props:
        raise SystemExit("No active properties with coordinates.")
    print(f"Building a map snapshot for {len(props)} properties ({radius_km:g} km around each).")

    places: dict[str, dict] | None = None
    used = ""
    if file:
        places, used = from_extract(Path(file), props, radius_km), f"file {Path(file).name}"
    else:
        if source in ("auto", "overpass"):
            try:
                places, used = await from_overpass(props, radius_km), "Overpass"
            except RuntimeError as exc:
                print(f"Overpass: {exc}")
                if source == "overpass":
                    raise SystemExit(1) from exc
        if places is None:
            urls = [u.strip() for u in os.environ.get("OSM_EXTRACT_URLS", DEFAULT_EXTRACT_URLS).split(",") if u.strip()]
            path = download_extract(urls, s.data_path / "osm", refresh)
            places, used = from_extract(path, props, radius_km), f"extract {path.name}"

    out_dir = s.data_path / "discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "osm_snapshot.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": used,
        "attribution": ATTRIBUTION,
        "license": "ODbL 1.0 - https://www.openstreetmap.org/copyright",
        "radius_km": radius_km,
        "properties": [p.name for p in props],
        "places": list(places.values()),
    }, ensure_ascii=False))
    print(f"\nSaved {len(places)} places (from {used}) to {out}.")
    print("Restart the backend to use it: docker compose restart backend")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "overpass", "extract"], default="auto")
    ap.add_argument("--file", help="Read this .osm.pbf/.osm file instead of downloading")
    ap.add_argument("--radius-km", type=float, default=10.0)
    ap.add_argument("--refresh", action="store_true", help="Re-download the extract even if a recent copy exists")
    a = ap.parse_args()
    asyncio.run(main(a.source, a.file, a.radius_km, a.refresh))
