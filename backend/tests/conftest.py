"""Test configuration.

Each run uses a fresh data directory (SQLite + embedded ChromaDB + uploads)
seeded through the real bootstrap (`python -m scripts.seed`). External services
are replaced with deterministic fakes:

* Overpass (OpenStreetMap): `FakeOverpass` - records calls, returns fixture places
* Ollama: not running by default (extractive mode); `fake_llm` injects a fake
* Embeddings: the local hash embedder (no model download needed);
  test_embeddings_st.py exercises the sentence-transformers path separately.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DATA_DIR = Path(tempfile.gettempdir()) / "hideout-tests"
os.environ.update(
    {
        "DATA_DIR": str(DATA_DIR),
        "DATABASE_URL": "",
        "ENVIRONMENT": "test",
        "CHROMA_MODE": "persistent",
        "EMBEDDING_PROVIDER": "hash",
        "LLM_ENABLED": "true",
        "OLLAMA_URL": "http://127.0.0.1:9",  # nothing listens here: Ollama "unreachable"
        "DISCOVERY_LIVE": "true",
        "DISCOVERY_SAMPLE_DATA": "true",
        "SECRET_KEY": "test-secret-key-for-unit-tests-only-0123456789",
        "RATE_LIMIT_CHAT_PER_MINUTE": "10000",
        "RATE_LIMIT_DEFAULT_PER_MINUTE": "10000",
        "RATE_LIMIT_LOGIN_PER_MINUTE": "10000",
        "ADMIN_EMAIL": "admin@test.local",
        "ADMIN_PASSWORD": "test-admin-password-123",
        "AUTO_SEED_SAMPLE_DATA": "true",
    }
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


def pytest_sessionstart(session):  # noqa: ARG001
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    r = subprocess.run([sys.executable, "-m", "scripts.seed"], env={**os.environ}, cwd=BACKEND, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Seeding the test database failed:\n{r.stdout}\n{r.stderr}")


# ------------------------------------------------------------------ fakes
from app.location.providers import DiscoveryError, RawPlace, SearchSpec  # noqa: E402

PROPERTY_COORDS = {"saket": (28.5245, 77.2066), "dwarka": (28.5921, 77.0460), "janakpuri": (28.6219, 77.0878)}


class FakeOverpass:
    """Stands in for the live Overpass API. Generates OSM-style places that
    match the requested category, at known offsets from the search origin."""

    name = "osm"

    def __init__(self) -> None:
        self.calls: list[SearchSpec] = []
        self.fail = False
        self.empty = False

    def build_query(self, spec: SearchSpec) -> str:
        from app.location.providers import OverpassProvider

        return OverpassProvider.build_query(self, spec)  # type: ignore[arg-type]

    async def search(self, spec: SearchSpec) -> list[RawPlace]:
        self.calls.append(spec)
        if self.fail:
            raise DiscoveryError("simulated Overpass outage")
        if self.empty:
            return []
        label = spec.category.key if spec.category else (spec.keyword or "place")
        tags_by_cat = {
            "budget_food": [{"amenity": "fast_food"}, {"amenity": "food_court"}],
            "club": [{"amenity": "nightclub", "opening_hours": "Mo-Su 21:00-03:00"}, {"amenity": "bar"}],
            "bar": [{"amenity": "bar", "opening_hours": "Mo-Su 17:00-01:00"}],
            "pharmacy": [{"amenity": "pharmacy", "opening_hours": "24/7"}],
            "hospital": [{"amenity": "hospital", "emergency": "yes"}],
            "cafe": [{"amenity": "cafe", "opening_hours": "Mo-Su 08:00-22:00"}],
        }
        variants = tags_by_cat.get(label, [{"amenity": "restaurant", "cuisine": "indian", "opening_hours": "Mo-Su 12:00-23:00"}])
        out = []
        offsets = [(0.003, "Near"), (0.008, "Mid"), (0.012, "Far"), (0.2, "Outside")]  # ~0.3, 0.9, 1.3, 22 km
        for i, (dlat, prefix) in enumerate(offsets):
            tags = dict(variants[i % len(variants)])
            tags["name"] = f"OSM {prefix} {label.title()}"
            tags["addr:street"] = "Test Marg"
            out.append(RawPlace(osm_id=f"node/{1000 + i}", name=tags["name"], lat=spec.latitude + dlat, lon=spec.longitude, tags=tags, source="osm"))
        # A duplicate of the first place (OSM often has node + way for one venue).
        dup = out[0]
        out.append(RawPlace(osm_id="way/2000", name=dup.name, lat=dup.lat + 0.0001, lon=dup.lon, tags=dup.tags, source="osm"))
        return out


@pytest.fixture
def fake_osm():
    from app.location import discovery
    from app.location.providers import set_providers

    provider = FakeOverpass()
    set_providers(overpass=provider)
    discovery.clear_cache()
    yield provider
    set_providers()
    discovery.clear_cache()


class FakeLLM:
    """Replaces the Ollama calls; records what the model would receive."""

    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.reply = "Grounded answer."
        self.fail = False
        self.classified: list[str] = []
        self.classify_result = {"intent": "LOCAL_DISCOVERY_QUERY", "search_query": "rooftop"}

    async def status(self, force: bool = False):
        return {"enabled": True, "reachable": True, "model": "fake", "model_ready": True, "models": ["fake"]}

    async def stream_chat(self, history, user_turn):
        from app.llm.ollama import LLMUnavailable

        self.turns.append({"history": history, "user_turn": user_turn})
        if self.fail:
            raise LLMUnavailable("simulated")
        for i in range(0, len(self.reply), 7):
            yield self.reply[i : i + 7]

    async def classify(self, message, context):
        self.classified.append(message)
        return self.classify_result


@pytest.fixture
def fake_llm(monkeypatch):
    from app.llm import ollama

    fake = FakeLLM()
    monkeypatch.setattr(ollama, "status", fake.status)
    monkeypatch.setattr(ollama, "stream_chat", fake.stream_chat)
    monkeypatch.setattr(ollama, "classify", fake.classify)
    return fake


@pytest_asyncio.fixture(scope="session")
async def app():
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app):
    import httpx

    from app.core.rate_limit import limiter

    limiter.reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60) as c:
        yield c


@pytest_asyncio.fixture
async def db():
    from app.core.database import get_sessionmaker

    async with get_sessionmaker()() as session:
        yield session


@pytest_asyncio.fixture
async def admin_headers(client):
    r = await client.post("/api/admin/login", json={"email": "admin@test.local", "password": "test-admin-password-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def property_ids(db):
    from sqlalchemy import select

    from app.models import Property

    rows = (await db.execute(select(Property.slug, Property.id))).all()
    return {slug.replace("demo-", ""): pid for slug, pid in rows}


class ChatSession:
    def __init__(self, client, sid: str, token: str):
        self.client, self.sid, self.token = client, sid, token
        self.headers = {"X-Session-Token": token}

    async def select(self, location: str):
        r = await self.client.put(f"/api/chat/sessions/{self.sid}/selection", json={"location_slug": location}, headers=self.headers)
        assert r.status_code == 200, r.text
        return r.json()

    async def ask(self, message: str) -> dict:
        import json

        out = {"text": "", "places": [], "intent": None, "events": [], "suggestions": [], "previous": False, "meta": {}}
        async with self.client.stream("POST", "/api/chat/message", json={"session_id": self.sid, "message": message}, headers=self.headers) as r:
            assert r.status_code == 200, await r.aread()
            event = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    out["events"].append(event)
                    if event == "token":
                        out["text"] += data
                    elif event == "replace":
                        out["text"] = data
                    elif event == "places":
                        out["places"], out["previous"] = data["items"], data["previous"]
                    elif event == "meta":
                        out["intent"], out["meta"] = data["intent"], data
                    elif event == "suggestions":
                        out["suggestions"] = data
                    elif event == "done":
                        out["intent"] = out["intent"] or data.get("intent")
                    elif event == "error":
                        raise AssertionError(f"stream error: {data}")
        return out


@pytest_asyncio.fixture
async def new_chat(client):
    async def _make(location: str | None = None) -> ChatSession:
        r = await client.post("/api/chat/sessions", json={})
        assert r.status_code == 201
        body = r.json()
        chat = ChatSession(client, body["session_id"], body["session_token"])
        if location:
            await chat.select(location)
        return chat

    return _make
