"""Retrieval isolation, admin auth, property CRUD, document ingestion API,
curated recommendations and graceful degradation."""

from __future__ import annotations

import io
import json
import uuid

import docx
import pytest
from sqlalchemy import func, select

from app.models import KnowledgeChunk, PropertyDocument
from app.rag.ingestion import wait_for_background
from app.rag.retriever import retrieve

QUESTIONS = [
    "What's the Wi-Fi password?",
    "What time is check-out?",
    "Is there parking?",
    "Are pets allowed?",
    "Host contact number",
    "door code keybox smart lock",
    "Dwarka wifi password dwarka-demo-1357",
    "Saket keybox 7391",
]


# ============================================================ isolation
@pytest.mark.parametrize("target", ["saket", "dwarka", "janakpuri"])
async def test_retrieval_never_crosses_properties(db, property_ids, target):
    pid = property_ids[target]
    for q in QUESTIONS:
        result = await retrieve(db, pid, q, allow_sensitive=True, top_k=20)
        assert all(c.property_id == pid for c in result.chunks), q
        assert all(c.metadata.get("property_id") == str(pid) for c in result.chunks)


async def test_saket_query_cannot_retrieve_dwarka_documents(db, property_ids):
    result = await retrieve(db, property_ids["saket"], "dwarka-demo-1357 Dwarka basement slot B-12", top_k=20)
    joined = " ".join(c.content for c in result.chunks)
    assert "dwarka-demo-1357" not in joined and "B-12" not in joined


async def test_retrieve_requires_uuid(db):
    with pytest.raises(TypeError):
        await retrieve(db, "not-a-uuid", "wifi")  # type: ignore[arg-type]


async def test_database_rejects_chunk_attached_to_other_propertys_document(db, property_ids):
    from sqlalchemy.exc import IntegrityError

    doc_id = (await db.execute(select(PropertyDocument.id).where(PropertyDocument.property_id == property_ids["dwarka"]))).scalars().first()
    db.add(KnowledgeChunk(property_id=property_ids["saket"], document_id=doc_id, chunk_index=0, content="x", embedding=[0.0] * 1536))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_sensitive_chunks_are_flagged(db, property_ids):
    result = await retrieve(db, property_ids["saket"], "Wi-Fi password", allow_sensitive=False)
    assert result.withheld_sensitive
    assert all(not c.is_sensitive for c in result.chunks)
    assert all("saket-demo-2468" not in c.content for c in result.chunks)


# ============================================================ public API basics
async def test_health_and_status(client):
    assert (await client.get("/health")).json() == {"status": "ok", "database": True}
    status = (await client.get("/api/status")).json()
    assert status["llm"] == "offline" and status["embeddings"] == "local"


async def test_locations_and_properties(client):
    locs = (await client.get("/api/locations")).json()
    assert {loc["slug"] for loc in locs} >= {"dwarka", "janakpuri", "saket"}
    props = (await client.get("/api/properties?location=saket")).json()
    assert [p["name"] for p in props] == ["Demo Property – Saket"]
    # Public property view never exposes structured sensitive details.
    detail = (await client.get(f"/api/properties/{props[0]['id']}")).json()
    assert "details" not in detail and "saket-demo-2468" not in json.dumps(detail)
    assert (await client.get(f"/api/properties/{uuid.uuid4()}")).status_code == 404


