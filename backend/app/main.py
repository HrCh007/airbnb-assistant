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
from app.llm import ollama
from app.rag import vectorstore

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    from app.services.bootstrap import bootstrap

    log.info(
        "Starting %s (env=%s, llm=ollama:%s, vector store=chromadb/%s, data=%s)",
        s.app_name, s.environment, s.ollama_model, s.chroma_mode, s.data_path,
    )
    await bootstrap()
    st = await ollama.status(force=True)
    if not st["reachable"]:
        log.warning("Ollama is not reachable at %s - answers use extractive mode until it is.", s.ollama_url)
    elif not st["model_ready"]:
        log.warning("Ollama model %s is not downloaded yet - run: ollama pull %s", s.ollama_model, s.ollama_model)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Hideout Studios Guest Assistant API",
        version="2.0.0",
        description=(
            "Zero-cost, locally hosted RAG assistant for Airbnb guests in Delhi "
            "(Ollama + sentence-transformers + ChromaDB + SQLite + OpenStreetMap). "
            "Guest endpoints under /api, admin endpoints under /api/admin (Bearer JWT)."
        ),
        lifespan=lifespan,
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
        """Liveness/readiness: the API is healthy when SQLite and ChromaDB respond.
        Ollama is reported but optional (answers fall back to extractive mode)."""
        db_ok = True
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        chroma_ok = await vectorstore.heartbeat()
        llm = await ollama.status()
        return {
            "status": "ok" if db_ok and chroma_ok else "degraded",
            "database": db_ok,
            "vector_store": chroma_ok,
            "llm": "ready" if llm["model_ready"] else ("model_not_downloaded" if llm["reachable"] else "unreachable"),
        }

    return app


app = create_app()
