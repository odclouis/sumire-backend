"""Assembles the compact per-user context injected into both LLM calls."""

from dataclasses import dataclass, field
from typing import Any

from app.db import get_client, get_pending_correction


@dataclass
class UserContext:
    """Everything the extraction and response LLM calls need to know about a user."""

    name: str | None
    job_role: str | None
    company: str | None
    career_stage: str | None
    active_goals: list[dict[str, Any]] = field(default_factory=list)
    recent_conversations: list[dict[str, Any]] = field(default_factory=list)
    top_skills: list[dict[str, Any]] = field(default_factory=list)
    inprogress_stories: list[dict[str, Any]] = field(default_factory=list)
    top_stakeholders: list[dict[str, Any]] = field(default_factory=list)
    pending_correction: dict[str, Any] | None = None

    def to_prompt_block(self) -> str:
        """Render this context as compact, labelled plain text for prompt injection."""
        lines: list[str] = []

        lines.append("PROFILE:")
        lines.append(f"  name: {self.name or 'unknown'}")
        lines.append(f"  role: {self.job_role or 'unknown'} at {self.company or 'unknown'}")
        lines.append(f"  career stage: {self.career_stage or 'unknown'}")

        if self.active_goals:
            lines.append("ACTIVE GOALS:")
            for goal in self.active_goals:
                lines.append(f"  - {goal['title']}")

        lines.append("RECENT CONVERSATION (oldest first):")
        if self.recent_conversations:
            for conv in self.recent_conversations:
                speaker = "User" if conv["direction"] == "inbound" else "Sumire"
                lines.append(f"  {speaker}: {conv['raw_content']}")
        else:
            lines.append("  none yet")

        lines.append("KNOWN SKILLS (top by mentions):")
        if self.top_skills:
            for skill in self.top_skills:
                lines.append(f"  - {skill['name']} ({skill['category']}, x{skill['mention_count']})")
        else:
            lines.append("  none yet")

        lines.append("IN-PROGRESS STORIES:")
        if self.inprogress_stories:
            for story in self.inprogress_stories:
                lines.append(f"  [{story['id']}] {story['title']}")
                lines.append(f"    situation ({story['situation_status']}): {story['situation'] or 'missing'}")
                lines.append(f"    action ({story['action_status']}): {story['action'] or 'missing'}")
                lines.append(f"    outcome ({story['outcome_status']}): {story['outcome'] or 'missing'}")
                lines.append(f"    evidence ({story['evidence_status']}): {story['evidence'] or 'missing'}")
                gaps = story.get("gaps") or []
                if gaps:
                    for gap in gaps:
                        lines.append(
                            f"    gap ({gap['element']}): {gap['gap_description']} "
                            f"-> {gap['follow_up_question']}"
                        )
                else:
                    lines.append("    gaps: none listed")
        else:
            lines.append("  none yet")

        lines.append("KNOWN STAKEHOLDERS:")
        if self.top_stakeholders:
            for stakeholder in self.top_stakeholders:
                lines.append(
                    f"  - [{stakeholder['id']}] {stakeholder['name']} "
                    f"({stakeholder['role'] or 'role unknown'}, {stakeholder['relationship']})"
                )
        else:
            lines.append("  none yet")

        if self.pending_correction:
            pending = self.pending_correction
            corrections = pending.get("corrections", [])
            lines.append("PENDING CORRECTION (from a previous message, unresolved):")
            for correction in corrections:
                lines.append(
                    f"  - {correction.get('incorrect_value')} should be "
                    f"{correction.get('correct_value')} ({correction.get('entity_type')}), "
                    "but it wasn't clear which one was meant."
                )
            candidate_stories = pending.get("candidate_stories") or []
            if candidate_stories:
                lines.append("  Candidate stories:")
                for candidate in candidate_stories:
                    lines.append(f"    [{candidate['id']}] {candidate['title']}")
            candidate_stakeholders = pending.get("candidate_stakeholders") or []
            if candidate_stakeholders:
                lines.append("  Candidate stakeholders:")
                for candidate in candidate_stakeholders:
                    lines.append(f"    [{candidate['id']}] {candidate['name']}")

        return "\n".join(lines)


def get_user_context(user_id: str) -> UserContext:
    """Fetch a user's profile, goals, history, skills, stories, stakeholders, and any pending correction."""
    client = get_client()

    profile_resp = (
        client.table("user_profiles")
        .select("name, job_role, company, career_stage")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    profile = profile_resp.data[0] if profile_resp.data else {}

    goals_resp = (
        client.table("goals")
        .select("id, title")
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )

    conversations_resp = (
        client.table("conversations")
        .select("direction, raw_content")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )

    skills_resp = (
        client.table("skills")
        .select("name, category, mention_count")
        .eq("user_id", user_id)
        .order("mention_count", desc=True)
        .limit(15)
        .execute()
    )

    stories_resp = (
        client.table("power_stories")
        .select(
            "id, title, situation, situation_status, action, action_status, "
            "outcome, outcome_status, evidence, evidence_status, gaps"
        )
        .eq("user_id", user_id)
        .eq("status", "building")
        .order("updated_at", desc=True)
        .limit(5)
        .execute()
    )

    stakeholders_resp = (
        client.table("stakeholders")
        .select("id, name, role, relationship")
        .eq("user_id", user_id)
        .order("mention_count", desc=True)
        .limit(15)
        .execute()
    )

    return UserContext(
        name=profile.get("name"),
        job_role=profile.get("job_role"),
        company=profile.get("company"),
        career_stage=profile.get("career_stage"),
        active_goals=goals_resp.data,
        recent_conversations=list(reversed(conversations_resp.data)),
        top_skills=skills_resp.data,
        inprogress_stories=stories_resp.data,
        top_stakeholders=stakeholders_resp.data,
        pending_correction=get_pending_correction(user_id),
    )
