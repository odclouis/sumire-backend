"""Deterministic scoring for power stories. Arithmetic belongs in Python, not the LLM."""

from typing import Any

# Elements-only weights, rescaled to sum to 100. The original design also has DEPTH and QUALITY
# dimensions that were deferred (not implemented) - when either ships, these weights need
# rescaling again so a fully-answered story with no remaining gaps still reaches 100, not a
# fraction of it.
_ELEMENT_POINTS: dict[str, dict[str, int]] = {
    "situation_status": {"strong": 20, "partial": 12, "missing": 0},
    "action_status": {"strong": 28, "partial": 18, "missing": 0},
    "outcome_status": {"strong": 32, "partial": 18, "missing": 0},
    "evidence_status": {"strong": 20, "partial": 10, "missing": 0},
}


def compute_completeness(story: dict[str, Any]) -> int:
    """Score a power story's element statuses out of 100 (elements only; depth/quality come later)."""
    return sum(
        points.get(story.get(status_key, "missing"), 0)
        for status_key, points in _ELEMENT_POINTS.items()
    )
