"""Background orchestration for processing an inbound WhatsApp message end to end."""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from app import db
from app.models import IncomingMessage
from app.services import context as context_service
from app.services import debug_state, idempotency, llm, whatsapp
from app.services.context import UserContext
from app.services.llm import Extraction
from app.services.transcription import transcribe_audio

logger = logging.getLogger("sumire.pipeline")

FALLBACK_REPLY = "Something went wrong on my end — try again in a moment."

EXTRACTIONS_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "extractions.jsonl"


def _log_extraction(message: str, extraction: Extraction) -> None:
    """Append the extraction input/output pair to the eval fixture log."""
    EXTRACTIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "input": message,
        "output": extraction.model_dump(),
    }
    with EXTRACTIONS_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _persist_story(
    user_id: str,
    extraction: Extraction,
    conversation_id: str,
    skill_names: list[str],
    stakeholder_ids: list[str],
) -> str | None:
    """Create or merge the power story named by story_action; return its id, or None if there isn't one."""
    if extraction.story_action == "new" and extraction.story:
        story = db.create_power_story(user_id, extraction.story.model_dump(), conversation_id, skill_names, stakeholder_ids)
        return story["id"]
    if extraction.story_action == "update_existing" and extraction.story and extraction.existing_story_id:
        story = db.update_power_story(extraction.existing_story_id, extraction.story.model_dump(), skill_names, stakeholder_ids)
        return story["id"]
    return None


def _build_pending_correction(extraction: Extraction, context: UserContext) -> dict:
    """Snapshot an ambiguous correction and its candidates' titles/names for later disambiguation."""
    target = extraction.correction_target
    candidate_story_ids = target.candidate_story_ids if target else []
    candidate_stakeholder_ids = target.candidate_stakeholder_ids if target else []
    story_titles = {story["id"]: story["title"] for story in context.inprogress_stories}
    stakeholder_names = {stakeholder["id"]: stakeholder["name"] for stakeholder in context.top_stakeholders}
    return {
        "corrections": [correction.model_dump() for correction in extraction.corrections],
        "correction_target": target.model_dump() if target else None,
        "candidate_stories": [
            {"id": story_id, "title": story_titles[story_id]}
            for story_id in candidate_story_ids
            if story_id in story_titles
        ],
        "candidate_stakeholders": [
            {"id": stakeholder_id, "name": stakeholder_names[stakeholder_id]}
            for stakeholder_id in candidate_stakeholder_ids
            if stakeholder_id in stakeholder_names
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }


def _resolve_corrections(user_id: str, extraction: Extraction, context: UserContext, fallback_story_id: str | None) -> None:
    """Apply this message's corrections if their target is resolved, else pend them and ask.

    Applying a correction to the wrong story or person is worse than not applying it, so
    "ambiguous" blocks everything this message would have corrected rather than guessing.
    """
    if not extraction.corrections:
        return

    if llm.is_ambiguous_correction(extraction):
        db.set_pending_correction(user_id, _build_pending_correction(extraction, context))
        logger.info("Stored pending correction for user_id=%s pending disambiguation", user_id)
        return

    target = extraction.correction_target
    story_id = target.story_id if target else None
    story_id = story_id or fallback_story_id
    stakeholder_id = target.stakeholder_id if target else None

    for correction in extraction.corrections:
        if correction.entity_type == "stakeholder":
            db.apply_stakeholder_correction(
                user_id, correction.incorrect_value, correction.correct_value, story_id, stakeholder_id
            )
        elif correction.entity_type == "skill":
            db.apply_skill_correction(user_id, correction.incorrect_value, correction.correct_value, story_id)
        # story_element corrections are already reflected in this turn's story text via the
        # normal extraction -> merge path; nothing further to persist for those.

    db.clear_pending_correction(user_id)


async def process_incoming_message(message: IncomingMessage) -> None:
    """Look up/create the user, transcribe, extract, persist, reply, and log the outcome."""
    logger.info(
        "Processing inbound message %s from %s (type=%s)",
        message.message_id,
        message.phone,
        message.type,
    )

    try:
        if idempotency.already_processed(message.message_id):
            return

        user = db.get_user_by_phone(message.phone)
        if user is None:
            user = db.create_user(message.phone)
            logger.info("Created new user %s for phone %s", user["id"], message.phone)
        user_id = user["id"]

        if message.type == "audio" and message.media_id:
            audio_bytes, mime_type = await whatsapp.download_media(message.media_id)
            transcript = await transcribe_audio(audio_bytes, mime_type)
            media_type = "voice"
        else:
            transcript = message.text or ""
            media_type = "text"

        user_context = context_service.get_user_context(user_id)

        extract_started = time.perf_counter()
        extraction = llm.extract_from_message(transcript, user_context)
        extract_ms = (time.perf_counter() - extract_started) * 1000

        debug_state.set_last_extraction(extraction)
        _log_extraction(transcript, extraction)

        inbound = db.create_conversation(
            user_id=user_id,
            direction="inbound",
            content=transcript,
            media_type=media_type,
            whatsapp_message_id=message.message_id,
            classification=extraction.classification,
        )

        skill_names: list[str] = []
        for skill in extraction.skills:
            db.upsert_skill(user_id, skill.name, skill.category)
            skill_names.append(skill.name)

        stakeholder_ids = [
            db.upsert_stakeholder(user_id, stakeholder.name, stakeholder.role, stakeholder.relationship, stakeholder.sentiment)["id"]
            for stakeholder in extraction.stakeholders
        ]

        story_id = _persist_story(user_id, extraction, inbound["id"], skill_names, stakeholder_ids)

        _resolve_corrections(user_id, extraction, user_context, story_id)

        db.create_career_update(
            user_id=user_id,
            conversation_id=inbound["id"],
            summary=extraction.summary,
            extracted_skills=skill_names,
            extracted_story=story_id,
            extracted_stakeholders=stakeholder_ids,
        )

        response_started = time.perf_counter()
        reply = llm.generate_response(transcript, extraction, user_context)
        response_ms = (time.perf_counter() - response_started) * 1000

        await whatsapp.send_message(message.phone, reply)

        db.create_conversation(
            user_id=user_id,
            direction="outbound",
            content=reply,
            media_type="text",
        )

        logger.info(
            "message_id=%s classification=%s signal_quality=%s story_action=%s "
            "skills=%d stakeholders=%d extract_ms=%.0f response_ms=%.0f",
            message.message_id,
            extraction.classification,
            extraction.signal_quality,
            extraction.story_action,
            len(extraction.skills),
            len(extraction.stakeholders),
            extract_ms,
            response_ms,
        )

    except Exception:
        logger.exception(
            "Failed to process message %s from %s", message.message_id, message.phone
        )
        try:
            await whatsapp.send_message(message.phone, FALLBACK_REPLY)
        except Exception:
            logger.exception("Failed to send fallback reply for message %s", message.message_id)
