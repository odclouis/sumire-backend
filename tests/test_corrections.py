"""A correction is a swap, not a deletion: apply_stakeholder_correction / apply_skill_correction
must, in one call, (a) delete/decrement the incorrect entity, (b) create or enrich the correct
one, (c) swap the story's array UUIDs/names, and (d) rewrite any story prose naming the incorrect
entity. All four regressed independently before (see app/db.py's history), so each is asserted
here explicitly.
"""

from unittest.mock import patch

from app import db
from tests.fake_supabase import FakeClient


def _seed_story_with_incorrect_stakeholder(client: FakeClient, user_id: str) -> tuple[dict, dict]:
    """Seed a stakeholder (Sarah, two prior mentions) and a story whose prose names her twice."""
    sarah = (
        client.table("stakeholders")
        .insert(
            {
                "user_id": user_id,
                "name": "Sarah",
                "role": "Finance decision-maker",
                "relationship": "sponsor",
                "sentiment": "positive",
                "mention_count": 2,
                "first_seen": "2024-01-01T00:00:00Z",
                "last_seen": "2024-01-01T00:00:00Z",
            }
        )
        .execute()
        .data[0]
    )

    story = (
        client.table("power_stories")
        .insert(
            {
                "user_id": user_id,
                "title": "Budget negotiation",
                "situation": "Needed budget approval for new tooling.",
                "situation_status": "strong",
                "action": "Presented it to Sarah, who has final say on the budget.",
                "action_status": "strong",
                "outcome": "Got the budget approved.",
                "outcome_status": "strong",
                "evidence": "Sarah said afterwards it was the clearest pitch she'd seen.",
                "evidence_status": "strong",
                "stakeholders": [sarah["id"]],
                "skills_used": [],
            }
        )
        .execute()
        .data[0]
    )

    # Both of Sarah's mentions came from this story, matching the reported scenario -
    # mention_count(2) should fully deplete and delete her.
    for _ in range(2):
        client.table("career_updates").insert(
            {
                "user_id": user_id,
                "conversation_id": "conv-x",
                "summary": "x",
                "extracted_skills": [],
                "extracted_story": story["id"],
                "extracted_stakeholders": [sarah["id"]],
            }
        ).execute()

    return sarah, story


def test_stakeholder_correction_swaps_atomically() -> None:
    """One correction call must delete the incorrect stakeholder, create the correct one
    enriched from her info, swap the story's stakeholder UUIDs, and rewrite its prose.
    """
    client = FakeClient()
    user_id = "user-1"
    sarah, story = _seed_story_with_incorrect_stakeholder(client, user_id)

    with patch("app.db.get_client", return_value=client):
        db.apply_stakeholder_correction(user_id, "Sarah", "Tom", story["id"])

    stakeholder_rows = client.table("stakeholders").select("*").eq("user_id", user_id).execute().data
    names = {row["name"] for row in stakeholder_rows}

    # (a) delete/decrement the incorrect stakeholder - both her mentions came from this story.
    assert "Sarah" not in names, "Sarah should have been deleted: both her mentions came from this story"

    # (b) create or enrich the correct stakeholder.
    assert "Tom" in names, "Tom should have been created since he didn't already exist"
    tom = next(row for row in stakeholder_rows if row["name"] == "Tom")
    assert tom["role"] == "Finance decision-maker"
    assert tom["relationship"] == "sponsor"
    assert tom["sentiment"] == "positive"

    updated_story = client.table("power_stories").select("*").eq("id", story["id"]).limit(1).execute().data[0]

    # (c) swap the UUIDs in power_stories.stakeholders.
    assert sarah["id"] not in updated_story["stakeholders"]
    assert tom["id"] in updated_story["stakeholders"]

    # (d) rewrite story prose naming the incorrect person.
    assert "Sarah" not in updated_story["action"]
    assert "Sarah" not in updated_story["evidence"]
    assert "Tom" in updated_story["action"]
    assert "Tom" in updated_story["evidence"]


def test_stakeholder_correction_enriches_existing_correct_stakeholder() -> None:
    """If the correct stakeholder already exists, enrich it (never-downgrade) instead of
    creating a duplicate.
    """
    client = FakeClient()
    user_id = "user-1"
    sarah, story = _seed_story_with_incorrect_stakeholder(client, user_id)
    tom = (
        client.table("stakeholders")
        .insert(
            {
                "user_id": user_id,
                "name": "Tom",
                "role": None,
                "relationship": "unknown",
                "sentiment": "unknown",
                "mention_count": 1,
                "first_seen": "x",
                "last_seen": "x",
            }
        )
        .execute()
        .data[0]
    )

    with patch("app.db.get_client", return_value=client):
        db.apply_stakeholder_correction(user_id, "Sarah", "Tom", story["id"])

    stakeholder_rows = client.table("stakeholders").select("*").eq("user_id", user_id).execute().data
    assert len([row for row in stakeholder_rows if row["name"] == "Tom"]) == 1, "must not create a duplicate Tom"
    updated_tom = next(row for row in stakeholder_rows if row["id"] == tom["id"])
    assert updated_tom["role"] == "Finance decision-maker"
    assert updated_tom["relationship"] == "sponsor"


def test_skill_correction_swaps_atomically() -> None:
    """A skill correction must delete/decrement the incorrect skill, create the correct one if
    missing, and swap it into the story's skills_used array.
    """
    client = FakeClient()
    user_id = "user-1"

    client.table("skills").insert(
        {
            "user_id": user_id,
            "name": "Python",
            "category": "technical",
            "mention_count": 1,
            "first_seen": "x",
            "last_seen": "x",
        }
    ).execute()
    story = (
        client.table("power_stories")
        .insert(
            {
                "user_id": user_id,
                "title": "t",
                "situation": "s",
                "situation_status": "strong",
                "action": "a",
                "action_status": "strong",
                "outcome": "o",
                "outcome_status": "strong",
                "evidence": "e",
                "evidence_status": "strong",
                "stakeholders": [],
                "skills_used": ["Python"],
            }
        )
        .execute()
        .data[0]
    )
    client.table("career_updates").insert(
        {
            "user_id": user_id,
            "conversation_id": "conv-x",
            "summary": "x",
            "extracted_skills": ["Python"],
            "extracted_story": story["id"],
            "extracted_stakeholders": [],
        }
    ).execute()

    with patch("app.db.get_client", return_value=client):
        db.apply_skill_correction(user_id, "Python", "Rust", story["id"])

    skill_rows = client.table("skills").select("*").eq("user_id", user_id).execute().data
    names = {row["name"] for row in skill_rows}
    assert "Python" not in names
    assert "Rust" in names

    updated_story = client.table("power_stories").select("*").eq("id", story["id"]).limit(1).execute().data[0]
    assert "Python" not in updated_story["skills_used"]
    assert "Rust" in updated_story["skills_used"]
