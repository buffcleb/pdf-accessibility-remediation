"""Portal API application."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from portal.config import get_portal_settings
from portal.db import get_engine
from portal.routers import admin, auth, branding, email_reports, groups, help, jobs, me, queues, reports, versions
from portal.security.tokens import check_config

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_config()  # refuse to start without signing keys and a secret
    if get_portal_settings().s3_create_bucket:
        from portal.storage import get_storage
        get_storage().ensure_bucket()
    yield


def create_app() -> FastAPI:
    settings = get_portal_settings()
    docs = os.environ.get("PORTAL_ENABLE_DOCS", "").lower() in ("1", "true", "yes")
    app = FastAPI(
        title="PDF Remediation Portal API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": {
            "error": "invalid_request", "message": "Some fields are invalid.", "fields": fields}})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Log the details for operators; never send stack traces or internals to clients.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": {
            "error": "server_error", "message": "Something went wrong. Try again later."}})

    for r in (auth.router, me.router, jobs.router, versions.router, groups.router, reports.router, admin.router,
              branding.router, help.router, queues.router, email_reports.router):
        app.include_router(r, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", tags=["health"])
    def health():
        try:
            with get_engine().connect() as conn:
                conn.execute(text("select 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return JSONResponse(status_code=200 if db_ok else 503,
                            content={"status": "ok" if db_ok else "degraded", "database": db_ok})

    return app


app = create_app()
