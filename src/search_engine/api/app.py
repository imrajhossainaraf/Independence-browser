"""FastAPI application factory for the search engine read-only HTTP API (Step 22)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from search_engine.api.middleware import (
    CorsMiddleware,
    HostHeaderMiddleware,
    RateLimiter,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from search_engine.api.routes.documents import router as documents_router
from search_engine.api.routes.health import router as health_router
from search_engine.api.routes.home import HomeCache
from search_engine.api.routes.home import router as home_router
from search_engine.api.routes.images import router as images_router
from search_engine.api.routes.news import router as news_router
from search_engine.api.routes.search import router as search_router
from search_engine.api.routes.suggest import router as suggest_router
from search_engine.api.security import is_loopback_host, validate_bearer_token
from search_engine.config import load_settings
from search_engine.errors import EngineError

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Enforces API key authentication per D15."""

    def __init__(self, app: object, db: Database, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.db = db
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        # Static client UI assets pass through so user can input API key in modal
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        is_loopback = is_loopback_host(self.settings.server_host)
        must_authenticate = self.settings.api_auth_required or not is_loopback

        if must_authenticate or auth_header is not None:
            if not auth_header:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "unauthorized",
                            "message": "Missing Authorization header",
                        }
                    },
                )
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "unauthorized",
                            "message": "Malformed Authorization header",
                        }
                    },
                )
            token = auth_header[7:].strip()
            with self.db.reader() as conn:
                if not validate_bearer_token(token, self.settings, conn):
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": {
                                "code": "unauthorized",
                                "message": "Invalid API key",
                            }
                        },
                    )

        return await call_next(request)


def create_app(db: Database, settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application for the search engine."""
    eff_settings = settings or load_settings()

    # Per D15: /docs and /redoc run only in development mode
    docs_url = "/docs" if eff_settings.development_mode else None
    redoc_url = "/redoc" if eff_settings.development_mode else None
    openapi_url = "/openapi.json" if eff_settings.development_mode else None

    app = FastAPI(
        title="Search Engine API",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # Store state
    app.state.db = db
    app.state.settings = eff_settings
    app.state.home_cache = HomeCache()

    # Register Exception Handlers for standard {error: {code, message}} responses
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "http_error"
        message = str(exc.detail)
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "http_error"))
            message = str(exc.detail.get("message", ""))
        else:
            status_codes = {
                400: "bad_request",
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                405: "method_not_allowed",
                429: "rate_limited",
                500: "internal_error",
                503: "service_unavailable",
            }
            code = status_codes.get(exc.status_code, "http_error")

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request parameters: " + str(exc.errors()),
                }
            },
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_exception_handler(
        request: Request, exc: sqlite3.OperationalError
    ) -> JSONResponse:
        err_msg = str(exc).lower()
        if "locked" in err_msg or "busy" in err_msg:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "storage_busy",
                        "message": "Database is temporarily busy. Please retry shortly.",
                    }
                },
            )
        if "no such table: doc_fts" in err_msg:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "index_unavailable",
                        "message": (
                            "Search index is unavailable. Run 'search-engine index rebuild'."
                        ),
                    }
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "database_error",
                    "message": "Internal database operational error.",
                }
            },
        )

    @app.exception_handler(EngineError)
    async def engine_exception_handler(request: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak traceback to client
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected server error occurred.",
                }
            },
        )

    # Mount API routers under /api/v1
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health_router)
    api_v1.include_router(home_router)
    api_v1.include_router(news_router)
    api_v1.include_router(search_router)
    api_v1.include_router(suggest_router)
    api_v1.include_router(images_router)
    api_v1.include_router(documents_router)
    app.include_router(api_v1)

    # Mount static thin web client assets (Step 23, Milestone M2)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        @app.get("/news", include_in_schema=False)
        def news_page() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    # Attach middlewares in reverse execution order (last added = outermost)
    app.add_middleware(AuthenticationMiddleware, db=db, settings=eff_settings)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(limit_rpm=eff_settings.api_rate_limit_rpm),
    )
    app.add_middleware(HostHeaderMiddleware, allowed_hosts=eff_settings.api_allowed_hosts)
    app.add_middleware(
        CorsMiddleware,
        enabled=eff_settings.api_cors_enabled,
        allowed_origins=eff_settings.api_cors_origins,
    )
    app.add_middleware(SecurityHeadersMiddleware, settings=eff_settings)

    return app


def export_openapi_schema(app: FastAPI, destination: Path) -> None:
    """Export the OpenAPI 3 schema to a JSON file."""
    schema = app.openapi()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(schema, indent=2), encoding="utf-8")
