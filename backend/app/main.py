"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import admin, public
from app.core.config import get_settings
from app.core.database import dispose_engine, get_engine
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    problems = s.validate_for_production()
    if problems:
        raise RuntimeError("Invalid production configuration: " + " ".join(problems))
    log.info(
        "Starting %s (env=%s, llm=%s, embeddings=%s, places=%s)",
        s.app_name, s.environment, "claude" if s.llm_enabled else "offline",
        s.resolved_embedding_provider, s.resolved_places_provider,
    )
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Delhi Stays Guest Assistant API",
        version="1.0.0",
        description=(
            "Location-aware, multi-property RAG assistant for Airbnb guests in Delhi. "
            "Guest endpoints under /api, admin endpoints under /api/admin (Bearer JWT)."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
    )
    install_exception_handlers(app)
    app.include_router(public.router)
    app.include_router(admin.auth_router)
    app.include_router(admin.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        db_ok = True
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "database": db_ok}

    return app


app = create_app()
