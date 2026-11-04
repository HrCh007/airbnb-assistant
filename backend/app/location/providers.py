"""Local discovery data sources (all free, no API keys).

* OverpassProvider - live OpenStreetMap data from the public Overpass API.
  Respects the usage policy: identifying User-Agent, one request at a time,
  results cached, and a cool-down after failures.
* DatasetProvider  - local JSON datasets in data/discovery/:
    - osm_snapshot.json : real OSM data you download once with
                          `python -m scripts.build_osm_snapshot` (source "osm_snapshot")
    - sample_places.json: FICTIONAL places for development (source "sample")

Data © OpenStreetMap contributors, available under the ODbL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.location.categories import Category, matches_category, tags_match
from app.location.geo import haversine_km

log = logging.getLogger(__name__)

ATTRIBUTION = "Map data © OpenStreetMap contributors (ODbL)"


class DiscoveryError(Exception):
    pass


@dataclass
class RawPlace:
    """A place before ranking: OSM-style tags plus where it came from."""

    osm_id: str | None
    name: str
    lat: float
    lon: float
    tags: dict
    source: str  # osm / osm_snapshot / sample
    extra: dict = field(default_factory=dict)


@dataclass
class SearchSpec:
    latitude: float
    longitude: float
    radius_m: int
    category: Category | None = None
    keyword: str | None = None  # free-text fallback ("tibetan", "rooftop")
    limit: int = 80


def _sanitise_keyword(kw: str) -> str:
    kw = re.sub(r"[^a-zA-Z0-9 \-]", " ", kw).strip()
    return re.escape(kw)[:40]


def keyword_selectors(keyword: str) -> tuple[str, ...]:
    kw = _sanitise_keyword(keyword)
    if not kw:
        return ()
    return tuple(
        f'["{key}"]["name"~"{kw}",i]' for key in ("amenity", "shop", "leisure", "tourism")
    ) + (f'["cuisine"~"{kw}",i]',)


# ------------------------------------------------------------------ Overpass (live)
class OverpassProvider:
    name = "osm"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(s.discovery_timeout_seconds + 5, connect=5.0),
            headers={"User-Agent": s.osm_user_agent, "Accept": "application/json"},
        )
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    def build_query(self, spec: SearchSpec) -> str:
        s = get_settings()
        selectors = list(spec.category.osm) if spec.category else []
        if spec.keyword:
            selectors += list(keyword_selectors(spec.keyword))
        if not selectors:
            raise DiscoveryError("Nothing to search for.")
        around = f"(around:{int(spec.radius_m)},{spec.latitude:.6f},{spec.longitude:.6f})"
        body = "\n".join(f"  nwr{sel}{around};" for sel in selectors)
        timeout = int(s.discovery_timeout_seconds)
        return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags {spec.limit};"

    async def search(self, spec: SearchSpec) -> list[RawPlace]:
        query = self.build_query(spec)
        async with self._lock:  # one request at a time (fair use)
            wait = 1.0 - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = await self._client.post(get_settings().overpass_url, data={"data": query})
            except httpx.HTTPError as exc:
                raise DiscoveryError(f"Overpass unreachable ({type(exc).__name__})") from exc
        if resp.status_code == 429:
            raise DiscoveryError("Overpass rate limit reached")
        if resp.status_code >= 400:
            raise DiscoveryError(f"Overpass error {resp.status_code}")
        try:
            elements = resp.json().get("elements", [])
        except ValueError as exc:
            raise DiscoveryError("Overpass returned invalid JSON") from exc
        return [p for p in (element_to_place(e, "osm") for e in elements) if p is not None]


def element_to_place(e: dict, source: str) -> RawPlace | None:
    tags = e.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    lat = e.get("lat", (e.get("center") or {}).get("lat"))
    lon = e.get("lon", (e.get("center") or {}).get("lon"))
    if not name or lat is None or lon is None:
        return None
    osm_id = f"{e['type']}/{e['id']}" if e.get("type") and e.get("id") else None
    return RawPlace(osm_id=osm_id, name=str(name)[:200], lat=float(lat), lon=float(lon), tags=tags, source=source)


# ------------------------------------------------------------------ local datasets
class DatasetProvider:
    """Searches a local JSON dataset with the same selectors as Overpass."""

    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.name = source
        self._places: list[RawPlace] | None = None
        self.meta: dict = {}

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> list[RawPlace]:
        if self._places is None:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.meta = {k: v for k, v in data.items() if k != "places"}
            places = []
            for item in data.get("places", []):
                tags = dict(item.get("tags") or {})
                if item.get("name") and not tags.get("name"):
                    tags["name"] = item["name"]
                p = element_to_place({"lat": item.get("lat"), "lon": item.get("lon"), "tags": tags}, self.name)
                if p:
                    p.osm_id = item.get("osm_id")
                    places.append(p)
            self._places = places
        return self._places

    async def search(self, spec: SearchSpec) -> list[RawPlace]:
        if not self.available:
            return []
        places = await asyncio.to_thread(self._load)
        kw_sel = keyword_selectors(spec.keyword) if spec.keyword else ()
        out = []
        for p in places:
            if haversine_km(spec.latitude, spec.longitude, p.lat, p.lon) * 1000 > spec.radius_m:
                continue
            tags = {**p.tags, "name": p.name}
            if (spec.category and matches_category(tags, spec.category)) or any(tags_match(tags, s) for s in kw_sel):
                out.append(p)
        return out[: spec.limit]


_overpass: OverpassProvider | None = None
_snapshot: DatasetProvider | None = None
_sample: DatasetProvider | None = None


def get_overpass() -> OverpassProvider:
    global _overpass
    if _overpass is None:
        _overpass = OverpassProvider()
    return _overpass


def get_snapshot() -> DatasetProvider:
    global _snapshot
    if _snapshot is None:
        s = get_settings()
        # Prefer the copy in the persistent data volume (written by build_osm_snapshot).
        path = s.data_path / "discovery" / "osm_snapshot.json"
        if not path.is_file():
            path = s.bundled_path / "discovery" / "osm_snapshot.json"
        _snapshot = DatasetProvider(path, "osm_snapshot")
    return _snapshot


def get_sample() -> DatasetProvider:
    global _sample
    if _sample is None:
        _sample = DatasetProvider(get_settings().bundled_path / "discovery" / "sample_places.json", "sample")
    return _sample


def set_providers(overpass=None, snapshot=None, sample=None) -> None:
    """Override (tests). Pass None to reset to defaults."""
    global _overpass, _snapshot, _sample
    _overpass, _snapshot, _sample = overpass, snapshot, sample
