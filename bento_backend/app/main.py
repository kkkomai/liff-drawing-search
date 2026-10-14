"""FastAPI application factory.

Routes are mounted under ``/api`` and CORS is restricted to the configured
LIFF origins (a LIFF app served from liff.line.me needs an explicit allow-list
or ``*`` — configurable, never hard-coded).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin, auth, config_api, orders
from .config import assert_production_ready, get_settings
from .db import get_conn, migrate
from .errors import ApiError

logger = logging.getLogger("bento")


def _error_body(status_code: int, error: str, message: str) -> dict:
    return {"success": False, "error": error, "message": message}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # A prod deploy with dev credentials or a missing LIFF id must die here, at
    # startup, not at 3am when a user's order silently fails.
    if settings.is_production:
        checks = assert_production_ready(settings)
        logger.info("production config verified: %s", "; ".join(checks))
    # Migrations run at startup so a fresh deploy self-heals; already-applied
    # versions are skipped via schema_migrations.
    applied = migrate(get_conn())
    if applied:
        logger.info("applied migrations: %s", applied)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Bento Order API",
        version="1.0.0",
        description="LINE LIFF お弁当注文システム バックエンド",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        # liff.line.me is always allowed; dev/staging hostnames come from
        # BENTO_CORS_ORIGINS / BENTO_EXTRA_CORS_ORIGINS.
        allow_origins=settings.effective_cors_origins,
        allow_credentials=False,  # bearer tokens only; no cookies are used
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Line-User-Id"],
        max_age=600,
    )

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.status_code, exc.error, exc.message)
        )

    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content=_error_body(400, "invalid_request", str(exc)))

    app.include_router(config_api.router)
    app.include_router(auth.router)
    app.include_router(orders.router)
    app.include_router(admin.router)

    # Optionally serve the LIFF page from the same origin. When BENTO_FRONTEND_DIR
    # is set, the LIFF endpoint URL and the API share one https origin, so the
    # browser makes same-origin requests and CORS stops being a factor.
    # Mounted last (see below) so it never shadows the API routes.

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "app_env": settings.app_env,
            "line_verification_enabled": settings.line_verification_enabled,
            "dev_auth_enabled": settings.dev_auth_enabled,
            "liff_configured": settings.liff_configured,
            "liff_app_url": settings.effective_liff_app_url,
            "api_base_url": settings.public_api_base_url,
        }

    # Mounted AFTER the API routes so "/" only catches what the API does not
    # define; a mount registered earlier would shadow /health and /api/*.
    if settings.frontend_dir:
        from pathlib import Path

        from fastapi.staticfiles import StaticFiles

        frontend_path = Path(settings.frontend_dir)
        if frontend_path.is_dir():
            app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
        else:
            logger.warning("BENTO_FRONTEND_DIR is not a directory: %s", frontend_path)

    return app


app = create_app()