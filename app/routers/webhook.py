"""Webhook endpoints for verifying and receiving WhatsApp Cloud API events."""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.services.pipeline import process_incoming_message
from app.services.whatsapp import parse_webhook_payload

logger = logging.getLogger("sumire.webhook")

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.get("/whatsapp")
def verify_whatsapp_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> PlainTextResponse:
    """Handle Meta's webhook verification handshake."""
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(hub_challenge, status_code=200)
    logger.warning("WhatsApp webhook verification failed (mode=%s)", hub_mode)
    return PlainTextResponse("Forbidden", status_code=403)


def _verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header as an HMAC-SHA256 of the raw body."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@router.post("/whatsapp")
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Verify the request signature, queue message processing in the background, and return 200."""
    settings = get_settings()
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")

    if not _verify_signature(raw_body, signature_header, settings.whatsapp_app_secret):
        logger.warning("WhatsApp webhook signature verification failed")
        return Response(status_code=403)

    payload = json.loads(raw_body)
    messages = parse_webhook_payload(payload)
    for message in messages:
        logger.info("Queued inbound message %s from %s", message.message_id, message.phone)
        background_tasks.add_task(process_incoming_message, message)

    return Response(status_code=200)
