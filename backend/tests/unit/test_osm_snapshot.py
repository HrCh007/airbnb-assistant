"""The snapshot builder reads an OpenStreetMap file (two passes, low memory)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("osmium")

from scripts.build_osm_snapshot import from_extract  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini.osm"


def test_extract_keeps_named_relevant_places_near_properties():
    props = [SimpleNamespace(latitude=28.5245, longitude=77.2066)]
    places = from_extract(FIXTURE, props, radius_km=5)
    names = {p["name"] for p in places.values()}
    assert names == {"PVR Test Cinema", "Test Coffee", "Test Mall"}  # no bench, no unnamed, nothing 45 km away
    mall = places["way/100"]  # building outline -> centre point from its nodes
    assert 28.522 < mall["lat"] < 28.524 and 77.204 < mall["lon"] < 77.206
    assert places["node/2"]["tags"]["opening_hours"] == "Mo-Su 08:00-23:00"
