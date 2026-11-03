"""Lightweight in-process sliding-window rate limiter.

Good enough for a single backend instance. For horizontally scaled deployments,
swap `InMemoryRateLimiter` for a Redis-backed implementation with the same
interface (documented in docs/deployment.md).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            retry_after = int(window_seconds - (now - q[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)
        # Opportunistic cleanup so memory stays bounded.
        if len(self._hits) > 10_000:
            for k in [k for k, v in self._hits.items() if not v][:5000]:
                del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()


limiter = InMemoryRateLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit_attr: str):
    """Build a FastAPI dependency enforcing `settings.<limit_attr>` requests/minute."""
    from app.core.config import get_settings

    async def _dep(request: Request) -> None:
        limit = getattr(get_settings(), limit_attr)
        limiter.check(f"{bucket}:{client_ip(request)}", limit)

    return _dep
