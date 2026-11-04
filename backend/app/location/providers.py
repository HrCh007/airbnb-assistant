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
import math
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


def osm_headers() -> dict:
    """Identify the app clearly (OSM usage policies) and look like a normal HTTP client."""
    return {
        "User-Agent": get_settings().osm_user_agent,
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }


# ------------------------------------------------------------------ Overpass (live)
class OverpassProvider:
    name = "osm"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(s.discovery_timeout_seconds + 3, connect=5.0), headers=osm_headers()
        )
        self._lock = asyncio.Lock()
        self._last_request: dict[str, float] = {}  # per server: at most 1 request/second
        self._down_until: dict[str, float] = {}  # per server: skip after a failure

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

    async def _query(self, url: str, query: str) -> list[dict]:
        try:
            resp = await self._client.post(url, data={"data": query})
            if resp.status_code == 406:
                # Some front ends reject form POSTs or header combinations: retry as GET.
                resp = await self._client.get(url, params={"data": query})
        except httpx.TimeoutException as exc:
            raise DiscoveryError(f"{_host(url)}: timed out") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryError(f"{_host(url)}: unreachable ({type(exc).__name__}: {str(exc)[:120]})") from exc
        if resp.status_code == 406:
            raise DiscoveryError(
                f"{_host(url)}: HTTP 406 (request refused - set OSM_USER_AGENT to your app name and a real contact email)"
            )
        if resp.status_code >= 400:
            raise DiscoveryError(f"{_host(url)}: HTTP {resp.status_code} {_remark(resp.text)}".strip())
        try:
            data = resp.json()
        except ValueError as exc:
            raise DiscoveryError(f"{_host(url)}: not JSON ({_remark(resp.text)})") from exc
        if data.get("remark") and "runtime error" in str(data["remark"]).lower():
            raise DiscoveryError(f"{_host(url)}: {str(data['remark'])[:160]}")
        return data.get("elements", [])

    async def search(self, spec: SearchSpec) -> list[RawPlace]:
        """Try each configured Overpass server in turn; raise with every reason if all fail."""
        query = self.build_query(spec)
        errors: list[str] = []
        s = get_settings()
        async with self._lock:  # one request at a time (fair use)
            urls = s.overpass_endpoints
            live = [u for u in urls if time.monotonic() >= self._down_until.get(u, 0.0)] or urls
            started = time.monotonic()
            for url in live:
                if errors and time.monotonic() - started > s.discovery_total_budget_seconds:
                    errors.append("time budget for Overpass servers used up")
                    break
                wait = 1.0 - (time.monotonic() - self._last_request.get(url, 0.0))
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request[url] = time.monotonic()
                try:
                    elements = await self._query(url, query)
                except DiscoveryError as exc:
                    errors.append(str(exc))
                    self._down_until[url] = time.monotonic() + s.discovery_failure_cooldown_seconds * 5
                    log.warning("Overpass server failed - %s", exc)
                    continue
                self._down_until.pop(url, None)
                HEALTH.record_ok(url)
                return [p for p in (element_to_place(e, "osm") for e in elements) if p is not None]
        HEALTH.record_error("; ".join(errors) or "no Overpass servers configured")
        raise DiscoveryError("; ".join(errors) or "no Overpass servers configured")


def _host(url: str) -> str:
    return httpx.URL(url).host or url


def _remark(text: str) -> str:
    """Overpass error pages are HTML with the reason in a <p> or 'remark'; keep it short."""
    m = re.search(r"<strong[^>]*>Error</strong>:?(.*?)</p>", text, re.S | re.I)
    msg = m.group(1) if m else text
    return re.sub(r"<[^>]+>|\s+", " ", msg).strip()[:160]


@dataclass
class LiveHealth:
    """Last outcome of live OpenStreetMap searches (shown in /api/status and the dashboard)."""

    last_ok_at: float | None = None
    last_ok_server: str | None = None
    last_error_at: float | None = None
    last_error: str | None = None

    def record_ok(self, url: str) -> None:
        self.last_ok_at, self.last_ok_server = time.time(), _host(url)

    def record_error(self, error: str) -> None:
        self.last_error_at, self.last_error = time.time(), error[:500]

    def as_dict(self) -> dict:
        return {
            "last_ok_at": self.last_ok_at, "last_ok_server": self.last_ok_server,
            "last_error_at": self.last_error_at, "last_error": self.last_error,
        }


