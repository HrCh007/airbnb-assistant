"""Unit tests: geo maths, OSM selectors, opening hours, ranking, Overpass client
(mocked HTTP transport - no network), local datasets, geocoder."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.location import opening_hours
from app.location.categories import category_by_key, match_category, matches_category, tags_match
from app.location.discovery import _dedupe, to_place_result
from app.location.geo import coords_directions_url, format_distance, haversine_km, osm_url
from app.location.providers import DatasetProvider, DiscoveryError, OverpassProvider, RawPlace, SearchSpec, element_to_place
from app.location.ranking import RankingContext, rank_places
from app.schemas.common import PlaceResult


# ------------------------------------------------------------------ geo
def test_haversine_known_distance():
    # Connaught Place -> India Gate is roughly 2.3-2.5 km.
    assert 2.2 < haversine_km(28.6315, 77.2167, 28.6129, 77.2295) < 2.6
    assert haversine_km(28.5, 77.2, 28.5, 77.2) == 0


@pytest.mark.parametrize("km,expected", [(0.234, "230 m"), (1.26, "1.3 km"), (None, None)])
def test_format_distance(km, expected):
    assert format_distance(km) == expected


def test_navigation_links_use_exact_coordinates():
    url = coords_directions_url(28.524512, 77.206612, 28.53, 77.21, "walking")
    assert "origin=28.524512%2C77.206612" in url and "destination=28.530000%2C77.210000" in url and "travelmode=walking" in url
    assert osm_url("node/123") == "https://www.openstreetmap.org/node/123"


# ------------------------------------------------------------------ categories & selectors
@pytest.mark.parametrize(
    "text,key",
    [("Is there parking?", None), ("good momos", "momos"), ("rooftop bar", "lounge"), ("pharmacy", "pharmacy"),
     ("parks nearby", "park"), ("work on my laptop", "work_cafe"), ("watch the cricket match", "sports_bar")],
)
def test_category_matching(text, key):
    cat = match_category(text)
    assert (cat.key if cat else None) == key


def test_selector_matching():
    assert tags_match({"amenity": "cafe"}, '["amenity"="cafe"]')
    assert not tags_match({"amenity": "bar"}, '["amenity"="cafe"]')
    assert tags_match({"amenity": "restaurant", "cuisine": "North_Indian;chinese"}, '["amenity"="restaurant"]["cuisine"~"indian",i]')
    assert not tags_match({"amenity": "restaurant"}, '["amenity"="restaurant"]["cuisine"~"indian",i]')
    assert tags_match({"amenity": "pub", "sport": "cricket"}, '["amenity"~"bar|pub"]["sport"]')
    assert matches_category({"shop": "chemist"}, category_by_key("pharmacy"))


# ------------------------------------------------------------------ opening hours
MON_8PM = datetime(2026, 11, 2, 20, 0, tzinfo=opening_hours.DELHI)  # Monday
MON_1AM = datetime(2026, 11, 2, 1, 0, tzinfo=opening_hours.DELHI)
SUN_1AM = datetime(2026, 11, 1, 1, 0, tzinfo=opening_hours.DELHI)


@pytest.mark.parametrize(
    "value,now,open_now,late",
    [
        ("24/7", MON_8PM, True, True),
        ("Mo-Su 10:00-22:00", MON_8PM, True, False),
        ("Mo-Fr 09:00-18:00; Sa 10:00-14:00", MON_8PM, False, False),
        ("Mo-Su 18:00-02:00", MON_1AM, True, True),  # Sunday's late shift spills into Monday
        ("Th-Su 21:00-03:00", MON_1AM, True, True),
        ("Th-Su 21:00-03:00", datetime(2026, 11, 3, 1, 0, tzinfo=opening_hours.DELHI), False, True),  # Tue 1am
        ("Mo-Sa 11:00-15:00,19:00-23:30", MON_8PM, True, True),
        ("Mo-Sa 09:00-21:00; Su off", SUN_1AM, False, False),
        ("sunrise-sunset", MON_8PM, None, None),
        (None, MON_8PM, None, None),
    ],
)
def test_opening_hours(value, now, open_now, late):
    st = opening_hours.status(value, now)
    assert (st.open_now, st.late_night) == (open_now, late)


# ------------------------------------------------------------------ OSM element -> place
def test_element_conversion_and_derived_fields():
    raw = element_to_place(
        {"type": "way", "id": 42, "center": {"lat": 28.53, "lon": 77.21},
         "tags": {"name": "Haldiram's", "amenity": "fast_food", "cuisine": "indian;sweets", "diet:vegetarian": "only",
                  "opening_hours": "Mo-Su 08:00-23:00", "addr:housenumber": "12", "addr:street": "Main Road", "addr:city": "New Delhi"}},
        "osm",
    )
    assert raw.osm_id == "way/42"
    p = to_place_result(raw, 28.5245, 77.2066, None)
    assert p.price_level == 1 and "budget" in p.price_note.lower()
    assert p.diet == "Pure vegetarian" and p.cuisine == "Indian, Sweets"
    assert p.address == "12 Main Road, New Delhi"
    assert p.osm_url == "https://www.openstreetmap.org/way/42" and p.directions_url
    assert 0.5 < p.distance_km < 1.0
    assert p.rating is None  # OSM has no ratings: never invented
    assert element_to_place({"type": "node", "id": 1, "lat": 1, "lon": 2, "tags": {"amenity": "cafe"}}, "osm") is None  # unnamed


def test_sample_places_have_no_links_and_are_labelled():
    raw = RawPlace(osm_id=None, name="[Sample] X", lat=28.53, lon=77.21, tags={"amenity": "cafe"}, source="sample")
    p = to_place_result(raw, 28.5245, 77.2066, None)
    assert p.source == "sample" and p.directions_url is None and p.osm_url is None


def test_dedupe_node_and_way_of_same_venue():
    a = PlaceResult(name="Cafe X", osm_id="node/1", latitude=28.5, longitude=77.2)
    b = PlaceResult(name="Cafe X", osm_id="way/2", latitude=28.5001, longitude=77.2)
    c = PlaceResult(name="Cafe X", osm_id="node/3", latitude=28.6, longitude=77.2)  # different branch, far away
    assert [p.osm_id for p in _dedupe([a, b, c])] == ["node/1", "node/3"]


# ------------------------------------------------------------------ ranking
def _p(name, dist, **kw):
    return PlaceResult(name=name, distance_km=dist, **kw)


def test_ranking_prefers_relevant_and_close():
    s = Settings()
    ranked = rank_places([(_p("Far match", 2.8), 1.0), (_p("Near match", 0.3), 1.0), (_p("Near weak", 0.3), 0.3)], RankingContext("cafe", 3), s)
    assert ranked[0].name == "Near match"


def test_ranking_budget_and_open_now():
    s = Settings(rank_weight_preference=0.5)
    cheap = _p("Cheap", 1, price_level=1)
    other = _p("Unknown price", 1)
    assert rank_places([(other, 1.0), (cheap, 1.0)], RankingContext("x", 3, budget="budget"), s)[0].name == "Cheap"
    closed = _p("Closed", 0.5, open_now=False)
    open_ = _p("Open", 0.5, open_now=True)
    assert rank_places([(closed, 1.0), (open_, 1.0)], RankingContext("x", 3), s)[0].name == "Open"


# ------------------------------------------------------------------ Overpass client (mocked HTTP)
OVERPASS_JSON = {
    "elements": [
        {"type": "node", "id": 1, "lat": 28.526, "lon": 77.207, "tags": {"name": "Real Cafe", "amenity": "cafe"}},
        {"type": "way", "id": 2, "center": {"lat": 28.527, "lon": 77.208}, "tags": {"name": "Big Cafe", "amenity": "cafe"}},
        {"type": "node", "id": 3, "lat": 28.528, "lon": 77.209, "tags": {"amenity": "cafe"}},  # unnamed -> skipped
    ]
}


async def test_overpass_query_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        seen["query"] = dict(httpx.QueryParams(request.content.decode()))["data"]
        return httpx.Response(200, json=OVERPASS_JSON)

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "test-agent/1.0"}))
    results = await provider.search(SearchSpec(latitude=28.5245, longitude=77.2066, radius_m=3000, category=category_by_key("cafe")))
    assert seen["url"] == "https://overpass-api.de/api/interpreter"
    assert seen["ua"] == "test-agent/1.0"
    q = seen["query"]
    assert q.startswith("[out:json][timeout:")
    assert 'nwr["amenity"="cafe"](around:3000,28.524500,77.206600);' in q
    assert "out center tags" in q
    assert [r.name for r in results] == ["Real Cafe", "Big Cafe"]


async def test_overpass_keyword_query_is_sanitised():
    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"elements": []}))))
    q = provider.build_query(SearchSpec(latitude=1, longitude=2, radius_m=500, keyword='tibetan"];out;(evil'))
    assert '"];out' not in q and "evil" in q  # injection characters stripped


@pytest.mark.parametrize("status", [429, 504, 400])
async def test_overpass_errors_raise(status):
    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))))
    with pytest.raises(DiscoveryError):
        await provider.search(SearchSpec(latitude=1, longitude=2, radius_m=500, category=category_by_key("cafe")))


async def test_overpass_timeout_raises():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(DiscoveryError):
        await provider.search(SearchSpec(latitude=1, longitude=2, radius_m=500, category=category_by_key("cafe")))


# ------------------------------------------------------------------ local datasets
async def test_dataset_provider_filters_by_radius_and_category(tmp_path: Path):
    data = {"places": [
        {"osm_id": "node/1", "name": "Near Pharmacy", "lat": 28.5250, "lon": 77.2066, "tags": {"amenity": "pharmacy"}},
        {"osm_id": "node/2", "name": "Far Pharmacy", "lat": 28.70, "lon": 77.2066, "tags": {"amenity": "pharmacy"}},
        {"osm_id": "node/3", "name": "Near Cafe", "lat": 28.5250, "lon": 77.2066, "tags": {"amenity": "cafe"}},
    ]}
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(data))
    provider = DatasetProvider(f, "osm_snapshot")
    res = await provider.search(SearchSpec(latitude=28.5245, longitude=77.2066, radius_m=1000, category=category_by_key("pharmacy")))
    assert [p.name for p in res] == ["Near Pharmacy"] and res[0].source == "osm_snapshot"
    kw = await provider.search(SearchSpec(latitude=28.5245, longitude=77.2066, radius_m=1000, keyword="cafe"))
    assert [p.name for p in kw] == ["Near Cafe"]


def test_bundled_sample_dataset_is_clearly_fictional():
    data = json.loads((Path(__file__).resolve().parents[3] / "data" / "discovery" / "sample_places.json").read_text())
    assert "FICTIONAL" in data["_comment"]
    assert data["places"] and all(p["name"].startswith("[Sample]") for p in data["places"])
    assert all(p["osm_id"] is None for p in data["places"])


# ------------------------------------------------------------------ geocoder
async def test_geocoder_sends_user_agent_and_parses():
    from app.location.geocode import geocode

    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[{"display_name": "Saket, Delhi", "lat": "28.52", "lon": "77.21"}])

    res = await geocode("Saket, New Delhi", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert res == [{"display_name": "Saket, Delhi", "latitude": 28.52, "longitude": 77.21}]
    assert seen["params"]["countrycodes"] == "in" and seen["params"]["format"] == "jsonv2"


async def test_overpass_tries_next_server_and_reports_reasons(monkeypatch):
    from app.core.config import get_settings
    from app.location.providers import HEALTH

    monkeypatch.setattr(get_settings(), "overpass_url", "")
    monkeypatch.setattr(get_settings(), "overpass_urls", "https://a.example/api/interpreter,https://b.example/api/interpreter")
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "a.example":
            return httpx.Response(504, text="<p><strong>Error</strong>: runtime error: Query timed out</p>")
        return httpx.Response(200, json=OVERPASS_JSON)

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await provider.search(SearchSpec(latitude=28.5245, longitude=77.2066, radius_m=1000, category=category_by_key("cafe")))
    assert hosts == ["a.example", "b.example"] and [r.name for r in res] == ["Real Cafe", "Big Cafe"]
    assert HEALTH.last_ok_server == "b.example"

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="Too many requests"))))
    with pytest.raises(DiscoveryError) as exc:
        await provider.search(SearchSpec(latitude=1, longitude=2, radius_m=500, category=category_by_key("cafe")))
    assert "a.example: HTTP 429" in str(exc.value) and "b.example: HTTP 429" in str(exc.value)
    assert "HTTP 429" in HEALTH.last_error


async def test_overpass_406_retries_as_get_and_explains(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "overpass_url", "")
    monkeypatch.setattr(get_settings(), "overpass_urls", "https://a.example/api/interpreter")
    seen = []

    def handler(request):
        seen.append((request.method, request.headers.get("accept-language")))
        if request.method == "POST":
            return httpx.Response(406, text="<html>406 Not Acceptable nginx</html>")
        return httpx.Response(200, json=OVERPASS_JSON)

    from app.location.providers import osm_headers

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=osm_headers()))
    res = await provider.search(SearchSpec(latitude=28.5, longitude=77.2, radius_m=1000, category=category_by_key("cafe")))
    assert [m for m, _ in seen] == ["POST", "GET"] and seen[0][1].startswith("en") and len(res) == 2

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(406))))
    with pytest.raises(DiscoveryError, match="OSM_USER_AGENT"):
        await provider.search(SearchSpec(latitude=1, longitude=2, radius_m=500, category=category_by_key("cafe")))


async def test_recently_failed_server_is_skipped(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "overpass_url", "")
    monkeypatch.setattr(get_settings(), "overpass_urls", "https://slow.example/api/interpreter,https://ok.example/api/interpreter")
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(504) if request.url.host == "slow.example" else httpx.Response(200, json=OVERPASS_JSON)

    provider = OverpassProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    spec = SearchSpec(latitude=1, longitude=2, radius_m=500, category=category_by_key("cafe"))
    await provider.search(spec)
    await provider.search(spec)
    assert hosts == ["slow.example", "ok.example", "ok.example"]  # second search skips the failing server


async def test_nominatim_fallback_parses_places():
    from app.location.providers import NominatimProvider

    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[
            {"osm_type": "node", "osm_id": 55, "lat": "28.5250", "lon": "77.2070", "name": "Blue Tokai", "category": "amenity",
             "type": "cafe", "display_name": "Blue Tokai, Select Citywalk, Saket, New Delhi, India", "extratags": {"opening_hours": "Mo-Su 08:00-23:00"}},
            {"osm_type": "way", "osm_id": 9, "lat": "28.52", "lon": "77.20", "name": "", "display_name": ""},
        ])

    provider = NominatimProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = await provider.search(SearchSpec(latitude=28.5245, longitude=77.2066, radius_m=1000, category=category_by_key("cafe")))
    assert seen["q"] == "cafe" and seen["bounded"] == "1" and seen["extratags"] == "1"
    west, north, east, south = map(float, seen["viewbox"].split(","))
    assert west < 77.2066 < east and south < 28.5245 < north
    assert len(res) == 1 and res[0].osm_id == "node/55" and res[0].tags["amenity"] == "cafe"
    p = to_place_result(res[0], 28.5245, 77.2066, None)
    assert p.opening_hours == "Mo-Su 08:00-23:00" and p.address.startswith("Select Citywalk") and p.source == "osm"
