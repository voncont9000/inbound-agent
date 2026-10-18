import asyncio
import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from inbound_api.db import session_factory, tenant_session
from inbound_api.jobs import enqueue
from inbound_api.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Message,
    MessageRole,
)
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
LOGGER = logging.getLogger(__name__)
WHATSAPP_WINDOW = timedelta(hours=24)
_PLACEHOLDERS = {
    "audio": "[audio]",
    "document": "[document]",
    "image": "[image]",
    "location": "[location]",
    "sticker": "[sticker]",
    "video": "[video]",
}


@router.get("/whatsapp")
async def verify_whatsapp_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    expected = get_settings().whatsapp_verify_token or ""
    if mode != "subscribe" or not expected or not hmac.compare_digest(verify_token or "", expected):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return Response(content=challenge or "", media_type="text/plain")


@router.post("/whatsapp")
async def receive_whatsapp_webhook(request: Request) -> dict[str, bool]:
    received_at = datetime.now(UTC)
    settings = get_settings()
    if not settings.whatsapp_app_secret:
        raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = (
        "sha256="
        + hmac.new(
            settings.whatsapp_app_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp signature")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"ok": True}
    if not isinstance(payload, dict):
        return {"ok": True}
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return {"ok": True}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                continue
            phone_number_id = metadata.get("phone_number_id")
            if not isinstance(phone_number_id, str) or not phone_number_id:
                continue
            contacts = {}
            raw_contacts = value.get("contacts")
            if isinstance(raw_contacts, list):
                for item in raw_contacts:
                    if not isinstance(item, dict):
                        continue
                    wa_id = item.get("wa_id")
                    profile = item.get("profile")
                    if isinstance(wa_id, str) and isinstance(profile, dict):
                        name = profile.get("name")
                        contacts[wa_id] = name if isinstance(name, str) else None
            messages = value.get("messages")
            if not isinstance(messages, list) or not messages:
                continue
            async with session_factory() as session:
                route = (
                    await session.execute(
                        text("SELECT * FROM whatsapp_route(:phone_number_id)"),
                        {"phone_number_id": phone_number_id},
                    )
                ).first()
            if route is None:
                LOGGER.warning(
                    "Ignoring WhatsApp event for unknown phone number ID %s",
                    phone_number_id,
                )
                continue
            tenant_id, assistant_id, connection_id = route
            for message in messages:
                if not isinstance(message, dict):
                    continue
                sender_id = message.get("from")
                await _store_inbound_message(
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    connection_id=connection_id,
                    phone_number_id=phone_number_id,
                    message=message,
                    contact_name=contacts.get(sender_id) if isinstance(sender_id, str) else None,
                    received_at=received_at,
                )
    return {"ok": True}


@router.post("/gmail")
async def receive_gmail_webhook(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    received_at = datetime.now(UTC).isoformat(timespec="microseconds")
    settings = get_settings()
    if settings.gmail_mode == "off":
        raise HTTPException(status_code=503, detail="Gmail is not enabled")
    if settings.gmail_mode == "fake":
        expected = settings.gmail_push_token or ""
        if not expected:
            raise HTTPException(status_code=503, detail="Gmail push webhook is not configured")
        if not hmac.compare_digest(token or "", expected):
            raise HTTPException(status_code=401, detail="Invalid Gmail push token")
    else:
        bearer = (authorization or "").removeprefix("Bearer ").strip()
        if not bearer or not await _verify_gmail_pubsub_token(bearer):
            raise HTTPException(status_code=401, detail="Invalid Pub/Sub identity token")
    try:
        envelope = await request.json()
        data = envelope.get("message", {}).get("data") if isinstance(envelope, dict) else None
        decoded = json.loads(base64.b64decode(data, validate=True)) if isinstance(data, str) else {}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"ok": True}
    email = decoded.get("emailAddress") if isinstance(decoded, dict) else None
    if not isinstance(email, str) or not email.strip():
        return {"ok": True}
    async with session_factory() as session:
        route = (
            await session.execute(
                text("SELECT * FROM gmail_route(:email)"),
                {"email": email.strip().lower()},
            )
        ).first()
    if route is None:
        LOGGER.warning("Ignoring Gmail push for unknown mailbox %s", email)
        return {"ok": True}
    tenant_id, _assistant_id, connection_id = route
    async with tenant_session(tenant_id) as session:
        await enqueue_gmail_sync(session, connection_id, received_at=received_at)
    return {"ok": True}


async def _verify_gmail_pubsub_token(token: str) -> bool:
    settings = get_settings()
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token

        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            GoogleRequest(),
            settings.gmail_pubsub_audience,
        )
    except Exception:
        LOGGER.warning("Gmail Pub/Sub token verification failed", exc_info=True)
        return False
    return (
        claims.get("email") == settings.gmail_pubsub_service_account
        and claims.get("email_verified") is True
        and claims.get("aud") == settings.gmail_pubsub_audience
    )


async def _store_inbound_message(
    *,
    tenant_id,
    assistant_id,
    connection_id,
    phone_number_id: str,
    message: dict,
    contact_name: str | None,
    received_at: datetime | None = None,
) -> None:
    wamid = message.get("id")
    wa_id = message.get("from")
    if not isinstance(wamid, str) or not wamid or not isinstance(wa_id, str) or not wa_id:
        return
    provider_timestamp = _message_timestamp(message.get("timestamp"))
    message_received_at = (received_at or provider_timestamp).astimezone(UTC)
    message_type = str(message.get("type") or "unknown")
    text_content = message.get("text")
    body = (
        text_content.get("body")
        if message_type == "text" and isinstance(text_content, dict)
        else None
    )
    if not isinstance(body, str):
        body = _PLACEHOLDERS.get(message_type, f"[{message_type}]")
    try:
        phone = normalise_phone(wa_id, strict=False)
    except ValueError:
        phone = None
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"{tenant_id}:{wa_id}"},
        )
        if await session.scalar(select(Message.id).where(Message.external_id == wamid).limit(1)):
            return
        conversations = await session.scalars(
            select(Conversation)
            .where(
                Conversation.channel == ConversationChannel.WHATSAPP,
                Conversation.status == ConversationStatus.ACTIVE,
                Conversation.external_ref.like(f"wa:{phone_number_id}:{wa_id}:%"),
            )
            .order_by(Conversation.started_at.desc(), Conversation.id.desc())
            .limit(1)
            .with_for_update()
        )
        conversation = conversations.first()
        if conversation is not None:
            last_message_at = await session.scalar(
                select(Message.ts)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.ts.desc(), Message.id.desc())
                .limit(1)
            )
            if (
                message_received_at - (last_message_at or conversation.started_at)
                >= WHATSAPP_WINDOW
            ):
                conversation.status = ConversationStatus.COMPLETED
                conversation.ended_at = message_received_at
                conversation = None
        if conversation is None:
            conversation = Conversation(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.WHATSAPP,
                external_ref=f"wa:{phone_number_id}:{wa_id}:{wamid}",
                status=ConversationStatus.ACTIVE,
                started_at=message_received_at,
                metrics={
                    "from_number": phone,
                    "whatsapp_wa_id": wa_id,
                    "whatsapp_phone_number_id": phone_number_id,
                    "channel_connection_id": str(connection_id),
                    "contact_name": contact_name,
                },
            )
            session.add(conversation)
            await session.flush()
        else:
            conversation.metrics = {
                **(conversation.metrics or {}),
                "from_number": phone,
                "whatsapp_wa_id": wa_id,
                "whatsapp_phone_number_id": phone_number_id,
                "channel_connection_id": str(connection_id),
                "contact_name": contact_name or (conversation.metrics or {}).get("contact_name"),
            }
        stored_message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.CALLER,
            text=body,
            ts=message_received_at,
            external_id=wamid,
            meta={
                "type": message_type,
                "wa_id": wa_id,
                "contact_name": contact_name,
                "provider_timestamp": provider_timestamp.isoformat(timespec="microseconds"),
                "received_at": message_received_at.isoformat(timespec="microseconds"),
            },
        )
        try:
            async with session.begin_nested():
                session.add(stored_message)
                await session.flush()
        except IntegrityError:
            return
        await enqueue(
            session,
            "message_reply",
            {"conversation_id": str(conversation.id), "message_id": str(stored_message.id)},
        )


def _message_timestamp(value) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return datetime.now(UTC)
