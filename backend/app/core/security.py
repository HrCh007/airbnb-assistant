"""Password hashing and JWT helpers for admin authentication."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings

_ALGORITHM = "HS256"
_dev_fallback_key = secrets.token_urlsafe(48)


def _signing_key() -> str:
    key = get_settings().secret_key
    # In development without SECRET_KEY we use a per-process random key
    # (tokens are invalidated on restart). Production refuses to start without one.
    return key or _dev_fallback_key


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; reject longer to avoid silent truncation.
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise ValueError("Password too long (max 72 bytes).")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, extra: dict | None = None) -> tuple[str, datetime]:
    ttl = get_settings().admin_token_ttl_minutes
    expires = datetime.now(UTC) + timedelta(minutes=ttl)
    payload = {"sub": subject, "exp": expires, "iat": datetime.now(UTC), "scope": "admin"}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _signing_key(), algorithm=_ALGORITHM), expires


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, _signing_key(), algorithms=[_ALGORITHM])
