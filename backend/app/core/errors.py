"""Domain exceptions and FastAPI exception handlers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError

log = logging.getLogger(__name__)


class NotFoundError(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} not found")
        self.what = what


class ServiceUnavailableError(Exception):
    """An upstream dependency (LLM, Places, embeddings) failed."""


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ServiceUnavailableError)
    async def _unavailable(_: Request, exc: ServiceUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(ConnectionRefusedError)
    @app.exception_handler(OSError)
    async def _db_down(_: Request, exc: Exception) -> JSONResponse:
        log.error("Database error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Our service is temporarily unavailable. Please try again in a moment."
            },
        )
