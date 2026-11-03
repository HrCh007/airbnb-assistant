"""Create or reset an admin user.

    python -m scripts.create_admin --email owner@example.com
(prompts for the password; or set ADMIN_PASSWORD in the environment)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os

from sqlalchemy import select

from app.core.database import dispose_engine, get_sessionmaker
from app.core.security import hash_password
from app.models import AdminUser


async def main(email: str, password: str, name: str | None) -> None:
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(AdminUser).where(AdminUser.email == email.lower()))).scalar_one_or_none()
        if user is None:
            db.add(AdminUser(email=email.lower(), password_hash=hash_password(password), full_name=name))
            print(f"Created admin {email}")
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
            print(f"Reset password for {email}")
        await db.commit()
    await dispose_engine()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name")
    args = ap.parse_args()
    pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(pw) < 12:
        raise SystemExit("Password must be at least 12 characters.")
    asyncio.run(main(args.email, pw, args.name))
