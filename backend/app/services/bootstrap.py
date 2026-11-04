"""Startup bootstrap: tables, admin account, sample data, index consistency.

Runs on every backend start (idempotent), so `docker compose up` alone gives a
working app. Sample data is only loaded into an EMPTY database and is clearly
marked fictional; disable with AUTO_SEED_SAMPLE_DATA=false.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_sessionmaker, init_db
from app.core.security import hash_password
from app.models import AdminUser, Location, Property, PropertyDocument
from app.rag import vectorstore
from app.rag.embeddings import get_embedder
from app.rag.extractors import detect_type
from app.rag.ingestion import create_document, process_document, reindex_property, sync_profile_document
from app.schemas.property import PropertyDetails

log = logging.getLogger(__name__)

DEV_ADMIN_EMAIL = "admin@example.com"
DEV_ADMIN_PASSWORD = "ChangeMe-Dev-2026!"

LOCATIONS = [
    ("dwarka", "Dwarka", "Planned sub-city in south-west Delhi, close to IGI Airport", 1),
    ("janakpuri", "Janakpuri", "Leafy west-Delhi residential hub on the Blue & Magenta lines", 2),
    ("saket", "Saket", "South Delhi's buzzing mall, food and nightlife district", 3),
]

PLACEHOLDER_ADDRESS = "PLACEHOLDER – replace with the real full address in the admin dashboard"

PROPERTIES = [
    {
        "slug": "demo-dwarka",
        "name": "Demo Property – Dwarka",
        "location": "dwarka",
        "neighbourhood": "Dwarka (placeholder sector)",
        "latitude": 28.5921, "longitude": 77.0460,
        "bedrooms": 3, "bathrooms": 2, "max_guests": 6,
        "description": "FICTIONAL development listing used to test the assistant.",
        "amenities": ["Wi-Fi", "Air conditioning", "Kitchen", "Washing machine", "TV", "Parking", "Power backup"],
        "details": {
            "check_in_out": {"check_in_time": "1:00 PM", "check_out_time": "10:30 AM"},
            "facilities": {"parking": "One covered parking spot in the basement, slot B-12 (fictional).", "kitchen": "Full kitchen with induction cooktop, microwave, toaster and mixer-grinder."},
            "emergency": {"host_contact": "Demo Host Dwarka, +91-00000-00011 (placeholder number)", "emergency_numbers": "112 (all emergencies)"},
            "neighbourhood": {"nearby_attractions": "Sample data: ask the assistant for live nearby places."},
        },
        "docs": ["dwarka/dwarka-guest-guide.json", "dwarka/dwarka-house-rules.txt"],
        "pin": None,
    },
    {
        "slug": "demo-janakpuri",
        "name": "Demo Property – Janakpuri",
        "location": "janakpuri",
        "neighbourhood": "Janakpuri (placeholder block)",
        "latitude": 28.6219, "longitude": 77.0878,
        "bedrooms": 1, "bathrooms": 1, "max_guests": 2,
        "description": "FICTIONAL development listing used to test the assistant. Demonstrates the guest PIN.",
        "amenities": ["Wi-Fi", "Air conditioning (bedroom)", "Kitchenette", "Parking"],
        "details": {
            "check_in_out": {"check_in_time": "3:00 PM", "check_out_time": "12:00 PM (noon)"},
            "facilities": {"parking": "Open parking for one car inside the building gate, first come first served.", "kitchen": "Kitchenette with a single induction plate, kettle and refrigerator. No oven.", "lift": "No lift; the apartment is on the second floor."},
            "emergency": {"host_contact": "Demo Host Janakpuri, +91-00000-00021 (placeholder number)", "emergency_numbers": "112 (all emergencies), 102 (ambulance)"},
        },
        "docs": ["janakpuri/janakpuri-guest-guide.docx", "janakpuri/janakpuri-emergency-and-transport.pdf"],
        "pin": "2468",  # DEV ONLY: sensitive facts require this PIN
    },
    {
        "slug": "demo-saket",
        "name": "Demo Property – Saket",
        "location": "saket",
        "neighbourhood": "Saket (placeholder block)",
        "latitude": 28.5245, "longitude": 77.2066,
        "bedrooms": 2, "bathrooms": 2, "max_guests": 4,
        "description": "FICTIONAL development listing used to test the assistant.",
        "amenities": ["Wi-Fi", "Air conditioning", "Kitchen", "Washing machine", "Smart TV", "Lift", "Inverter backup"],
        "details": {
            "check_in_out": {"check_in_time": "2:00 PM", "check_out_time": "11:00 AM"},
            "facilities": {"wifi_name": "DEMO-SAKET-WIFI", "wifi_password": "saket-demo-2468", "lift": "Yes, the building has a lift.", "parking": "No dedicated parking; street parking is sometimes available on the lane outside.", "kitchen": "Two-burner gas stove, microwave, kettle and basic cookware."},
            "house_rules": {"smoking_policy": "No smoking inside; balcony only.", "pet_policy": "Pets are not allowed."},
            "emergency": {"host_contact": "Demo Host Saket, +91-00000-00001 (placeholder number)", "emergency_numbers": "112 (all emergencies), 102 (ambulance)"},
        },
        "docs": ["saket/saket-guest-guide.md", "saket/saket-faq.csv"],
        "pin": None,
    },
]




async def ensure_admin() -> None:
    s = get_settings()
    email = s.admin_email or (DEV_ADMIN_EMAIL if s.environment != "production" else "")
    password = s.admin_password or (DEV_ADMIN_PASSWORD if s.environment != "production" else "")
    if not (email and password):
        log.warning("No admin account configured: set ADMIN_EMAIL and ADMIN_PASSWORD.")
        return
    async with get_sessionmaker()() as db:
        existing = (await db.execute(select(AdminUser).where(AdminUser.email == email.lower()))).scalar_one_or_none()
        if existing is None:
            db.add(AdminUser(email=email.lower(), password_hash=hash_password(password), full_name="Administrator"))
            await db.commit()
            log.info("Created admin account %s", email)
            if not s.admin_password:
                log.warning("Using the DEFAULT development admin password - set ADMIN_PASSWORD in .env.")


async def seed_sample_data(force: bool = False) -> list[str]:
    """Create the 3 locations and fictional demo properties (+ documents). Returns log lines."""
    out: list[str] = []
    sample_dir = get_settings().bundled_path / "sample_documents"
    async with get_sessionmaker()() as db:
        if not force and (await db.scalar(select(func.count()).select_from(Property))):
            return out
        locs: dict[str, Location] = {}
        for slug, name, tagline, order in LOCATIONS:
            loc = (await db.execute(select(Location).where(Location.slug == slug))).scalar_one_or_none()
            if loc is None:
                loc = Location(slug=slug, name=name, tagline=tagline, sort_order=order)
                db.add(loc)
                await db.flush()
                out.append(f"+ location {name}")
            locs[slug] = loc
        doc_ids = []
        for spec in PROPERTIES:
            if (await db.execute(select(Property).where(Property.slug == spec["slug"]))).scalar_one_or_none():
                continue
            prop = Property(
                slug=spec["slug"], name=spec["name"], location_id=locs[spec["location"]].id,
                address=PLACEHOLDER_ADDRESS, neighbourhood=spec["neighbourhood"],
                latitude=spec["latitude"], longitude=spec["longitude"], discovery_radius_m=3000,
                bedrooms=spec["bedrooms"], bathrooms=spec["bathrooms"], max_guests=spec["max_guests"],
                description=spec["description"], amenities=spec["amenities"],
                details=PropertyDetails.model_validate(spec["details"]).model_dump(exclude_none=True),
                guest_pin_hash=hash_password(spec["pin"]) if spec["pin"] else None,
                is_placeholder=True,
            )
            db.add(prop)
            await db.flush()
            await sync_profile_document(db, prop)
            for rel in spec["docs"]:
                path = sample_dir / rel
                if not path.is_file():
                    out.append(f"  ! sample document {path} not found - skipped")
                    continue
                doc = await create_document(db, prop, path.name, detect_type(path.name), path.read_bytes(), None)
                doc_ids.append(doc.id)
            out.append(f"+ property {spec['name']} ({len(spec['docs'])} documents)")
        await db.commit()
    for doc_id in doc_ids:
        await process_document(doc_id)
    async with get_sessionmaker()() as db:
        if doc_ids:
            rows = (await db.execute(select(PropertyDocument).where(PropertyDocument.id.in_(doc_ids)))).scalars().all()
            for r in rows:
                out.append(f"  indexed {r.filename}: {r.status} ({r.chunk_count} chunks){' - ' + r.error_message if r.error_message else ''}")
    return out


async def ensure_index_consistency() -> None:
    """Re-index properties whose vectors were made by a different embedding model,
    or whose vectors are missing (e.g. the Chroma volume was deleted)."""
    current = get_embedder().name
    async with get_sessionmaker()() as db:
        props = (await db.execute(select(Property))).scalars().unique().all()
        for prop in props:
            has_docs = await db.scalar(select(func.count()).select_from(PropertyDocument).where(PropertyDocument.property_id == prop.id))
            if not has_docs:
                continue
            stored = await vectorstore.collection_embedder(prop.id)
            if stored != current:
                log.info("Re-indexing %s (vectors from %s, current model %s)", prop.name, stored or "none", current)
                await reindex_property(db, prop)


async def bootstrap() -> None:
    s = get_settings()
    await init_db()
    await ensure_admin()
    if s.auto_seed_sample_data:
        for line in await seed_sample_data():
            log.info(line)
    await ensure_index_consistency()
