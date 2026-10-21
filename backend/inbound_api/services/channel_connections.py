from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext
from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.models import Assistant, ChannelConnection
from inbound_api.schemas import ChannelConnectionCreate, ChannelConnectionUpdate
from inbound_api.services.audit import write_audit
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings


async def create_channel_connection(
    session: AsyncSession,
    ctx: TenantContext,
    assistant_id: UUID,
    payload: ChannelConnectionCreate,
) -> ChannelConnection:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    if payload.kind == "gmail" and get_settings().gmail_mode == "off":
        raise HTTPException(status_code=409, detail="Gmail is not enabled")
    if payload.kind == "gmail" and get_settings().gmail_mode == "google":
        raise HTTPException(status_code=422, detail="Connect Gmail with Google")
    external_id = payload.external_id.strip()
    if payload.kind == "phone":
        try:
            external_id = normalise_phone(external_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    elif payload.kind == "gmail":
        external_id = external_id.lower()
    if not external_id:
        raise HTTPException(status_code=422, detail="Channel external_id is required")
    connection = ChannelConnection(
        tenant_id=ctx.tenant_id,
        assistant_id=assistant_id,
        kind=payload.kind,
        external_id=external_id,
        status="active",
        settings=payload.settings,
    )
    try:
        async with session.begin_nested():
            session.add(connection)
            await session.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This channel is already connected") from None
    if payload.kind == "gmail":
        client = FakeGmailClient(external_id, get_settings().gmail_fake_dir)
        history_id = await client.watch("fake")
        connection.settings = {
            **connection.settings,
            "history_id": history_id,
            "watch_renewed_at": datetime.now(UTC).isoformat(),
        }
        await enqueue_gmail_sync(session, connection.id)
    await write_audit(session, ctx, "channel.created", "channel_connection", connection.id)
    return connection


async def update_channel_connection(
    session: AsyncSession,
    ctx: TenantContext,
    connection: ChannelConnection,
    payload: ChannelConnectionUpdate,
    *,
    allow_assistant_reassignment: bool = False,
) -> ChannelConnection:
    changed = False
    if "assistant_id" in payload.model_fields_set:
        if not allow_assistant_reassignment:
            raise HTTPException(
                status_code=422,
                detail="Assistant reassignment is not supported here",
            )
        if payload.assistant_id is not None and payload.assistant_id != connection.assistant_id:
            if await session.get(Assistant, payload.assistant_id) is None:
                raise HTTPException(status_code=404, detail="Assistant not found")
            connection.assistant_id = payload.assistant_id
            changed = True
    if "include_rules" in payload.model_fields_set:
        if connection.kind != "gmail":
            raise HTTPException(
                status_code=422, detail="Include rules are only supported for Gmail"
            )
        connection.settings = {
            **connection.settings,
            "include_rules": [rule.model_dump() for rule in payload.include_rules or []],
        }
        changed = True
    if changed:
        await write_audit(session, ctx, "channel.updated", "channel_connection", connection.id)
    return connection


async def delete_channel_connection(
    session: AsyncSession,
    ctx: TenantContext,
    connection: ChannelConnection,
) -> None:
    await write_audit(session, ctx, "channel.deleted", "channel_connection", connection.id)
    await session.delete(connection)
