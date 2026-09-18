"""Audio transcription via the Gemini API."""

import base64
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("sumire.transcription")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = "gemini-2.5-flash"

# TODO: replace with the exact transcription prompt from your Make.com scenario.
TRANSCRIPTION_PROMPT = (
    "Transcribe this audio message exactly as spoken, in its original language. "
    "Return only the plain transcript text, with no labels, timestamps, or commentary."
)


class TranscriptionError(Exception):
    """Raised when Gemini fails to transcribe an audio message."""


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """Transcribe audio bytes to a plain-text transcript using Gemini 2.5 Flash."""
    settings = get_settings()
    url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent"
    payload: dict[str, Any] = {
        "contents": [
            {
                "parts": [
                    {"text": TRANSCRIPTION_PROMPT},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(audio_bytes).decode(),
                        }
                    },
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, params={"key": settings.gemini_api_key}, json=payload)

    if not response.is_success:
        logger.error("Gemini transcription request failed: %s", response.text)
        raise TranscriptionError(
            f"Gemini transcription failed with status {response.status_code}: {response.text}"
        )

    data = response.json()
    try:
        transcript = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected Gemini response shape: %s", data)
        raise TranscriptionError("Gemini returned an unexpected response shape") from exc

    logger.info("Transcribed %d bytes of audio to a %d-character transcript", len(audio_bytes), len(transcript))
    return transcript
