"""Pydantic models mirroring the career-mvp-schema.sql tables used by this service."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class UserProfile(BaseModel):
    """A row in the user_profiles table."""

    id: UUID
    phone: str
    name: str | None = None
    job_role: str | None = None
    company: str | None = None
    career_stage: str | None = None
    onboarding_done: bool = False
    onboarding_data: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class Conversation(BaseModel):
    """A row in the conversations table."""

    id: UUID
    user_id: UUID
    direction: str
    raw_content: str | None = None
    media_type: str | None = None
    classification: str | None = None
    created_at: datetime