# ============================================================ admin auth
async def test_admin_endpoints_require_auth(client):
    for method, path in [("get", "/api/admin/dashboard"), ("get", "/api/admin/properties"), ("post", "/api/admin/properties"), ("get", "/api/admin/documents")]:
        r = await getattr(client, method)(path)
        assert r.status_code == 401, path
    r = await client.get("/api/admin/dashboard", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


async def test_login_rejects_bad_credentials(client):
    assert (await client.post("/api/admin/login", json={"email": "admin@test.local", "password": "nope"})).status_code == 401
    assert (await client.post("/api/admin/login", json={"email": "ghost@test.local", "password": "x"})).status_code == 401


async def test_dashboard(client, admin_headers):
    d = (await client.get("/api/admin/dashboard", headers=admin_headers)).json()
    assert d["properties"] >= 3 and d["chunks"] > 0 and d["placeholder_properties"] >= 3


# ============================================================ property CRUD + profile indexing
async def test_property_crud_and_structured_profile_is_searchable(client, admin_headers, db):
    body = {
        "name": "CRUD Test Flat",
        "location_slug": "dwarka",
        "address": "Test address",
        "latitude": 28.59,
        "longitude": 77.05,
        "discovery_radius_m": 2500,
        "bedrooms": 2,
        "details": {"facilities": {"wifi_name": "CRUDNET", "wifi_password": "crud-pass-42"}, "check_in_out": {"check_out_time": "9:45 AM"}},
    }
    r = await client.post("/api/admin/properties", json=body, headers=admin_headers)
    assert r.status_code == 201, r.text
    prop = r.json()
    pid = uuid.UUID(prop["id"])
    assert prop["slug"] == "crud-test-flat" and prop["chunk_count"] > 0

    res = await retrieve(db, pid, "What is the checkout time?")
    assert any("9:45 AM" in c.content for c in res.chunks)
    wifi = await retrieve(db, pid, "wifi password")
    assert any(c.is_sensitive and "crud-pass-42" in c.content for c in wifi.chunks)

    r = await client.put(f"/api/admin/properties/{pid}", json={"details": {"check_in_out": {"check_out_time": "10:15 AM"}}, "guest_pin": "9999"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["requires_pin"] is True
    await db.rollback()
    res = await retrieve(db, pid, "What is the checkout time?")
    joined = " ".join(c.content for c in res.chunks)
    assert "10:15 AM" in joined and "9:45 AM" not in joined

    # Validation
    bad = await client.put(f"/api/admin/properties/{pid}", json={"latitude": 123}, headers=admin_headers)
    assert bad.status_code == 422

    # Deactivate hides from guests; hard delete removes chunks.
    assert (await client.delete(f"/api/admin/properties/{pid}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"/api/properties/{pid}")).status_code == 404
    assert (await client.delete(f"/api/admin/properties/{pid}?hard=true", headers=admin_headers)).status_code == 204
    count = await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.property_id == pid))
    assert count == 0


async def test_create_location(client, admin_headers):
    slug = f"test-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/admin/locations", json={"slug": slug, "name": "Test Area"}, headers=admin_headers)
    assert r.status_code == 201
    assert (await client.post("/api/admin/locations", json={"slug": slug, "name": "Dup"}, headers=admin_headers)).status_code == 409


# ============================================================ document ingestion API
def _docx_bytes(text: str) -> bytes:
    d = docx.Document()
    d.add_heading("Extra info", level=1)
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    "filename,content,needle",
    [
        ("extra.txt", b"# Bicycles\nTwo bicycles are available in the parking area for guests.", "bicycles"),
        ("extra.md", b"## Board games\nBoard games are in the TV cabinet.", "board games"),
        ("extra.csv", b"section,answer\nYoga mats,Two yoga mats are in the wardrobe.", "yoga mats"),
        ("extra.json", json.dumps({"rooftop": {"access": "Rooftop terrace open 6 AM to 10 PM"}}).encode(), "rooftop terrace"),
        ("extra.docx", None, "hair dryer"),
    ],
)
async def test_upload_index_retrieve_delete(client, admin_headers, db, property_ids, filename, content, needle):
    pid = property_ids["dwarka"]
    if content is None:
        content = _docx_bytes("A hair dryer is in the bathroom drawer.")
    r = await client.post("/api/admin/documents/upload", data={"property_id": str(pid)}, files={"file": (filename, content)}, headers=admin_headers)
    assert r.status_code == 202, r.text
    doc_id = r.json()["id"]
    await wait_for_background()

    docs = (await client.get(f"/api/admin/documents?property_id={pid}", headers=admin_headers)).json()
    row = next(d for d in docs if d["id"] == doc_id)
    assert row["status"] == "ready" and row["chunk_count"] >= 1 and row["last_indexed_at"]

    res = await retrieve(db, pid, needle)
    assert any(needle in c.content.lower() for c in res.chunks)
    # Never visible to another property.
    other = await retrieve(db, property_ids["saket"], needle, top_k=20)
    assert not any(c.document_id == uuid.UUID(doc_id) for c in other.chunks)

    chunks = (await client.get(f"/api/admin/documents/{doc_id}/chunks", headers=admin_headers)).json()
    assert chunks[0]["metadata"]["property_id"] == str(pid)
    assert set(chunks[0]["metadata"]) >= {"property_id", "location", "document_name", "document_type", "section", "chunk_id", "last_updated"}

    assert (await client.post(f"/api/admin/documents/{doc_id}/reindex", headers=admin_headers)).status_code == 202
    await wait_for_background()

    assert (await client.delete(f"/api/admin/documents/{doc_id}", headers=admin_headers)).status_code == 204
    await db.rollback()
    left = await db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == uuid.UUID(doc_id)))
    assert left == 0


