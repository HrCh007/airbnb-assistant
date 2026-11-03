"""Unit tests: geo maths, ranking, category matching and the Google Places
client (against a mocked HTTP transport - no real API calls)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.location.categories import match_category
from app.location.geo import directions_url, format_distance, haversine_km
from app.location.providers import GooglePlacesProvider, PlacesError, SearchSpec
from app.location.ranking import RankingContext, rank_places
from app.schemas.common import PlaceResult


def test_haversine_known_distance():
    # Connaught Place -> India Gate is roughly 2.3-2.5 km.
    d = haversine_km(28.6315, 77.2167, 28.6129, 77.2295)
    assert 2.2 < d < 2.6
    assert haversine_km(28.5, 77.2, 28.5, 77.2) == 0


def test_format_distance():
    assert format_distance(0.234) == "230 m"
    assert format_distance(1.26) == "1.3 km"
    assert format_distance(None) is None


def test_directions_url_uses_exact_origin_and_place_id():
    url = directions_url(28.524512, 77.206612, "Cafe X", "Saket", "ChIJabc", "walking")
    assert "origin=28.524512%2C77.206612" in url
    assert "destination_place_id=ChIJabc" in url and "travelmode=walking" in url


@pytest.mark.parametrize(
    "text,key",
    [("Is there parking?", None), ("good momos", "momos"), ("rooftop bar", "lounge"), ("pharmacy", "pharmacy"), ("parks nearby", "park")],
)
def test_category_matching_uses_word_boundaries(text, key):
    cat = match_category(text)
    assert (cat.key if cat else None) == key


def _p(name, dist, rating, count, price=None, open_now=None):
    return PlaceResult(name=name, distance_km=dist, rating=rating, user_rating_count=count, price_level=price, open_now=open_now)


def test_ranking_balances_rating_and_distance_not_just_closest():
    s = Settings()
    places = [_p("Close but poor", 0.1, 3.2, 20), _p("Great and near", 0.8, 4.7, 3000), _p("Far", 2.9, 4.8, 5000)]
    ranked = rank_places(places, RankingContext(query="cafe", radius_km=3), s)
    assert ranked[0].name == "Great and near"


def test_bayesian_rating_discounts_tiny_review_counts():
    s = Settings()
    places = [_p("Five stars from 2 reviews", 1, 5.0, 2), _p("4.5 from 4000", 1, 4.5, 4000)]
    ranked = rank_places(places, RankingContext(query="x", radius_km=3), s)
    assert ranked[0].name == "4.5 from 4000"


def test_budget_preference_promotes_cheaper():
    s = Settings(rank_weight_preference=0.5)
    places = [_p("Pricey", 1, 4.5, 500, price=4), _p("Cheap", 1, 4.4, 500, price=1)]
    ranked = rank_places(places, RankingContext(query="x", radius_km=3, budget="budget"), s)
    assert ranked[0].name == "Cheap"


# --------------------------------------------------------------------------- Google Places client
def _settings(**kw):
    kw.setdefault("places_max_retries", 2)
    return Settings(google_maps_api_key="test-key-not-real", **kw)


SAMPLE = {
    "places": [
        {
            "id": "ChIJ1",
            "displayName": {"text": "Real Cafe"},
            "formattedAddress": "1 Road, Delhi",
            "location": {"latitude": 28.53, "longitude": 77.21},
            "rating": 4.4,
            "userRatingCount": 812,
            "priceLevel": "PRICE_LEVEL_MODERATE",
            "currentOpeningHours": {"openNow": True},
            "primaryTypeDisplayName": {"text": "Cafe"},
            "types": ["cafe", "food"],
            "googleMapsUri": "https://maps.google.com/?cid=1",
        },
        {"id": "ChIJ2", "displayName": {"text": "Closed Forever"}, "businessStatus": "CLOSED_PERMANENTLY"},
    ]
}


async def test_text_search_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=SAMPLE)

    provider = GooglePlacesProvider(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = await provider.search(
        SearchSpec(query="quiet cafe", latitude=28.5245, longitude=77.2066, radius_m=3000, included_type="cafe", open_now=True, max_price_level=2)
    )
    assert seen["url"] == "https://places.googleapis.com/v1/places:searchText"
    assert seen["headers"]["X-Goog-Api-Key"] == "test-key-not-real"
    assert "places.displayName" in seen["headers"]["X-Goog-FieldMask"]
    circle = seen["body"]["locationBias"]["circle"]
    assert circle["center"] == {"latitude": 28.5245, "longitude": 77.2066} and circle["radius"] == 3000.0
    assert seen["body"]["openNow"] is True
    assert seen["body"]["priceLevels"] == ["PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE"]
    assert seen["body"]["includedType"] == "cafe"
    assert len(results) == 1  # permanently closed filtered out
    r = results[0]
    assert (r.name, r.rating, r.price_level, r.open_now, r.category) == ("Real Cafe", 4.4, 2, True, "Cafe")


async def test_nearby_search_used_for_typed_distance_queries():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"places": []})

    provider = GooglePlacesProvider(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await provider.search(SearchSpec(query="", latitude=1, longitude=2, radius_m=1500, included_type="pharmacy", rank_by_distance=True))
    assert seen["url"].endswith("places:searchNearby")
    assert seen["body"]["includedTypes"] == ["pharmacy"] and seen["body"]["rankPreference"] == "DISTANCE"


async def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=SAMPLE)

    provider = GooglePlacesProvider(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert len(await provider.search(SearchSpec(query="x", latitude=1, longitude=2, radius_m=100))) == 1
    assert calls["n"] == 3


async def test_does_not_retry_client_errors():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})

    provider = GooglePlacesProvider(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PlacesError):
        await provider.search(SearchSpec(query="x", latitude=1, longitude=2, radius_m=100))
    assert calls["n"] == 1


async def test_timeouts_raise_places_error():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    provider = GooglePlacesProvider(_settings(places_max_retries=1), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PlacesError):
        await provider.search(SearchSpec(query="x", latitude=1, longitude=2, radius_m=100))


async def test_details_rejects_malformed_place_ids():
    provider = GooglePlacesProvider(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert await provider.details("../../etc/passwd") is None
