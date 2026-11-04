"""Load the fictional sample properties, documents and a dev admin account.

    python -m scripts.seed            # only if the database has no properties
    python -m scripts.seed --reset    # delete ALL properties/documents first (dev only)

The backend also does this automatically on first start (AUTO_SEED_SAMPLE_DATA=true).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engine, get_sessionmaker, init_db
from app.models import Property, PropertyDocument
from app.rag import vectorstore
from app.rag.ingestion import delete_document_files
from app.services.bootstrap import ensure_admin, seed_sample_data


async def main(reset: bool) -> None:
    s = get_settings()
    await init_db()
    if reset:
        if s.environment == "production":
            sys.exit("Refusing to --reset in production.")
        async with get_sessionmaker()() as db:
            for doc in (await db.execute(select(PropertyDocument))).scalars():
                await delete_document_files(doc)
            for prop in (await db.execute(select(Property))).scalars().unique():
                await vectorstore.delete_property(prop.id)
                await db.delete(prop)
            await db.commit()
        print("Deleted all properties and documents.")
    await ensure_admin()
    lines = await seed_sample_data(force=reset)
    print("\n".join(lines) if lines else "Database already has properties - nothing to seed (use --reset to start over).")
    await dispose_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    asyncio.run(main(ap.parse_args().reset))
