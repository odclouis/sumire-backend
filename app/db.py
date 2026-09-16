"""Supabase client and data-access helpers for the collection layer schema."""

import re
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


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
    user_id: str, direction: str, content: str, media_type: str = "text"
) -> dict:
    """Insert a new conversation row logging an inbound or outbound message and return it."""
    client = get_client()
    payload = {
        "user_id": user_id,
        "direction": direction,
        "raw_content": content,
        "media_type": media_type,
    }
    response = client.table("conversations").insert(payload).execute()
    return response.data[0]


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
