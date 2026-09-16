"""Health check endpoint for verifying the service and its database connection are up."""

from fastapi import APIRouter

from app import db
from app.config import get_settings

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return service status, live database connectivity, and the running environment."""
    settings = get_settings()
    database_status = "connected" if db.check_database_connection() else "error"
    return {"status": "ok", "database": database_status, "environment": settings.environment}
