"""The two Claude calls in the pipeline: structured extraction and free-text response."""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, field_validator

from app.config import get_settings
from app.services import prompts
from app.services.context import UserContext

logger = logging.getLogger("sumire.llm")

MODEL = "claude-sonnet-5"

StatusLiteral = Literal["strong", "partial", "missing"]

_DASH_PATTERN = re.compile(r"\s*[—–]\s*")


def _strip_dashes(text: str) -> str:
    """Replace any em/en dash with a comma or full stop; belt and braces on top of the prompt rule."""

    def _replacement(match: re.Match[str]) -> str:
        next_char = text[match.end() : match.end() + 1]
        return ". " if next_char.isupper() else ", "

    return _DASH_PATTERN.sub(_replacement, text).rstrip(", ")


class ExtractedSkill(BaseModel):
    """A skill evidenced by what the user described."""

    name: str
    category: Literal["technical", "leadership", "communication", "strategic", "operational"]


class ExtractedStakeholder(BaseModel):
    """A named individual mentioned in the message."""

    name: str
    role: str | None
    relationship: Literal[
        "manager", "skip_level", "peer", "report", "mentor", "sponsor", "external", "unknown"
    ]
    sentiment: Literal["positive", "neutral", "negative", "unknown"]


class StoryGap(BaseModel):
    """A missing or partial story element and how to unlock it."""

    element: str
    gap_description: str
    follow_up_question: str

    @field_validator("follow_up_question")
    @classmethod
    def _strip_dashes_from_question(cls, value: str) -> str:
        """These questions surface directly in the app, so they get the same dash filter as WhatsApp replies."""
        return _strip_dashes(value)


class Correction(BaseModel):
    """A previously-recorded entity the user is explicitly retracting or replacing with another."""

    entity_type: Literal["stakeholder", "skill", "story_element"]
    incorrect_value: str
    correct_value: str


ConfidenceLiteral = Literal["certain", "likely", "ambiguous"]


class CorrectionTarget(BaseModel):
    """Which specific in-progress story and known stakeholder a correction is about, with confidence."""

    story_id: str | None
    story_confidence: ConfidenceLiteral
    candidate_story_ids: list[str]
    stakeholder_id: str | None
    stakeholder_confidence: ConfidenceLiteral
    candidate_stakeholder_ids: list[str]


class ExtractedStory(BaseModel):
    """A power story in Situation/Action/Outcome/Evidence form."""

    title: str
    situation: str
    situation_status: StatusLiteral
    action: str
    action_status: StatusLiteral
    outcome: str
    outcome_status: StatusLiteral
    evidence: str
    evidence_status: StatusLiteral
    gaps: list[StoryGap]


class Extraction(BaseModel):
    """The full structured output of the extraction call."""

    classification: Literal[
        "work_update", "question", "correction", "context_reply",
        "acknowledgement", "reflection_reply", "unknown",
    ]
    signal_quality: Literal["rich", "simple"]
    skills: list[ExtractedSkill]
    stakeholders: list[ExtractedStakeholder]
    corrections: list[Correction]
    correction_target: CorrectionTarget | None
    story_action: Literal["new", "update_existing", "none"]
    existing_story_id: str | None
    story: ExtractedStory | None
    summary: str

    @field_validator("story", "correction_target", mode="before")
    @classmethod
    def _parse_nested_object_if_stringified(cls, value: object) -> object:
        """Defensively parse a nullable nested object if the model returns it JSON-encoded as a string."""
        if isinstance(value, str):
            logger.warning("Extraction field arrived as a JSON string instead of a tool-call object; parsing it defensively")
            return json.loads(value)
        return value


_STATUS_ENUM = {"type": "string", "enum": ["strong", "partial", "missing"]}
_CONFIDENCE_ENUM = {"type": "string", "enum": ["certain", "likely", "ambiguous"]}

_EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_extraction",
    "description": "Record the structured extraction for one incoming WhatsApp message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classification": {
                "type": "string",
                "enum": [
                    "work_update", "question", "correction", "context_reply",
                    "acknowledgement", "reflection_reply", "unknown",
                ],
            },
            "signal_quality": {"type": "string", "enum": ["rich", "simple"]},
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["technical", "leadership", "communication", "strategic", "operational"],
                        },
                    },
                    "required": ["name", "category"],
                    "additionalProperties": False,
                },
            },
            "stakeholders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "relationship": {
                            "type": "string",
                            "enum": [
                                "manager", "skip_level", "peer", "report",
                                "mentor", "sponsor", "external", "unknown",
                            ],
                        },
                        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative", "unknown"]},
                    },
                    "required": ["name", "role", "relationship", "sentiment"],
                    "additionalProperties": False,
                },
            },
            "corrections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_type": {"type": "string", "enum": ["stakeholder", "skill", "story_element"]},
                        "incorrect_value": {"type": "string"},
                        "correct_value": {"type": "string"},
                    },
                    "required": ["entity_type", "incorrect_value", "correct_value"],
                    "additionalProperties": False,
                },
            },
            "correction_target": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "story_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "story_confidence": _CONFIDENCE_ENUM,
                            "candidate_story_ids": {"type": "array", "items": {"type": "string"}},
                            "stakeholder_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "stakeholder_confidence": _CONFIDENCE_ENUM,
                            "candidate_stakeholder_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": [
                            "story_id", "story_confidence", "candidate_story_ids",
                            "stakeholder_id", "stakeholder_confidence", "candidate_stakeholder_ids",
                        ],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ],
            },
            "story_action": {"type": "string", "enum": ["new", "update_existing", "none"]},
            "existing_story_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "story": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "situation": {"type": "string"},
                            "situation_status": _STATUS_ENUM,
                            "action": {"type": "string"},
                            "action_status": _STATUS_ENUM,
                            "outcome": {"type": "string"},
                            "outcome_status": _STATUS_ENUM,
                            "evidence": {"type": "string"},
                            "evidence_status": _STATUS_ENUM,
                            "gaps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "element": {"type": "string"},
                                        "gap_description": {"type": "string"},
                                        "follow_up_question": {"type": "string"},
                                    },
                                    "required": ["element", "gap_description", "follow_up_question"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "title", "situation", "situation_status", "action", "action_status",
                            "outcome", "outcome_status", "evidence", "evidence_status", "gaps",
                        ],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ],
            },
            "summary": {"type": "string"},
        },
        "required": [
            "classification", "signal_quality", "skills", "stakeholders", "corrections",
            "correction_target", "story_action", "existing_story_id", "story", "summary",
        ],
        "additionalProperties": False,
    },
}

