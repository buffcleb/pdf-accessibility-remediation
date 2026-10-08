import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().jobs_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="PDF Accessibility Remediation API",
    version="0.1.0",
    description="Headless, asynchronous PDF/UA remediation engine.",
    lifespan=lifespan,
)
app.include_router(router)
