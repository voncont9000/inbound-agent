from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from inbound_core.config import assistant_config_json_schema, load_assistant_config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.jobs import enqueue
from inbound_api.models import (
    Assistant,
    AssistantConfigVersion,
    ChannelConnection,
    MembershipRole,
)
from inbound_api.schemas import (
    AssistantCreate,
    AssistantListResponse,
    AssistantResponse,
    AssistantUpdate,
    AssistantVersionsResponse,
    ChannelConnectionCreate,
    ChannelConnectionResponse,
    ChannelConnectionsResponse,
    TemplateResponse,
    TestCallResponse,
)
from inbound_api.services.audit import write_audit
from inbound_api.services.diff import config_diff
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings
from inbound_api.web_call_tokens import issue

router = APIRouter(tags=["assistants"])
_staff = Depends(require_role(MembershipRole.STAFF))
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


def _template_exists(name: str) -> bool:
    return any(
        path.is_file() and path.stem == name for path in get_settings().templates_dir.glob("*.yaml")
    )


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(_: TenantContext = _staff) -> list[TemplateResponse]:
    templates = get_settings().templates_dir
    return [
        TemplateResponse(
            name=path.stem,
            config=load_assistant_config(path, template_dir=templates),
        )
        for path in sorted(path for path in templates.glob("*.yaml") if path.is_file())
    ]


@router.get("/assistant-config/schema", response_model=dict[str, Any])
async def assistant_config_schema(_: TenantContext = _staff) -> dict:
    return assistant_config_json_schema()


@router.get("/assistants", response_model=AssistantListResponse)
async def list_assistants(
    ctx: TenantContext = _staff, session: AsyncSession = _tenant_session_dependency
) -> AssistantListResponse:
    rows = await session.scalars(select(Assistant).order_by(Assistant.created_at.desc()))
    return AssistantListResponse(items=[AssistantResponse.model_validate(row) for row in rows])


@router.post("/assistants", response_model=AssistantResponse, status_code=201)
async def create_assistant(
    payload: AssistantCreate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> AssistantResponse:
    if payload.template and not _template_exists(payload.template):
        raise HTTPException(status_code=422, detail="Unknown template")
    assistant = Assistant(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        template=payload.template,
        config=payload.config.model_dump(mode="json"),
        version=1,
    )
    session.add(assistant)
    await session.flush()
    session.add(
        AssistantConfigVersion(
            tenant_id=ctx.tenant_id,
            assistant_id=assistant.id,
            version=1,
            config=assistant.config,
            created_by=ctx.user.id,
        )
    )
    await write_audit(session, ctx, "assistant.created", "assistant", assistant.id)
    return AssistantResponse.model_validate(assistant)


@router.get("/assistants/{assistant_id}", response_model=AssistantResponse)
async def get_assistant(
    assistant_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> AssistantResponse:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    return AssistantResponse.model_validate(assistant)


@router.post(
    "/assistants/{assistant_id}/test-calls",
    response_model=TestCallResponse,
)
async def create_test_call(
    assistant_id: UUID,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> TestCallResponse:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    base_url = get_settings().web_call_base_url
    if not base_url:
        raise HTTPException(status_code=503, detail="Test calls are not configured")
    token, expires_at = issue(ctx.tenant_id, assistant_id)
    await write_audit(session, ctx, "assistant.test_call_created", "assistant", assistant_id)
    return TestCallResponse(
        url=urljoin(base_url, f"?call_token={token}"),
        expires_at=expires_at,
    )


@router.put("/assistants/{assistant_id}", response_model=AssistantResponse)
async def update_assistant(
    assistant_id: UUID,
    payload: AssistantUpdate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> AssistantResponse:
    assistant = await session.scalar(
        select(Assistant).where(Assistant.id == assistant_id).with_for_update()
    )
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    if payload.template and not _template_exists(payload.template):
        raise HTTPException(status_code=422, detail="Unknown template")
    old_version = assistant.version
    old_config = assistant.config
    new_config = payload.config.model_dump(mode="json")
    changes = config_diff(old_config, new_config)
    new_name = payload.name if payload.name is not None else assistant.name
    if assistant.name != new_name:
        changes["name"] = {"from": assistant.name, "to": new_name}
    if assistant.template != payload.template:
        changes["template"] = {"from": assistant.template, "to": payload.template}
    assistant.name = new_name
    assistant.template = payload.template
    assistant.config = new_config
    assistant.version += 1
    await session.flush()
    await session.refresh(assistant)
    session.add(
        AssistantConfigVersion(
            tenant_id=ctx.tenant_id,
            assistant_id=assistant.id,
            version=assistant.version,
            config=new_config,
            created_by=ctx.user.id,
        )
    )
    await write_audit(
        session,
        ctx,
        "assistant.updated",
        "assistant",
        assistant.id,
        {
            "version": {"from": old_version, "to": assistant.version},
            "changes": changes,
        },
    )
    return AssistantResponse.model_validate(assistant)


@router.delete("/assistants/{assistant_id}", status_code=204)
async def delete_assistant(
    assistant_id: UUID,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> None:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    conversation_id = await session.scalar(
        text("SELECT id FROM conversations WHERE assistant_id = :assistant_id LIMIT 1"),
        {"assistant_id": assistant_id},
    )
    if conversation_id is not None:
        raise HTTPException(status_code=409, detail="Assistant has conversations")
    await write_audit(session, ctx, "assistant.deleted", "assistant", assistant_id)
    await enqueue(
        session,
        "knowledge_files_cleanup",
        {"assistant_id": str(assistant_id)},
    )
    await session.delete(assistant)


@router.get("/assistants/{assistant_id}/versions", response_model=AssistantVersionsResponse)
async def list_assistant_versions(
    assistant_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> AssistantVersionsResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    rows = await session.scalars(
        select(AssistantConfigVersion)
        .where(AssistantConfigVersion.assistant_id == assistant_id)
        .order_by(AssistantConfigVersion.version.desc())
    )
    return AssistantVersionsResponse(items=list(rows))


@router.get("/assistants/{assistant_id}/channels", response_model=ChannelConnectionsResponse)
async def list_channels(
    assistant_id: UUID,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ChannelConnectionsResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    rows = await session.scalars(
        select(ChannelConnection)
        .where(ChannelConnection.assistant_id == assistant_id)
        .order_by(ChannelConnection.created_at.desc())
    )
    return ChannelConnectionsResponse(
        items=[ChannelConnectionResponse.model_validate(row) for row in rows]
    )


@router.post(
    "/assistants/{assistant_id}/channels",
    response_model=ChannelConnectionResponse,
    status_code=201,
)
async def create_channel(
    assistant_id: UUID,
    payload: ChannelConnectionCreate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ChannelConnectionResponse:
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
    return ChannelConnectionResponse.model_validate(connection)


@router.delete("/assistants/{assistant_id}/channels/{channel_id}", status_code=204)
async def delete_channel(
    assistant_id: UUID,
    channel_id: UUID,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> None:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    connection = await session.scalar(
        select(ChannelConnection).where(
            ChannelConnection.id == channel_id,
            ChannelConnection.assistant_id == assistant_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Channel connection not found")
    await write_audit(session, ctx, "channel.deleted", "channel_connection", channel_id)
    await session.delete(connection)
