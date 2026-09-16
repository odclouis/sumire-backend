"""FastAPI application entrypoint for the Sumire backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers import health

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sumire")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log a startup message announcing the running environment before serving requests."""
    settings = get_settings()
    logger.info("Sumire backend starting up in %s environment", settings.environment)
    yield


app = FastAPI(title="Sumire Backend", lifespan=lifespan)
app.include_router(health.router)
