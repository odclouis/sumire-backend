"""Anti-inflation: a user pushing to close an incomplete story must not raise its status or
completeness score on its own. See prompts.py's "## Anti-inflation rules" for what the model is
told; this test covers the deterministic side - given a compliant extraction (one that correctly
leaves a status unchanged because the message added no new factual content), the merge and
scoring pipeline (app/db.py, app/services/scoring.py) must not inflate anything on its own
either. It cannot verify live model compliance with the prompt rules - that needs an eval against
the real API, not a unit test - only that the deterministic backend doesn't undo a compliant
model's honesty.
"""

import asyncio
from unittest.mock import patch

from app.models import IncomingMessage
from app.services import pipeline
from app.services.context import UserContext
from app.services.llm import Extraction
from tests.fake_supabase import FakeClient

_STORY_FIELDS = {
    "title": "Vendor negotiation",
    "situation": "The vendor's price hike would have blown a $40k hole in the budget.",
    "situation_status": "strong",
    "action": "You pushed back and proposed a phased rollout instead.",
    "action_status": "strong",
    "outcome": "The vendor agreed to hold pricing.",
    "outcome_status": "partial",
    "evidence": "Tom said it was the strongest pushback he'd seen from the team.",
    "evidence_status": "strong",
    "gaps": [
        {
            "element": "outcome",
            "gap_description": "No number for how much the phased rollout actually saved.",
            "follow_up_question": "What was the dollar or percentage saved by holding pricing?",
        }
    ],
}


def _run(msg: IncomingMessage, extraction: Extraction, client: FakeClient, context: UserContext) -> None:
    with (
        patch("app.db.get_client", return_value=client),
        patch("app.services.context.get_client", return_value=client),
        patch("app.services.idempotency.already_processed", return_value=False),
        patch("app.services.context.get_user_context", return_value=context),
        patch("app.services.llm.extract_from_message", return_value=extraction),
        patch("app.services.llm.generate_response", return_value="Still needs a number on the savings."),
    ):
        async def fake_send(*_a: object, **_kw: object) -> dict:
            return {"ok": True}

        with patch("app.services.whatsapp.send_message", side_effect=fake_send):
            asyncio.run(pipeline.process_incoming_message(msg))


def test_pushing_to_close_does_not_inflate_status_or_completeness() -> None:
    """"That's everything, can we call it done" carries no new factual content, so a compliant
    extraction leaves outcome_status "partial" - the merge/scoring pipeline must preserve that
    and must not raise completeness above what it already was.
    """
    client = FakeClient()
    user = client.table("user_profiles").insert({"phone": "1"}).execute().data[0]
    user_id = user["id"]

    story = client.table("power_stories").insert(
        {
            "user_id": user_id,
            **_STORY_FIELDS,
            "completeness": 86,  # situation 20 + action 28 + outcome partial 18 + evidence 20
            "status": "building",
            "stakeholders": [],
            "skills_used": [],
        }
    ).execute().data[0]

    context = UserContext(None, None, None, None)
    msg = IncomingMessage(
        phone="1",
        message_id="m1",
        timestamp="2024-01-01T00:00:00Z",
        type="text",
        text="That's everything, can we call it done?",
    )

    # A compliant extraction: no new factual content was given, so outcome_status correctly
    # stays "partial" rather than being nudged to "strong" by the request to close it out.
    extraction = Extraction.model_validate(
        {
            "classification": "question",
            "signal_quality": "simple",
            "skills": [],
            "stakeholders": [],
            "corrections": [],
            "correction_target": None,
            "story_action": "update_existing",
            "existing_story_id": story["id"],
            "story": _STORY_FIELDS,
            "summary": "User asked to close the story out; no new factual content given.",
        }
    )

    _run(msg, extraction, client, context)

    updated = client.table("power_stories").select("*").eq("id", story["id"]).limit(1).execute().data[0]

    assert updated["outcome_status"] == "partial", "pushing to close must not upgrade an unproven outcome"
    assert updated["completeness"] == 86, "completeness must not increase without new factual content"
