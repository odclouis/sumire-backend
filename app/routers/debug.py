"""Dev-only introspection endpoints. Not for production use."""

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.services import debug_state

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/last-extraction")
def get_last_extraction() -> dict:
    """Return the raw extraction from the most recently processed message, before persistence touched it."""
    if get_settings().environment == "production":
        raise HTTPException(status_code=404)

    extraction = debug_state.get_last_extraction()
    if extraction is None:
        raise HTTPException(status_code=404, detail="No extraction recorded yet")
    return extraction.model_dump()
