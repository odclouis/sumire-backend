"""In-memory holder of the most recent extraction, for the dev-only debug endpoint."""

from app.services.llm import Extraction

_last_extraction: Extraction | None = None


def set_last_extraction(extraction: Extraction) -> None:
    """Record the extraction exactly as the model produced it, before persistence normalizes it."""
    global _last_extraction
    _last_extraction = extraction


def get_last_extraction() -> Extraction | None:
    """Return the most recently recorded extraction, or None if no message has been processed yet."""
    return _last_extraction
