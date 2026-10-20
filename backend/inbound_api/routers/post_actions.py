from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from inbound_core.config import (
    AssistantConfig,
    EmailCustomerAction,
    NotifyStaffAction,
    SheetsRowAction,
    WebhookAction,
)
from inbound_core.post_actions import (
    TEMPLATE_VARIABLES,
    derive_events,
    matched_events,
    render_template,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.channels.credentials import encrypt_credential
from inbound_api.integrations.google_auth import google_connection_for, has_google_feature
from inbound_api.models import (
    Assistant,
    Conversation,
    MembershipRole,
    PostActionSecret,
    Task,
)
from inbound_api.post_actions.service import build_action_context, render_action_subject
from inbound_api.schemas import (
    PostActionTestRequest,
    PostActionTestResponse,
    PostActionVariable,
    WebhookSecretCreateResponse,
    WebhookSecretListResponse,
    WebhookSecretSummary,
)
from inbound_api.services.audit import write_audit
from inbound_api.settings import get_settings

router = APIRouter(tags=["post-actions"])
_staff = Depends(require_role(MembershipRole.STAFF))
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/post-actions/variables", response_model=list[PostActionVariable])
async def list_post_action_variables(_: TenantContext = _staff):
    return [
        PostActionVariable(name=name, description=description)
        for name, description in TEMPLATE_VARIABLES
    ]


@router.post(
    "/assistants/{assistant_id}/post-actions/test",
    response_model=PostActionTestResponse,
)
async def test_post_action(
    assistant_id: UUID,
    payload: PostActionTestRequest,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> PostActionTestResponse:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    if payload.conversation_id is None:
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.assistant_id == assistant_id)
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
    else:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.assistant_id == assistant_id,
            )
        )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    tasks = list(
        (await session.scalars(select(Task).where(Task.conversation_id == conversation.id))).all()
    )
    events = sorted(derive_events(task.kind for task in tasks if task.status.value != "dismissed"))
    config = AssistantConfig.model_validate(assistant.config)
    action = payload.action
    if set(action.trigger.services) - set(config.services):
        raise HTTPException(status_code=422, detail="Action references an unknown service")
    service_key = await _service_key_from_config_and_tasks(session, conversation.id, config)
    matched = sorted(
        matched_events(
            action,
            set(events),
            channel=conversation.channel.value,
            service=service_key,
        )
    )
    context = await build_action_context(
        session,
        conversation=conversation,
        assistant=assistant,
        config=config,
        tasks=tasks,
        events=matched,
    )
    settings = get_settings()
    would_skip = None
    rendered: dict
    if isinstance(action, EmailCustomerAction):
        to = context.get("contact.email", "")
        if not to:
            would_skip = "No known customer email address"
        if settings.email_sender == "off":
            would_skip = "Email sending is off"
        rendered = {
            "to": [to] if to else [],
            "subject": render_action_subject(action.subject_template, context),
            "body": render_template(action.body_template, context),
        }
    elif isinstance(action, NotifyStaffAction):
        if settings.email_sender == "off":
            would_skip = "Email sending is off"
        rendered = {
            "to": [str(address) for address in action.to],
            "subject": render_action_subject(action.subject_template, context),
            "body": render_template(action.body_template, context),
        }
    elif isinstance(action, SheetsRowAction):
        if settings.sheets_writer == "off":
            would_skip = "Google Sheets writing is off"
        connection = await google_connection_for(session, assistant_id)
        if connection is None or not has_google_feature(connection, "sheets"):
            would_skip = "Google Sheets is not connected"
        rendered = {
            "spreadsheet_id": action.spreadsheet_id,
            "sheet_name": action.sheet_name,
            "headers": [column.header for column in action.columns],
            "row": [
                render_template(column.value_template, context, sheet_cell=True)
                for column in action.columns
            ],
        }
    elif isinstance(action, WebhookAction):
        secret = await session.scalar(
            select(PostActionSecret).where(
                PostActionSecret.assistant_id == assistant_id,
                PostActionSecret.action_key == action.key,
            )
        )
        if secret is None:
            would_skip = "Webhook signing secret is not generated"
        if settings.webhook_sender == "off":
            would_skip = "Webhook delivery is off"
        rendered = {
            "url": str(action.url),
            "payload": {
                "event_id": "dry-run",
                "action_key": action.key,
                "events": matched,
                "conversation_id": str(conversation.id),
                "test": context["test_call"],
                "context": context,
            },
        }
    else:
        raise HTTPException(status_code=422, detail="Unsupported action kind")
    if not matched and would_skip is None:
        would_skip = "No trigger events match this conversation"
    return PostActionTestResponse(
        conversation_id=conversation.id,
        events=events,
        matched_events=matched,
        would_skip=would_skip,
        rendered=rendered,
    )


@router.get(
    "/assistants/{assistant_id}/post-actions/webhook-secrets",
    response_model=WebhookSecretListResponse,
)
async def list_webhook_secrets(
    assistant_id: UUID,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> WebhookSecretListResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    rows = await session.scalars(
        select(PostActionSecret)
        .where(PostActionSecret.assistant_id == assistant_id)
        .order_by(PostActionSecret.action_key)
    )
    return WebhookSecretListResponse(
        items=[
            WebhookSecretSummary(action_key=row.action_key, created_at=row.created_at)
            for row in rows
        ]
    )


@router.post(
    "/assistants/{assistant_id}/post-actions/{action_key}/webhook-secret",
    response_model=WebhookSecretCreateResponse,
)
async def rotate_webhook_secret(
    assistant_id: UUID,
    action_key: str,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> WebhookSecretCreateResponse:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    action = next(
        (
            action
            for action in AssistantConfig.model_validate(assistant.config).post_actions
            if action.key == action_key and isinstance(action, WebhookAction)
        ),
        None,
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Webhook action not found")
    secret_value = secrets.token_urlsafe(32)
    try:
        ciphertext = encrypt_credential(secret_value)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="CREDENTIALS_ENCRYPTION_KEY is not configured",
        ) from error
    secret = await session.scalar(
        select(PostActionSecret)
        .where(
            PostActionSecret.assistant_id == assistant_id,
            PostActionSecret.action_key == action_key,
        )
        .with_for_update()
    )
    if secret is None:
        secret = PostActionSecret(
            tenant_id=ctx.tenant_id,
            assistant_id=assistant_id,
            action_key=action_key,
            secret_ciphertext=ciphertext,
        )
        session.add(secret)
    else:
        secret.secret_ciphertext = ciphertext
        secret.updated_at = datetime.now(UTC)
    await session.flush()
    await write_audit(
        session,
        ctx,
        "post_action.webhook_secret_rotated",
        "post_action_secret",
        secret.id,
        {"action_key": action_key},
    )
    return WebhookSecretCreateResponse(
        action_key=action_key,
        secret=secret_value,
        created_at=secret.created_at,
    )


async def _service_key_from_config_and_tasks(session, conversation_id, config) -> str:
    from inbound_api.models import Intake, IntakeVersion

    intake = await session.scalar(
        select(Intake).where(
            Intake.conversation_id == conversation_id,
            Intake.version == IntakeVersion.MERGED,
        )
    )
    data = intake.data if intake else {}
    service = data.get("service_key", data.get("service", ""))
    if isinstance(service, dict):
        service = service.get("key", service.get("service_key", ""))
    return str(service or "")