HEALTH = LiveHealth()


def element_to_place(e: dict, source: str) -> RawPlace | None:
    tags = e.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    lat = e.get("lat", (e.get("center") or {}).get("lat"))
    lon = e.get("lon", (e.get("center") or {}).get("lon"))
    if not name or lat is None or lon is None:
        return None
    osm_id = f"{e['type']}/{e['id']}" if e.get("type") and e.get("id") else None
    return RawPlace(osm_id=osm_id, name=str(name)[:200], lat=float(lat), lon=float(lon), tags=tags, source=source)


# ------------------------------------------------------------------ Nominatim (live fallback)
class NominatimProvider:
    """Fallback live source: OpenStreetMap's own search service (nominatim.openstreetmap.org).

    Searches a category phrase ("cafe", "pharmacy") inside a box around the
    property. Fewer details than Overpass (max 40 results, tags via extratags),
    but it answers when the public Overpass servers are busy. Usage policy:
    at most 1 request per second, identifying User-Agent - both enforced here.
    """

    name = "osm"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0), headers=osm_headers())
        self._lock = asyncio.Lock()
        self._last = 0.0

    @staticmethod
    def phrase(spec: SearchSpec) -> str | None:
        if spec.keyword:
            return spec.keyword
        if spec.category:
            return spec.category.label.replace("-", " ")
        return None

    async def search(self, spec: SearchSpec) -> list[RawPlace]:
        q = self.phrase(spec)
        if not q:
            raise DiscoveryError("nominatim: nothing to search for")
        dlat = spec.radius_m / 111_320
        dlon = spec.radius_m / (111_320 * max(0.2, math.cos(math.radians(spec.latitude))))
        params = {
            "q": q, "format": "jsonv2", "limit": 40, "bounded": 1, "extratags": 1, "countrycodes": "in",
            "viewbox": f"{spec.longitude - dlon:.6f},{spec.latitude + dlat:.6f},{spec.longitude + dlon:.6f},{spec.latitude - dlat:.6f}",
        }
        url = f"{get_settings().nominatim_url.rstrip('/')}/search"
        async with self._lock:
            wait = 1.1 - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TimeoutException as exc:
                raise DiscoveryError("nominatim: timed out") from exc
            except httpx.HTTPError as exc:
                raise DiscoveryError(f"nominatim: unreachable ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise DiscoveryError(f"nominatim: HTTP {resp.status_code}")
        try:
            items = resp.json()
        except ValueError as exc:
            raise DiscoveryError("nominatim: not JSON") from exc
        out = []
        for it in items:
            name = it.get("name") or (it.get("display_name") or "").split(",")[0]
            if not name or it.get("lat") is None:
                continue
            tags = {**(it.get("extratags") or {}), "name": name}
            if it.get("category") and it.get("type"):
                tags.setdefault(it["category"], it["type"])
            if it.get("display_name") and "addr:full" not in tags:
                tags["addr:full"] = ", ".join(x.strip() for x in it["display_name"].split(",")[1:4])
            osm_id = f"{it['osm_type']}/{it['osm_id']}" if it.get("osm_type") and it.get("osm_id") else None
            out.append(RawPlace(osm_id=osm_id, name=name[:200], lat=float(it["lat"]), lon=float(it["lon"]), tags=tags, source="osm"))
        HEALTH.record_ok(url)
        return out


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
_nominatim: NominatimProvider | None = None
_snapshot: DatasetProvider | None = None
_sample: DatasetProvider | None = None


def get_overpass() -> OverpassProvider:
    global _overpass
    if _overpass is None:
        _overpass = OverpassProvider()
    return _overpass


def get_nominatim() -> NominatimProvider:
    global _nominatim
    if _nominatim is None:
        _nominatim = NominatimProvider()
    return _nominatim


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


def set_providers(overpass=None, snapshot=None, sample=None, nominatim=None) -> None:
    """Override (tests). Pass None to reset to defaults."""
    global _overpass, _snapshot, _sample, _nominatim
    _overpass, _snapshot, _sample, _nominatim = overpass, snapshot, sample, nominatim
