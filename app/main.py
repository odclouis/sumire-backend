"""FastAPI application entrypoint for the Sumire backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import get_settings
from app.routers import debug, health, webhook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sumire")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log a startup message and check for schema drift before serving requests."""
    settings = get_settings()
    logger.info("Sumire backend starting up in %s environment", settings.environment)

    try:
        missing_columns = db.check_schema()
    except Exception:
        logger.warning("Could not run startup schema drift check", exc_info=True)
    else:
        for column in missing_columns:
            logger.warning("Schema drift: code expects %s but the database does not have it", column)

    yield


app = FastAPI(title="Sumire Backend", lifespan=lifespan)
app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(debug.router)
