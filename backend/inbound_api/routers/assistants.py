from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from inbound_core.config import AssistantConfig, assistant_config_json_schema
from inbound_core.custom_templates import (
    SECTORS,
    USE_CASES,
    build_custom_template_prompt,
    parse_custom_template,
)
from inbound_core.setup_draft import build_services_draft_prompt, parse_services_draft
from inbound_core.templates import find_template
from inbound_core.templates import list_templates as load_templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.jobs import enqueue
from inbound_api.models import (
    Assistant,
    AssistantConfigVersion,
    ChannelConnection,
    Conversation,
    ConversationStatus,
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
    CustomTemplateOptions,
    CustomTemplateRequest,
    CustomTemplateResponse,
    ServicesDraftResponse,
    TemplateResponse,
    TestCallResponse,
)
from inbound_api.services.audit import write_audit
from inbound_api.services.conversation_delete import (
    delete_conversations as delete_conversations_service,
)
from inbound_api.services.diff import config_diff
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.services.knowledge_import import make_genai_client
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings
from inbound_api.web_call_tokens import issue

router = APIRouter(tags=["assistants"])
_staff = Depends(require_role(MembershipRole.STAFF))
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


def _template_exists(name: str) -> bool:
    return find_template(get_settings().templates_dir, name) is not None


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(_: TenantContext = _staff) -> list[TemplateResponse]:
    return [
        TemplateResponse(
            name=template.name,
            label=template.meta.label,
            description=template.meta.description,
            sets_up=template.meta.sets_up,
            sample_business_name=(
                template.meta.sample.business_name if template.meta.sample else None
            ),
            config=template.config,
        )
        for template in load_templates(get_settings().templates_dir)
    ]


@router.get("/templates/custom-options", response_model=CustomTemplateOptions)
async def custom_template_options(_: TenantContext = _staff) -> CustomTemplateOptions:
    return CustomTemplateOptions(sectors=list(SECTORS), use_cases=list(USE_CASES))


@router.post("/templates/custom-draft", response_model=CustomTemplateResponse)
async def draft_custom_template(
    payload: CustomTemplateRequest, _: TenantContext = _owner
) -> CustomTemplateResponse:
    """Generate a starting template for a sector and use case, derived from the blank template."""
    settings = get_settings()
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Template generation is not configured.")
    blank = find_template(settings.templates_dir, "blank")
    if blank is None:
        raise HTTPException(status_code=500, detail="Blank template is missing")
    base = blank.config
    if payload.language is not None:
        base = base.model_copy(update={"language": payload.language})
    client = make_genai_client(api_key)
    try:
        response = await client.aio.models.generate_content(
            model=settings.setup_draft_model,
            contents=build_custom_template_prompt(
                sector=payload.sector,
                use_case=payload.use_case,
                details=payload.details,
                base=base,
            ),
            config={"response_mime_type": "application/json"},
        )
    finally:
        await client.aio.aclose()
    draft = parse_custom_template(
        response.text or "", base, fallback_label=f"{payload.sector}: {payload.use_case}"
    )
    return CustomTemplateResponse(
        label=draft.label,
        description=draft.description,
        sets_up=draft.sets_up,
        config=draft.config,
        warnings=draft.warnings,
    )


@router.get("/assistant-config/schema", response_model=dict[str, Any])
async def assistant_config_schema(_: TenantContext = _staff) -> dict:
    return assistant_config_json_schema()


@router.get("/assistants", response_model=AssistantListResponse)
async def list_assistants(
    ctx: TenantContext = _staff, session: AsyncSession = _tenant_session_dependency
) -> AssistantListResponse:
    rows = await session.scalars(select(Assistant).order_by(Assistant.created_at.desc()))
    assistants = list(rows)
    count_rows = await session.execute(
        select(Conversation.assistant_id, func.count(Conversation.id)).group_by(
            Conversation.assistant_id
        )
    )
    counts = dict(count_rows.all())
    return AssistantListResponse(
        items=[
            AssistantResponse.model_validate(assistant).model_copy(
                update={"conversation_count": counts.get(assistant.id, 0)}
            )
            for assistant in assistants
        ]
    )


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


@router.post("/assistants/{assistant_id}/services-draft", response_model=ServicesDraftResponse)
async def draft_services(
    assistant_id: UUID,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ServicesDraftResponse:
    """Suggest services and intake fields from the assistant's prompt and knowledge."""
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    settings = get_settings()
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Suggestions are not configured.")
    config = AssistantConfig.model_validate(assistant.config)
    client = make_genai_client(api_key)
    try:
        response = await client.aio.models.generate_content(
            model=settings.setup_draft_model,
            contents=build_services_draft_prompt(config),
            config={"response_mime_type": "application/json"},
        )
    finally:
        await client.aio.aclose()
    services, warnings = parse_services_draft(response.text or "", config)
    return ServicesDraftResponse(
        services={key: service.model_dump(mode="json") for key, service in services.items()},
        warnings=warnings,
    )


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
    delete_conversations: bool = False,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> None:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    conversations = list(
        await session.scalars(
            select(Conversation).where(Conversation.assistant_id == assistant_id).with_for_update()
        )
    )
    if conversations and not delete_conversations:
        raise HTTPException(status_code=409, detail="Assistant has conversations")
    if any(item.status == ConversationStatus.ACTIVE for item in conversations):
        raise HTTPException(status_code=409, detail="Conversation is still in progress")
    if conversations:
        result = await delete_conversations_service(
            session,
            ctx,
            [conversation.id for conversation in conversations],
        )
        if result.skipped_in_progress_ids:
            raise HTTPException(status_code=409, detail="Conversation is still in progress")
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
