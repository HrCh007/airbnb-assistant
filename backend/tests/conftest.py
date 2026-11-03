"""Test configuration.

Uses a dedicated database (TEST_DATABASE_URL, default airbnb_assistant_test),
rebuilt from migrations and seeded with the fictional sample dataset once per
run. External services are replaced with deterministic fakes:

* Places: `FakePlacesProvider` (records calls, returns test fixtures)
* Claude: disabled by default (offline mode); specific tests inject a fake client
* Embeddings: the local hashing embedder
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://airbnb:airbnb@localhost:5432/airbnb_assistant_test"
)
os.environ.update(
    {
        "DATABASE_URL": TEST_DB,
        "ENVIRONMENT": "test",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GOOGLE_MAPS_API_KEY": "",
        "EMBEDDING_PROVIDER": "local",
        "PLACES_PROVIDER": "disabled",
        "SECRET_KEY": "test-secret-key-for-unit-tests-only-0123456789",
        "RATE_LIMIT_CHAT_PER_MINUTE": "10000",
        "RATE_LIMIT_DEFAULT_PER_MINUTE": "10000",
        "RATE_LIMIT_LOGIN_PER_MINUTE": "10000",
        "ADMIN_EMAIL": "admin@test.local",
        "ADMIN_PASSWORD": "test-admin-password-123",
    }
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


def _reset_database() -> None:
    """Drop/recreate the test schema, run migrations and seed (in subprocesses,
    so no event loop or engine leaks into the test session)."""
    code = (
        "import asyncio, asyncpg, os\n"
        "async def m():\n"
        "    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg', ''))\n"
        "    await c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')\n"
        "    await c.close()\n"
        "asyncio.run(m())\n"
    )
    env = {**os.environ}
    subprocess.run([sys.executable, "-c", code], check=True, env=env, cwd=BACKEND)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env, cwd=BACKEND, capture_output=True)
    subprocess.run([sys.executable, "-m", "scripts.seed"], check=True, env=env, cwd=BACKEND, capture_output=True)


def pytest_sessionstart(session):  # noqa: ARG001
    _reset_database()


# ------------------------------------------------------------------ fakes
from app.location.providers import SearchSpec  # noqa: E402
from app.schemas.common import PlaceResult  # noqa: E402


class FakePlacesProvider:
    """Deterministic stand-in for Google Places. Returns fixture places placed
    at known offsets from the search origin and records every call."""

    name = "google_places"

    def __init__(self) -> None:
        self.calls: list[SearchSpec] = []
        self.empty_queries: set[str] = set()
        self.fail = False

    async def search(self, spec: SearchSpec) -> list[PlaceResult]:
        from app.location.providers import PlacesError

        self.calls.append(spec)
        if self.fail:
            raise PlacesError("simulated outage")
        if any(q in spec.query.lower() for q in self.empty_queries):
            return []
        kind = (spec.included_type or spec.query).split()[-1]
        base = [
            ("Test Premium", 4, 4.6, 900, 0.004, True),
            ("Test Midrange", 2, 4.3, 1500, 0.008, True),
            ("Test Budget A", 1, 4.1, 600, 0.010, True),
            ("Test Budget B", 1, 3.9, 80, 0.006, False),
            ("Test Faraway", 2, 4.8, 3000, 0.300, True),  # ~33 km away: filtered out
        ]
        out = []
        for name, price, rating, count, dlat, open_now in base:
            if spec.max_price_level is not None and price > spec.max_price_level:
                continue
            if spec.open_now and not open_now:
                continue
            out.append(
                PlaceResult(
                    place_id=f"test-{name.lower().replace(' ', '-')}-{kind}",
                    name=f"{name} {kind.title()}",
                    category=kind.replace("_", " ").title(),
                    types=[kind],
                    address=f"{name} Road, Test City",
                    latitude=spec.latitude + dlat,
                    longitude=spec.longitude,
                    rating=rating,
                    user_rating_count=count,
                    price_level=price,
                    open_now=open_now,
                    summary=f"A {kind} used in automated tests.",
                    maps_url=f"https://maps.google.com/?cid={abs(hash(name)) % 10**8}",
                )
            )
        return out

    async def details(self, place_id: str):
        return None


@pytest.fixture
def fake_places():
    from app.location import discovery
    from app.location.providers import set_places_provider

    provider = FakePlacesProvider()
    set_places_provider(provider)
    discovery.clear_cache()
    yield provider
    set_places_provider(None)
    discovery.clear_cache()


@pytest_asyncio.fixture(scope="session")
async def app():
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app):
    import httpx

    from app.core.rate_limit import limiter

    limiter.reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30) as c:
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


# ------------------------------------------------------------------ chat helper
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

        out = {"text": "", "places": [], "intent": None, "events": [], "suggestions": [], "previous": False}
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
                    elif event == "places":
                        out["places"], out["previous"] = data["items"], data["previous"]
                    elif event == "meta":
                        out["intent"] = data["intent"]
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
