"""Seed development data: 3 locations, 3 clearly-marked placeholder properties,
their fictional sample documents, and an admin user.

    python -m scripts.seed            # idempotent; skips what already exists
    python -m scripts.seed --reset    # wipe properties/documents first (dev only)

Placeholder coordinates are rough public locality centre-points used only so
distance maths works in development. They are NOT property locations; replace
them with each property's exact coordinates in the admin dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker
from app.core.security import hash_password
from app.models import AdminUser, Location, Property, PropertyDocument
from app.rag.extractors import detect_type
from app.rag.ingestion import create_document, process_document, sync_profile_document
from app.schemas.property import PropertyDetails

DATA = Path(os.environ.get("SAMPLE_DOCUMENTS_DIR") or Path(__file__).resolve().parents[2] / "data" / "sample_documents")
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
        "details": {"check_in_out": {"check_in_time": "1:00 PM", "check_out_time": "10:30 AM"}},
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
        "details": {},
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
            "facilities": {"wifi_name": "DEMO-SAKET-WIFI", "wifi_password": "saket-demo-2468", "lift": "Yes, the building has a lift."},
            "house_rules": {"smoking_policy": "No smoking inside; balcony only.", "pet_policy": "Pets are not allowed."},
            "emergency": {"host_contact": "Demo Host Saket, +91-00000-00001 (placeholder number)", "emergency_numbers": "112 (all emergencies), 102 (ambulance)"},
        },
        "docs": ["saket/saket-guest-guide.md", "saket/saket-faq.csv"],
        "pin": None,
    },
]


async def seed(reset: bool = False) -> None:
    s = get_settings()
    async with get_sessionmaker()() as db:
        if reset:
            if s.environment == "production":
                sys.exit("Refusing to --reset in production.")
            await db.execute(delete(Property))
            await db.commit()

        locs: dict[str, Location] = {}
        for slug, name, tagline, order in LOCATIONS:
            loc = (await db.execute(select(Location).where(Location.slug == slug))).scalar_one_or_none()
            if loc is None:
                loc = Location(slug=slug, name=name, tagline=tagline, sort_order=order)
                db.add(loc)
                await db.flush()
                print(f"+ location {name}")
            locs[slug] = loc

        doc_ids = []
        for spec in PROPERTIES:
            prop = (await db.execute(select(Property).where(Property.slug == spec["slug"]))).scalar_one_or_none()
            if prop is not None:
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
                path = DATA / rel
                if not path.is_file():
                    print(f"  ! sample document {path} not found - skipping (mount data/sample_documents)")
                    continue
                doc = await create_document(db, prop, path.name, detect_type(path.name), path.read_bytes(), None)
                doc_ids.append(doc.id)
            print(f"+ property {spec['name']} ({len(spec['docs'])} documents)")
        await db.commit()

        for doc_id in doc_ids:
            await process_document(doc_id)
        if doc_ids:
            rows = (await db.execute(select(PropertyDocument.filename, PropertyDocument.status, PropertyDocument.chunk_count, PropertyDocument.error_message).where(PropertyDocument.id.in_(doc_ids)))).all()
            for r in rows:
                print(f"  indexed {r.filename}: {r.status} ({r.chunk_count} chunks){' - ' + r.error_message if r.error_message else ''}")

        email = s.admin_email or (DEV_ADMIN_EMAIL if s.environment != "production" else "")
        password = s.admin_password or (DEV_ADMIN_PASSWORD if s.environment != "production" else "")
        if email and password:
            existing = (await db.execute(select(AdminUser).where(AdminUser.email == email.lower()))).scalar_one_or_none()
            if existing is None:
                db.add(AdminUser(email=email.lower(), password_hash=hash_password(password), full_name="Administrator"))
                await db.commit()
                shown = password if not s.admin_password else "(from ADMIN_PASSWORD)"
                print(f"+ admin {email} / {shown}")
                if not s.admin_password:
                    print("  WARNING: default development admin credentials - change them before deploying.")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(seed(args.reset))
