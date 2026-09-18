"""Supabase client and data-access helpers for the collection layer schema."""

import logging
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.config import get_settings
from app.services.scoring import compute_completeness

logger = logging.getLogger("sumire.db")

PENDING_CORRECTION_TTL = timedelta(hours=24)


@lru_cache
def get_client() -> Client:
    """Create (and cache) a Supabase client authenticated with the service role key."""
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)


def normalize_phone(phone: str) -> str:
    """Strip every non-digit character from a phone number so lookups and storage are consistent."""
    return re.sub(r"\D", "", phone)


def get_user_by_phone(phone: str) -> dict | None:
    """Return the user profile matching the given phone number, or None if no match exists."""
    client = get_client()
    normalized = normalize_phone(phone)
    response = client.table("user_profiles").select("*").eq("phone", normalized).limit(1).execute()
    return response.data[0] if response.data else None


def create_user(phone: str, name: str | None = None) -> dict:
    """Insert a new user profile with a normalized phone number and return the created row."""
    client = get_client()
    payload: dict[str, str] = {"phone": normalize_phone(phone)}
    if name is not None:
        payload["name"] = name
    response = client.table("user_profiles").insert(payload).execute()
    return response.data[0]


def create_conversation(
    user_id: str,
    direction: str,
    content: str,
    media_type: str = "text",
    whatsapp_message_id: str | None = None,
    classification: str | None = None,
) -> dict:
    """Insert a new conversation row logging an inbound or outbound message and return it."""
    client = get_client()
    payload: dict[str, str] = {
        "user_id": user_id,
        "direction": direction,
        "raw_content": content,
        "media_type": media_type,
    }
    if whatsapp_message_id is not None:
        payload["whatsapp_message_id"] = whatsapp_message_id
    if classification is not None:
        payload["classification"] = classification
    response = client.table("conversations").insert(payload).execute()
    return response.data[0]


