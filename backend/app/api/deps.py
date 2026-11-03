"""Shared API dependencies."""

from __future__ import annotations

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import AdminUser

_bearer = HTTPBearer(auto_error=False)


async def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
        if payload.get("scope") != "admin":
            raise unauthorized
        admin = await db.get(AdminUser, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthorized from None
    if admin is None or not admin.is_active:
        raise unauthorized
    return admin


def session_token(x_session_token: str | None = Header(default=None)) -> str | None:
    return x_session_token
