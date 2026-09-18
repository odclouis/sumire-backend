"""Client for the WhatsApp Cloud API: parsing webhook payloads, downloading media, and sending messages."""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.models import IncomingMessage

logger = logging.getLogger("sumire.whatsapp")

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def parse_webhook_payload(body: dict[str, Any]) -> list[IncomingMessage]:
    """Extract all supported incoming messages from a Meta webhook payload, skipping status updates."""
    messages: list[IncomingMessage] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for raw_message in change.get("value", {}).get("messages", []):
                parsed = _parse_message(raw_message)
                if parsed is not None:
                    messages.append(parsed)
    return messages


def _parse_message(raw_message: dict[str, Any]) -> IncomingMessage | None:
    """Convert a single raw Meta message object into an IncomingMessage, or None if unsupported."""
    message_type = raw_message.get("type")
    message_id = raw_message["id"]
    common = {
        "phone": raw_message["from"],
        "message_id": message_id,
        "timestamp": datetime.fromtimestamp(int(raw_message["timestamp"]), tz=UTC),
        "type": message_type,
    }

    if message_type == "text":
        return IncomingMessage(**common, text=raw_message["text"]["body"])
    if message_type == "audio":
        return IncomingMessage(**common, media_id=raw_message["audio"]["id"])

    logger.info("Ignoring unsupported message type %s for message %s", message_type, message_id)
    return None


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Download WhatsApp media by id, returning its raw bytes and MIME type."""
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    async with httpx.AsyncClient() as client:
        lookup_response = await client.get(f"{GRAPH_API_BASE}/{media_id}", headers=headers)
        lookup_response.raise_for_status()
        media_url = lookup_response.json()["url"]

        media_response = await client.get(media_url, headers=headers)
        media_response.raise_for_status()

    mime_type = media_response.headers.get("content-type", "application/octet-stream")
    logger.info("Downloaded media %s (%d bytes, %s)", media_id, len(media_response.content), mime_type)
    return media_response.content, mime_type


async def send_message(to_phone: str, text: str) -> dict[str, Any]:
    """Send a text message to a WhatsApp user via the Cloud API, raising on a non-2xx response."""
    settings = get_settings()
    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.is_success:
        logger.info("Sent WhatsApp message to %s: %s", to_phone, response.json())
    else:
        logger.error("Failed to send WhatsApp message to %s: %s", to_phone, response.text)
    response.raise_for_status()
    return response.json()