async def test_upload_rejects_bad_files(client, admin_headers, property_ids):
    pid = str(property_ids["saket"])
    r = await client.post("/api/admin/documents/upload", data={"property_id": pid}, files={"file": ("x.exe", b"MZ...")}, headers=admin_headers)
    assert r.status_code == 415
    r = await client.post("/api/admin/documents/upload", data={"property_id": pid}, files={"file": ("fake.pdf", b"hello")}, headers=admin_headers)
    assert r.status_code == 415
    r = await client.post("/api/admin/documents/upload", data={"property_id": pid}, files={"file": ("empty.txt", b"")}, headers=admin_headers)
    assert r.status_code == 422
    big = b"a" * (10 * 1024 * 1024 + 10)
    r = await client.post("/api/admin/documents/upload", data={"property_id": pid}, files={"file": ("big.txt", big)}, headers=admin_headers)
    assert r.status_code == 413


async def test_failed_processing_is_recorded(client, admin_headers, property_ids):
    pid = str(property_ids["saket"])
    r = await client.post("/api/admin/documents/upload", data={"property_id": pid}, files={"file": ("broken.json", b"{not valid json")}, headers=admin_headers)
    assert r.status_code == 202
    await wait_for_background()
    docs = (await client.get(f"/api/admin/documents?property_id={pid}", headers=admin_headers)).json()
    row = next(d for d in docs if d["id"] == r.json()["id"])
    assert row["status"] == "failed" and "Invalid JSON" in row["error_message"]
    await client.delete(f"/api/admin/documents/{row['id']}", headers=admin_headers)


async def test_system_profile_document_cannot_be_deleted(client, admin_headers, property_ids):
    docs = (await client.get(f"/api/admin/documents?property_id={property_ids['saket']}&include_system=true", headers=admin_headers)).json()
    system = next(d for d in docs if d["is_system"])
    assert (await client.delete(f"/api/admin/documents/{system['id']}", headers=admin_headers)).status_code == 400


# ============================================================ curated recommendations
async def test_curated_picks_complement_live_results(client, admin_headers, property_ids, new_chat, fake_places):
    pid = str(property_ids["saket"])
    r = await client.post(
        "/api/admin/curated",
        json={"property_id": pid, "name": "Host Test Cafe", "category": "cafe", "note": "Our favourite filter coffee.", "latitude": 28.526, "longitude": 77.207},
        headers=admin_headers,
    )
    assert r.status_code == 201
    rec_id = r.json()["id"]
    try:
        res = await (await new_chat("saket")).ask("Suggest a good cafe nearby")
        names = [p["name"] for p in res["places"]]
        assert "Host Test Cafe" in names and len(names) > 1  # complements live results
        host = next(p for p in res["places"] if p["source"] == "host_curated")
        assert host["host_note"] == "Our favourite filter coffee." and host["distance_km"] is not None
        # Not shown for other properties.
        res = await (await new_chat("dwarka")).ask("Suggest a good cafe nearby")
        assert "Host Test Cafe" not in [p["name"] for p in res["places"]]
    finally:
        await client.delete(f"/api/admin/curated/{rec_id}", headers=admin_headers)


async def test_places_search_endpoint(client, property_ids, fake_places):
    r = await client.post("/api/places/search", json={"property_id": str(property_ids["janakpuri"]), "query": "cafe", "limit": 3})
    body = r.json()
    assert r.status_code == 200 and len(body["results"]) <= 3
    assert body["origin"]["latitude"] == 28.6219
    assert all(body["results"][i]["score"] >= body["results"][i + 1]["score"] for i in range(len(body["results"]) - 1))
