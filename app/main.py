import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def check_api_key() -> None:
    """The API refuses to start without its key (#457): a setting left out would otherwise open it to anyone who can
    reach it, with nothing to say so."""
    s = get_settings()
    if not s.service_api_key and not s.allow_no_api_key:
        raise RuntimeError("SERVICE_API_KEY isn't set, so the engine's API would accept anyone. Set it (the portal's "
                           "PORTAL_ENGINE_API_KEY must be the same), or ALLOW_NO_API_KEY=true for local development "
                           "only.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_api_key()
    get_settings().jobs_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="PDF Accessibility Remediation API",
    version="0.1.0",
    description="Headless, asynchronous PDF/UA remediation engine.",
    lifespan=lifespan,
)
app.include_router(router)
