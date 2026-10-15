"""Portal API application."""
from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text

from portal.config import get_portal_settings
from portal.db import get_engine
from portal.routers import admin, auth, branding, email_reports, groups, help, jobs, me, queues, reports, versions
from portal.security.tokens import check_config

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

# The routes that take files, and the setting that bounds what one request to each may carry (in MB)
_UPLOAD_LIMITS = (
    (re.compile(r"/jobs$"), lambda rs: rs.max_files_per_upload * rs.max_file_mb),
    (re.compile(r"/jobs/[^/]+/help$"), lambda rs: rs.help_max_request_mb),
    (re.compile(r"/help/[^/]+/messages$"), lambda rs: rs.help_max_request_mb),
    (re.compile(r"/help/[^/]+/corrected$"), lambda rs: rs.max_file_mb),
)
_BODY_SLACK = 1024 * 1024  # the multipart framing and the form fields around the files


def upload_limit_bytes(path: str, rs) -> int | None:
    """What a request to this path may carry, or None for a path that takes no files."""
    if not path.startswith(API_PREFIX):
        return None
    rest = path[len(API_PREFIX):]
    for pattern, limit_mb in _UPLOAD_LIMITS:
        if pattern.search(rest):
            return limit_mb(rs) * 1024 * 1024 + _BODY_SLACK
    return None


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
    async def body_size(request: Request, call_next):
        """A request whose declared size is past what its route's files may add up to is refused before any of
        it is read: the routes' own file-size and file-count checks run only after Starlette has spooled every
        part to disk, so an account could fill the pod's temporary space with bodies far larger than the limits."""
        length = request.headers.get("content-length")
        if request.method == "POST" and length and length.isdigit():
            def limit():
                from portal import settings_store
                from portal.db import open_session
                with closing(open_session()) as db:
                    return upload_limit_bytes(request.url.path, settings_store.load(db))
            cap = await run_in_threadpool(limit)
            if cap is not None and int(length) > cap:
                return JSONResponse(status_code=413, content={"detail": {
                    "error": "request_too_large",
                    "message": f"This request is larger than the files it may carry add up to ({cap // (1024 * 1024)} MB)."}})
        return await call_next(request)

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