def get_conversation_by_whatsapp_message_id(whatsapp_message_id: str) -> dict | None:
    """Return the conversation row for a given WhatsApp message id, or None if not yet recorded."""
    client = get_client()
    response = (
        client.table("conversations")
        .select("id")
        .eq("whatsapp_message_id", whatsapp_message_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def get_recent_conversations(user_id: str, limit: int = 10) -> list[dict]:
    """Return the most recent conversations for a user, newest first."""
    client = get_client()
    response = (
        client.table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data


def check_database_connection() -> bool:
    """Return True if a trivial query against user_profiles succeeds, False otherwise."""
    try:
        get_client().table("user_profiles").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def upsert_skill(user_id: str, name: str, category: str) -> dict:
    """Bump mention_count on a case-insensitive name match, else insert a new skill at count 1."""
    client = get_client()
    now = datetime.now(UTC).isoformat()
    existing_resp = (
        client.table("skills")
        .select("id, mention_count")
        .eq("user_id", user_id)
        .ilike("name", name)
        .limit(1)
        .execute()
    )
    if existing_resp.data:
        existing = existing_resp.data[0]
        response = (
            client.table("skills")
            .update({"mention_count": existing["mention_count"] + 1, "last_seen": now})
            .eq("id", existing["id"])
            .execute()
        )
    else:
        response = (
            client.table("skills")
            .insert(
                {
                    "user_id": user_id,
                    "name": name,
                    "category": category,
                    "mention_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
            )
            .execute()
        )
    if not response.data:
        logger.error("upsert_skill returned no row for user_id=%s name=%r", user_id, name)
        raise RuntimeError(f"upsert_skill returned no row for skill {name!r}")
    return response.data[0]


def upsert_stakeholder(user_id: str, name: str, role: str | None, relationship: str, sentiment: str) -> dict:
    """Bump mention_count and enrich role/relationship/sentiment on a case-insensitive name match, else insert new.

    Enrichment never overwrites a specific value with a vaguer one, mirroring the story elements'
    never-downgrade rule: role only fills in from null, relationship only upgrades away from
    "unknown", and sentiment only changes away from "unknown" once something concrete is known.
    """
    client = get_client()
    now = datetime.now(UTC).isoformat()
    existing_resp = (
        client.table("stakeholders")
        .select("id, mention_count, role, relationship, sentiment")
        .eq("user_id", user_id)
        .ilike("name", name)
        .limit(1)
        .execute()
    )
    if existing_resp.data:
        existing = existing_resp.data[0]
        update_payload: dict[str, Any] = {
            "mention_count": existing["mention_count"] + 1,
            "last_seen": now,
        }
        if existing.get("role") is None and role is not None:
            update_payload["role"] = role
        if existing.get("relationship") == "unknown" and relationship != "unknown":
            update_payload["relationship"] = relationship
        if sentiment != "unknown" or existing.get("sentiment") == "unknown":
            update_payload["sentiment"] = sentiment
        response = (
            client.table("stakeholders")
            .update(update_payload)
            .eq("id", existing["id"])
            .execute()
        )
    else:
        response = (
            client.table("stakeholders")
            .insert(
                {
                    "user_id": user_id,
                    "name": name,
                    "role": role,
                    "relationship": relationship,
                    "sentiment": sentiment,
                    "mention_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
            )
            .execute()
        )
    if not response.data:
        logger.error("upsert_stakeholder returned no row for user_id=%s name=%r", user_id, name)
        raise RuntimeError(f"upsert_stakeholder returned no row for stakeholder {name!r}")
    return response.data[0]


def _count_story_mentions(user_id: str, story_id: str, column: str, value: str) -> int:
    """Count career_updates rows that link `story_id` to `value` in the array column `column`.

    This reuses career_updates as a lightweight provenance record (conversation -> story ->
    contributed skills/stakeholders) instead of a dedicated join table - it already captures
    exactly this shape on every message, story-linked or not. It only answers "how many messages
    linked this entity to this story," not which specific attribute values came from which
    message - see the comment in apply_stakeholder_correction for what that limits.
    """
    client = get_client()
    response = (
        client.table("career_updates")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .eq("extracted_story", story_id)
        .contains(column, [value])
        .execute()
    )
    return response.count or 0


def _swap_in_story_array(story_id: str, column: str, is_old_value, new_value: str) -> None:
    """Remove entries matching `is_old_value` and ensure `new_value` is present, in one
    power_stories row's array `column`. A correction is a swap, not a deletion - the array must
    end up naming the corrected-to entity, not just missing the corrected-away one.
    """
    client = get_client()
    resp = client.table("power_stories").select(f"id, {column}").eq("id", story_id).limit(1).execute()
    if not resp.data:
        return
    current = resp.data[0].get(column) or []
    updated = [item for item in current if not is_old_value(item)]
    if new_value not in updated:
        updated.append(new_value)
    if updated != current:
        client.table("power_stories").update({column: updated}).eq("id", story_id).execute()


def _rewrite_story_prose(story_id: str, incorrect_name: str, correct_name: str) -> None:
    """Replace whole-word mentions of `incorrect_name` with `correct_name` in a story's prose.

    The structured swap (stakeholder record, story arrays) doesn't guarantee the story's own
    situation/action/outcome/evidence text gets updated too - the model may correctly treat an
    entity-level stakeholder correction as not requiring it to re-emit a fresh story object this
    turn. Doing the text substitution here in Python makes it deterministic rather than relying
    on the model to rewrite the sentence every time a correction happens.
    """
    client = get_client()
    resp = (
        client.table("power_stories")
        .select("id, " + ", ".join(_STORY_ELEMENTS))
        .eq("id", story_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return
    story = resp.data[0]
    pattern = re.compile(rf"\b{re.escape(incorrect_name)}\b", re.IGNORECASE)
    updates: dict[str, str] = {}
    for element in _STORY_ELEMENTS:
        text = story.get(element)
        if text and pattern.search(text):
            updates[element] = pattern.sub(correct_name, text)
    if updates:
        client.table("power_stories").update(updates).eq("id", story_id).execute()


def apply_stakeholder_correction(
    user_id: str,
    incorrect_name: str,
    correct_name: str,
    story_id: str | None,
    stakeholder_id: str | None = None,
) -> None:
    """Swap a misattributed stakeholder for the correct one: ensure the correct stakeholder
    exists (creating it from the incorrect one's info if it doesn't), transfer info the correct
    one is missing, remove the mentions this story contributed to the incorrect one (deleting it
    if nothing else references it), and update the corrected story's stakeholder UUIDs and prose
    to name the right person.

    `stakeholder_id`, when given, is the already-resolved target from the extraction's
    correction_target (certain/likely confidence) - prefer it over the name lookup, since the
    model has already disambiguated between same/similar-named stakeholders.
    """
    client = get_client()
    now = datetime.now(UTC).isoformat()

    if stakeholder_id:
        incorrect_resp = client.table("stakeholders").select("*").eq("id", stakeholder_id).limit(1).execute()
    else:
        incorrect_resp = (
            client.table("stakeholders")
            .select("*")
            .eq("user_id", user_id)
            .ilike("name", incorrect_name)
            .limit(1)
            .execute()
        )
    if not incorrect_resp.data:
        logger.warning(
            "Correction referenced unknown stakeholder %r for user_id=%s; nothing to undo", incorrect_name, user_id
        )
        return
    incorrect = incorrect_resp.data[0]

    correct_resp = (
        client.table("stakeholders")
        .select("id, role, relationship, sentiment")
        .eq("user_id", user_id)
        .ilike("name", correct_name)
        .limit(1)
        .execute()
    )
    if correct_resp.data:
        correct = correct_resp.data[0]
        correct_id = correct["id"]
        enrichment: dict[str, Any] = {}
        if correct.get("role") is None and incorrect.get("role") is not None:
            enrichment["role"] = incorrect["role"]
        if correct.get("relationship") == "unknown" and incorrect.get("relationship") not in (None, "unknown"):
            enrichment["relationship"] = incorrect["relationship"]
        if correct.get("sentiment") in (None, "unknown") and incorrect.get("sentiment") not in (None, "unknown"):
            enrichment["sentiment"] = incorrect["sentiment"]
        if enrichment:
            client.table("stakeholders").update(enrichment).eq("id", correct_id).execute()
    else:
        # A correction is a swap, not a deletion - the corrected-to stakeholder must exist by the
        # end even if this message never separately extracted them as their own entry. Seed the
        # new row entirely from what the incorrect record knew.
        created_resp = (
            client.table("stakeholders")
            .insert(
                {
                    "user_id": user_id,
                    "name": correct_name,
                    "role": incorrect.get("role"),
                    "relationship": incorrect.get("relationship") or "unknown",
                    "sentiment": incorrect.get("sentiment") or "unknown",
                    "mention_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
            )
            .execute()
        )
        if not created_resp.data:
            logger.error("Failed to create corrected stakeholder %r for user_id=%s", correct_name, user_id)
            raise RuntimeError(f"Failed to create corrected stakeholder {correct_name!r}")
        correct_id = created_resp.data[0]["id"]

    mentions_from_this_story = (
        _count_story_mentions(user_id, story_id, "extracted_stakeholders", incorrect["id"]) if story_id else 1
    )
    remaining_mentions = incorrect.get("mention_count", 1) - mentions_from_this_story
    if remaining_mentions <= 0:
        client.table("stakeholders").delete().eq("id", incorrect["id"]).execute()
    else:
        # The stakeholder is still genuinely referenced by other stories/conversations, so the
        # row survives with a lower mention_count. Deliberate limitation: career_updates tells us
        # THAT a message linked this stakeholder to a story, not WHICH attribute values that
        # message set - so we can't selectively un-apply just the role/relationship/sentiment
        # that came from *this* corrected story. The row may keep an imprecise attribute from it.
        # That's an accepted, self-correcting gap (a future real mention re-enriches it via
        # upsert_stakeholder's never-downgrade rule) rather than something worth solving today.
        # TODO: Week 2 Gmail sync needs per-interaction records anyway (last contacted,
        # interaction frequency, "worth a catch-up" scoring) - that's the natural moment to add
        # field-level provenance if this gap still matters by then.
        client.table("stakeholders").update({"mention_count": remaining_mentions}).eq("id", incorrect["id"]).execute()

    if story_id:
        _swap_in_story_array(story_id, "stakeholders", lambda sid: sid == incorrect["id"], correct_id)
        _rewrite_story_prose(story_id, incorrect_name, correct_name)


def apply_skill_correction(user_id: str, incorrect_name: str, correct_name: str, story_id: str | None) -> None:
    """Swap a misattributed skill for the correct one: ensure the correct skill exists (creating
    it if it doesn't), remove the mentions this story contributed to the incorrect one (deleting
    it if nothing else references it), and swap the corrected story's skills_used entry.
    """
    client = get_client()
    now = datetime.now(UTC).isoformat()

    existing_resp = (
        client.table("skills")
        .select("id, mention_count, category")
        .eq("user_id", user_id)
        .ilike("name", incorrect_name)
        .limit(1)
        .execute()
    )
    if not existing_resp.data:
        logger.warning(
            "Correction referenced unknown skill %r for user_id=%s; nothing to undo", incorrect_name, user_id
        )
        return
    existing = existing_resp.data[0]

    correct_resp = (
        client.table("skills")
        .select("id")
        .eq("user_id", user_id)
        .ilike("name", correct_name)
        .limit(1)
        .execute()
    )
    if not correct_resp.data:
        # A correction is a swap, not a deletion - the corrected-to skill must exist by the end
        # even if this message never separately extracted it as its own entry.
        created_resp = (
            client.table("skills")
            .insert(
                {
                    "user_id": user_id,
                    "name": correct_name,
                    "category": existing.get("category"),
                    "mention_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
            )
            .execute()
        )
        if not created_resp.data:
            logger.error("Failed to create corrected skill %r for user_id=%s", correct_name, user_id)
            raise RuntimeError(f"Failed to create corrected skill {correct_name!r}")

    mentions_from_this_story = (
        _count_story_mentions(user_id, story_id, "extracted_skills", incorrect_name) if story_id else 1
    )
    remaining_mentions = existing.get("mention_count", 1) - mentions_from_this_story
    if remaining_mentions <= 0:
        client.table("skills").delete().eq("id", existing["id"]).execute()
    else:
        client.table("skills").update({"mention_count": remaining_mentions}).eq("id", existing["id"]).execute()

    if story_id:
        _swap_in_story_array(
            story_id, "skills_used", lambda skill_name: skill_name.lower() == incorrect_name.lower(), correct_name
        )


def get_pending_correction(user_id: str) -> dict[str, Any] | None:
    """Return the user's pending correction if one exists and hasn't passed its 24h TTL, else None.

    A pending correction is single-slot conversational state (at most one open disambiguation
    question per user, since Sumire won't stack a second one on top of an unresolved first) - a
    JSONB column on user_profiles rather than its own table, avoiding a migration and CRUD
    surface for what is ephemeral, short-lived state with no relations of its own.
    """
    client = get_client()
    response = client.table("user_profiles").select("pending_correction").eq("id", user_id).limit(1).execute()
    if not response.data:
        return None
    pending = response.data[0].get("pending_correction")
    if not pending:
        return None
    created_at = datetime.fromisoformat(pending["created_at"])
    if datetime.now(UTC) - created_at > PENDING_CORRECTION_TTL:
        clear_pending_correction(user_id)
        return None
    return pending


def set_pending_correction(user_id: str, pending: dict[str, Any]) -> None:
    """Store a pending correction, overwriting any previous one (single-slot by design)."""
    get_client().table("user_profiles").update({"pending_correction": pending}).eq("id", user_id).execute()


def clear_pending_correction(user_id: str) -> None:
    """Clear the user's pending correction, if any."""
    get_client().table("user_profiles").update({"pending_correction": None}).eq("id", user_id).execute()


_STORY_ELEMENTS = ("situation", "action", "outcome", "evidence")
_STORY_STATUS_RANK = {"missing": 0, "partial": 1, "strong": 2}


def _normalize_story_content(story: dict[str, Any]) -> dict[str, Any]:
    """Null out an element's text when its own status is missing.

    The extraction tool schema requires each element as a non-null string, so when there's
    nothing to say the model fills the field with the status word itself (e.g. evidence="missing").
    That placeholder must never reach the content column - only *_status should ever hold it.
    """
    normalized = dict(story)
    for element in _STORY_ELEMENTS:
        if normalized.get(f"{element}_status") == "missing":
            normalized[element] = None
    return normalized


def create_power_story(
    user_id: str,
    story: dict[str, Any],
    source_conversation_id: str,
    skill_names: list[str],
    stakeholder_ids: list[str],
) -> dict:
    """Insert a brand-new power story, its gaps, its linked skills/stakeholders, and its completeness score."""
    client = get_client()
    story = _normalize_story_content(story)
    payload = {
        "user_id": user_id,
        "source": "whatsapp",
        "source_id": source_conversation_id,
        "title": story.get("title"),
        **{f"{element}": story.get(element) for element in _STORY_ELEMENTS},
        **{f"{element}_status": story.get(f"{element}_status", "missing") for element in _STORY_ELEMENTS},
        "gaps": story.get("gaps", []),
        "skills_used": skill_names,
        "stakeholders": stakeholder_ids,
        "completeness": compute_completeness(story),
        "status": "building",
    }
    response = client.table("power_stories").insert(payload).execute()
    return response.data[0]


def update_power_story(
    story_id: str,
    story: dict[str, Any],
    skill_names: list[str],
    stakeholder_ids: list[str],
) -> dict:
    """Merge new story content into an existing one, never downgrading a stronger element."""
    client = get_client()
    story = _normalize_story_content(story)
    existing_resp = client.table("power_stories").select("*").eq("id", story_id).limit(1).execute()
    existing = existing_resp.data[0] if existing_resp.data else {}

    merged: dict[str, Any] = {"title": story.get("title") or existing.get("title")}
    for element in _STORY_ELEMENTS:
        status_key = f"{element}_status"
        existing_status = existing.get(status_key, "missing")
        new_status = story.get(status_key, "missing")
        if _STORY_STATUS_RANK.get(new_status, 0) < _STORY_STATUS_RANK.get(existing_status, 0):
            merged[element] = existing.get(element)
            merged[status_key] = existing_status
        else:
            merged[element] = story.get(element)
            merged[status_key] = new_status

    # Gaps come from the model's read of the new message alone, so an element it flagged as
    # partial/missing may already be strong in the merged (never-downgraded) result - drop those.
    merged_status_by_element = {element: merged[f"{element}_status"] for element in _STORY_ELEMENTS}
    merged["gaps"] = [
        gap for gap in story.get("gaps", []) if merged_status_by_element.get(gap.get("element"), "partial") != "strong"
    ]

    existing_skills = existing.get("skills_used") or []
    merged["skills_used"] = list(dict.fromkeys([*existing_skills, *skill_names]))
    existing_stakeholders = existing.get("stakeholders") or []
    merged["stakeholders"] = list(dict.fromkeys([*existing_stakeholders, *stakeholder_ids]))

    merged["completeness"] = compute_completeness(merged)
    merged["updated_at"] = datetime.now(UTC).isoformat()

    response = client.table("power_stories").update(merged).eq("id", story_id).execute()
    return response.data[0]


def get_inprogress_stories(user_id: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` in-progress power stories for a user, most recently updated first."""
    client = get_client()
    response = (
        client.table("power_stories")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "building")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data


def create_career_update(
    user_id: str,
    conversation_id: str,
    summary: str,
    extracted_skills: list[str],
    extracted_story: str | None,
    extracted_stakeholders: list[str],
) -> dict:
    """Insert a career_updates row linking a conversation to what was extracted from it."""
    client = get_client()
    payload = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "summary": summary,
        "extracted_skills": extracted_skills,
        "extracted_story": extracted_story,
        "extracted_stakeholders": extracted_stakeholders,
    }
    response = client.table("career_updates").insert(payload).execute()
    return response.data[0]


# Columns this module actually reads or writes, per table. Checked against the live
# database at startup (see check_schema) so drift from schema.sql is caught at boot,
# not the first time a webhook hits a column that's missing or renamed.
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "user_profiles": {"id", "phone", "name", "job_role", "company", "career_stage", "pending_correction"},
    "goals": {"id", "user_id", "title", "status"},
    "conversations": {
        "id", "user_id", "direction", "raw_content", "media_type",
        "classification", "whatsapp_message_id", "created_at",
    },
    "skills": {"id", "user_id", "name", "category", "mention_count", "first_seen", "last_seen"},
    "stakeholders": {
        "id", "user_id", "name", "role", "relationship", "sentiment",
        "mention_count", "first_seen", "last_seen",
    },
    "power_stories": {
        "id", "user_id", "title", "situation", "situation_status", "action", "action_status",
        "outcome", "outcome_status", "evidence", "evidence_status", "completeness", "status",
        "source", "source_id", "gaps", "skills_used", "stakeholders", "updated_at",
    },
    "career_updates": {
        "id", "user_id", "conversation_id", "summary",
        "extracted_skills", "extracted_story", "extracted_stakeholders",
    },
}


def check_schema() -> list[str]:
    """Return 'table.column' entries EXPECTED_COLUMNS names that the live database is missing.

    Queries each table directly instead of information_schema - Supabase's PostgREST doesn't
    expose that schema by default (this previously failed on every boot with PGRST106, "schema
    must be one of the following: ...", turning the check into pure log noise). A `limit(1)`
    select of the expected columns is cheap, needs no database objects of its own, and so has
    nothing that can drift out of sync with schema.sql the way a hand-maintained SQL function
    would when schema.sql gets regenerated from a fresh information_schema dump.
    """
    client = get_client()
    missing: list[str] = []

    for table, expected_columns in EXPECTED_COLUMNS.items():
        try:
            client.table(table).select("id").limit(1).execute()
        except Exception:
            missing.append(f"{table} (table not found or unreachable)")
            continue

        columns = sorted(expected_columns)
        try:
            client.table(table).select(", ".join(columns)).limit(1).execute()
        except Exception:
            # At least one column in this table is missing - probe individually to name
            # exactly which ones, rather than reporting the whole table as broken.
            for column in columns:
                try:
                    client.table(table).select(column).limit(1).execute()
                except Exception:
                    missing.append(f"{table}.{column}")

    return missing
