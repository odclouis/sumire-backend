"""Deterministic scoring for power stories. Arithmetic belongs in Python, not the LLM."""

from typing import Any

_ELEMENT_POINTS: dict[str, dict[str, int]] = {
    "situation_status": {"strong": 16, "partial": 10, "missing": 0},
    "action_status": {"strong": 22, "partial": 14, "missing": 0},
    "outcome_status": {"strong": 26, "partial": 14, "missing": 0},
    "evidence_status": {"strong": 16, "partial": 8, "missing": 0},
}


def compute_completeness(story: dict[str, Any]) -> int:
    """Score a power story's element statuses out of 80 points (depth/quality dimensions come later)."""
    return sum(
        points.get(story.get(status_key, "missing"), 0)
        for status_key, points in _ELEMENT_POINTS.items()
    )
