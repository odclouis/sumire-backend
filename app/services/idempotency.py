"""Idempotency guard so retried WhatsApp webhook deliveries aren't processed twice."""

import logging

from app import db

logger = logging.getLogger("sumire.idempotency")


def already_processed(whatsapp_message_id: str) -> bool:
    """Return True if a conversation row already exists for this WhatsApp message id."""
    exists = db.get_conversation_by_whatsapp_message_id(whatsapp_message_id) is not None
    if exists:
        logger.info("Message %s already processed, skipping", whatsapp_message_id)
    return exists