_BRANCH_INSTRUCTIONS_BY_CLASSIFICATION: dict[str, str] = {
    "question": prompts.QUESTION_INSTRUCTION,
    "correction": prompts.CORRECTION_INSTRUCTION,
    "context_reply": prompts.CONTEXT_REPLY_INSTRUCTION,
    "acknowledgement": prompts.ACKNOWLEDGEMENT_INSTRUCTION,
    "reflection_reply": prompts.REFLECTION_REPLY_INSTRUCTION,
    "unknown": prompts.UNKNOWN_INSTRUCTION,
}


@lru_cache
def _get_client() -> anthropic.Anthropic:
    """Create (and cache) the Anthropic client."""
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def _select_branch_instruction(classification: str, signal_quality: str) -> str:
    """Map classification (and signal quality, for work_update) to its response instruction."""
    if classification == "work_update":
        return prompts.WORK_UPDATE_RICH_INSTRUCTION if signal_quality == "rich" else prompts.WORK_UPDATE_SIMPLE_INSTRUCTION
    return _BRANCH_INSTRUCTIONS_BY_CLASSIFICATION.get(classification, prompts.UNKNOWN_INSTRUCTION)


def is_ambiguous_correction(extraction: Extraction) -> bool:
    """Whether this correction can't be safely applied because its story or stakeholder target is ambiguous.

    Only checks the confidence dimensions this message's corrections actually rely on: story
    resolution matters for stakeholder/skill corrections (their arrays live on power_stories),
    stakeholder resolution only for a stakeholder correction specifically.
    """
    target = extraction.correction_target
    if extraction.classification != "correction" or target is None or not extraction.corrections:
        return False
    needs_story = any(c.entity_type in ("stakeholder", "skill") for c in extraction.corrections)
    needs_stakeholder = any(c.entity_type == "stakeholder" for c in extraction.corrections)
    return (needs_story and target.story_confidence == "ambiguous") or (
        needs_stakeholder and target.stakeholder_confidence == "ambiguous"
    )


def extract_from_message(message: str, context: UserContext) -> Extraction:
    """Run the extraction call: classify the message and pull skills, stakeholders, and story signal."""
    user_message = (
        f"{context.to_prompt_block()}\n\n"
        f"NEW MESSAGE FROM USER:\n{message}\n\n"
        "Call record_extraction with your analysis of this message."
    )

    # No `temperature` here: anthropic 1.6.0's messages.create() doesn't accept it for this
    # model (see MODEL). Determinism is enforced through the prompt instead — see
    # prompts.EXTRACTION_SYSTEM_PROMPT's "Be literal and consistent" section.
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=2500,
        system=prompts.EXTRACTION_SYSTEM_PROMPT,
        tools=[_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_extraction"},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    return Extraction.model_validate(tool_use.input)


def generate_response(message: str, extraction: Extraction, context: UserContext) -> str:
    """Run the response call: a short, direct WhatsApp reply shaped by the extraction."""
    ambiguous = is_ambiguous_correction(extraction)
    branch_instruction = (
        prompts.CORRECTION_AMBIGUOUS_INSTRUCTION
        if ambiguous
        else _select_branch_instruction(extraction.classification, extraction.signal_quality)
    )
    system = f"{prompts.SUMIRE_VOICE}\n\n{branch_instruction}"

    gap_line = ""
    if extraction.story and extraction.story.gaps:
        gap = extraction.story.gaps[0]
        gap_line = f"\nMOST USEFUL FOLLOW-UP QUESTION: {gap.follow_up_question}"

    ambiguity_line = ""
    if ambiguous and extraction.correction_target:
        target = extraction.correction_target
        ambiguity_line = (
            f"\nAMBIGUOUS CORRECTION CANDIDATES: story ids {target.candidate_story_ids}, "
            f"stakeholder ids {target.candidate_stakeholder_ids}. Match these ids against the "
            "context above to find their titles/names, then ask which one using those names - "
            "never state an id."
        )

    user_message = (
        f"{context.to_prompt_block()}\n\n"
        f"USER'S MESSAGE:\n{message}\n\n"
        f"CLASSIFICATION: {extraction.classification} ({extraction.signal_quality})\n"
        f"EXTRACTION SUMMARY: {extraction.summary}"
        f"{gap_line}"
        f"{ambiguity_line}"
    )

    # No `temperature` here either, same SDK/model limitation as extract_from_message.
    # Natural response variation is fine without it; no prompt compensation needed.
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    return _strip_dashes(next(block.text for block in response.content if block.type == "text").strip())
