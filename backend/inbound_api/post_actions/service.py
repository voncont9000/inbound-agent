from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from inbound_core.config import (
    AssistantConfig,
    EmailCustomerAction,
    NotifyStaffAction,
    SheetsRowAction,
    WebhookAction,
)
from inbound_core.post_actions import (
    derive_events,
    format_value,
    matched_events,
    render_subject,
    render_template,
)
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.channels.credentials import decrypt_credential
from inbound_api.jobs import current_job, enqueue
from inbound_api.models import (
    ActionRun,
    Assistant,
    Conversation,
    Identity,
    Intake,
    IntakeVersion,
    Job,
    Person,
    PostActionSecret,
    Task,
    TaskSource,
    TaskStatus,
)
from inbound_api.post_actions.providers import (
    ActionSkipped,
    EmailSender,
    PermanentActionError,
    SheetsWriter,
    WebhookSender,
    email_sender_from_settings,
    sheets_writer_for,
    sign_webhook,
    webhook_sender_from_settings,
)
from inbound_api.settings import Settings, get_settings

LOGGER = logging.getLogger(__name__)
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


async def plan_conversation_actions(
    session: AsyncSession,
    conversation_id: UUID,
) -> dict[str, int]:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return {"queued": 0}
    if conversation.metrics.get("test_call") is True:
        return {"queued": 0}
    assistant = await session.get(Assistant, conversation.assistant_id)
    if assistant is None:
        return {"queued": 0}
    config = AssistantConfig.model_validate(assistant.config)
    if not config.post_actions:
        return {"queued": 0}

    task_rows = await session.scalars(
        select(Task).where(
            Task.conversation_id == conversation.id,
            Task.status != TaskStatus.DISMISSED,
        )
    )
    events = derive_events(task.kind for task in task_rows)
    intake = await _merged_intake(session, conversation.id)
    service_key = _service_key(intake)
    queued = 0
    for action in config.post_actions:
        matched = matched_events(
            action,
            events,
            channel=conversation.channel.value,
            service=service_key,
        )
        if not matched:
            continue
        await session.execute(
            pg_insert(ActionRun)
            .values(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                action_key=action.key,
                action_kind=action.kind,
            )
            .on_conflict_do_nothing(index_elements=["conversation_id", "action_key"])
        )
        run = await session.scalar(
            select(ActionRun)
            .where(
                ActionRun.conversation_id == conversation.id,
                ActionRun.action_key == action.key,
            )
            .with_for_update()
        )
        if run is None:
            continue
        new_events = matched - set(run.fired_events) - set(run.pending_events)
        if not new_events:
            continue
        run.action_kind = action.kind
        run.status = "queued"
        run.pending_events = sorted(set(run.pending_events) | new_events)
        run.fire_count += 1
        await enqueue(
            session,
            "post_action_execute",
            {
                "conversation_id": str(conversation.id),
                "action_key": action.key,
                "fire_count": run.fire_count,
            },
        )
        queued += 1
    return {"queued": queued}


async def execute_conversation_action(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    action_key: str,
    fire_count: int,
    settings: Settings | None = None,
    email_sender: EmailSender | None = None,
    sheets_writer: SheetsWriter | None = None,
    webhook_sender: WebhookSender | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    run = await session.scalar(
        select(ActionRun)
        .where(
            ActionRun.conversation_id == conversation_id,
            ActionRun.action_key == action_key,
        )
        .with_for_update()
    )
    if run is None or run.fire_count != fire_count or run.status != "queued":
        return {"stale": True}
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        _finish_run(run, "skipped", {"reason": "Conversation no longer exists"})
        return {"status": "skipped"}
    assistant = await session.get(Assistant, conversation.assistant_id)
    if assistant is None:
        _finish_run(run, "skipped", {"reason": "Assistant no longer exists"})
        return {"status": "skipped"}
    config = AssistantConfig.model_validate(assistant.config)
    action = next((item for item in config.post_actions if item.key == action_key), None)
    if action is None or not action.enabled:
        _finish_run(run, "skipped", {"reason": "Action is no longer configured"})
        return {"status": "skipped"}
    if action.kind != run.action_kind:
        run.action_kind = action.kind

    tasks = list(
        (await session.scalars(select(Task).where(Task.conversation_id == conversation.id))).all()
    )
    context = await build_action_context(
        session,
        conversation=conversation,
        assistant=assistant,
        config=config,
        tasks=tasks,
        events=sorted(run.pending_events),
        settings=settings,
    )
    try:
        output = await _dispatch_action(
            session,
            assistant=assistant,
            action=action,
            context=context,
            settings=settings,
            email_sender=email_sender,
            sheets_writer=sheets_writer,
            webhook_sender=webhook_sender,
            run=run,
        )
    except ActionSkipped as error:
        run.attempts = await _current_attempt(session)
        _finish_run(run, "skipped", {"reason": str(error)})
        if error.setup_task:
            _add_action_task(
                session,
                conversation,
                kind="post_action_setup",
                action_key=action.key,
                details=error.setup_task,
            )
        return {"status": "skipped"}
    except PermanentActionError as error:
        run.attempts = await _current_attempt(session)
        _finish_run(run, "failed", {"error": str(error)})
        _add_action_task(
            session,
            conversation,
            kind="post_action_failed",
            action_key=action.key,
            details={"message": str(error)},
        )
        return {"status": "failed"}
    except Exception as error:
        attempt = await _current_attempt(session)
        if attempt >= settings.job_max_attempts:
            run.attempts = attempt
            _finish_run(run, "failed", {"error": _safe_error(error)})
            _add_action_task(
                session,
                conversation,
                kind="post_action_failed",
                action_key=action.key,
                details={"message": "Action delivery failed after retries"},
            )
            return {"status": "failed"}
        run_id = run.id
        tenant_id = run.tenant_id
        pending = list(run.pending_events)
        error_text = _safe_error(error)
        await session.rollback()
        from inbound_api.db import tenant_session

        async with tenant_session(tenant_id) as retry_session:
            retry_run = await retry_session.scalar(
                select(ActionRun).where(ActionRun.id == run_id).with_for_update()
            )
            if retry_run and retry_run.fire_count == fire_count:
                retry_run.status = "queued"
                retry_run.attempts = attempt
                retry_run.last_error = error_text
                retry_run.pending_events = pending
        raise

    _finish_run(run, "succeeded", output)
    run.attempts = await _current_attempt(session)
    return {"status": "succeeded", "output": output}


async def build_action_context(
    session: AsyncSession,
    *,
    conversation: Conversation,
    assistant: Assistant,
    config: AssistantConfig,
    tasks: list[Task] | None = None,
    events: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, str]:
    settings = settings or get_settings()
    intake = await _merged_intake(session, conversation.id)
    service_key = _service_key(intake)
    service_config = config.services.get(service_key)
    service_label = service_config.label if service_config else service_key
    person = (
        await session.get(Person, conversation.person_id)
        if conversation.person_id is not None
        else None
    )
    identities = []
    if person:
        identities = list(
            (await session.scalars(select(Identity).where(Identity.person_id == person.id))).all()
        )
    email = next(
        (identity.value_normalized for identity in identities if identity.type.value == "email"), ""
    )
    phone = next(
        (identity.value_normalized for identity in identities if identity.type.value == "phone"), ""
    )
    merged_tasks = tasks
    if merged_tasks is None:
        merged_tasks = list(
            (
                await session.scalars(select(Task).where(Task.conversation_id == conversation.id))
            ).all()
        )
    merged_tasks = [task for task in merged_tasks if task.status != TaskStatus.DISMISSED]
    lines: list[str] = []
    labels = {field.name: field.label for field in service_config.fields} if service_config else {}
    variables = {
        "business_name": config.business_name,
        "contact.name": person.display_name if person else "",
        "contact.email": email,
        "contact.phone": phone,
        "channel": conversation.channel.value,
        "started_at": conversation.started_at.astimezone(
            ZoneInfo(config.office_hours.timezone)
        ).strftime("%Y-%m-%d %H:%M %Z"),
        "service": service_label,
        "service_key": service_key,
        "summary": conversation.summary or "",
        "urgency": "urgent" if "urgent" in (events or []) else "normal",
        "urgency_reasons": format_value((conversation.metrics or {}).get("urgency_reasons", [])),
        "tasks": "\n".join(_task_text(task) for task in merged_tasks),
        "events": ", ".join(events or []),
        "conversation.id": str(conversation.id),
        "conversation.link": f"{settings.app_base_url.rstrip('/')}/conversations/{conversation.id}",
    }
    fields = intake.get("fields", {})
    for field, field_data in fields.items() if isinstance(fields, dict) else []:
        key = str(field)
        value = field_data.get("value") if isinstance(field_data, dict) else field_data
        formatted = format_value(value)
        variables[f"intake.{key}"] = formatted
        lines.append(f"{labels.get(key, key)}: {formatted}")
    variables["intake_table"] = "\n".join(lines)
    return variables


async def _dispatch_action(
    session: AsyncSession,
    *,
    assistant: Assistant,
    action,
    context: dict[str, str],
    settings: Settings,
    email_sender: EmailSender | None,
    sheets_writer: SheetsWriter | None,
    webhook_sender: WebhookSender | None,
    run: ActionRun,
) -> dict[str, Any]:
    if isinstance(action, EmailCustomerAction):
        recipient = context.get("contact.email", "")
        if not recipient:
            raise ActionSkipped("No known customer email address")
        try:
            recipient = str(_EMAIL_ADAPTER.validate_python(recipient))
        except ValidationError as error:
            raise ActionSkipped("The known customer email address is invalid") from error
        sender = email_sender or email_sender_from_settings(session, settings=settings)
        return await sender.send(
            assistant_id=assistant.id,
            to=[recipient],
            subject=render_subject(action.subject_template, context),
            body=render_template(action.body_template, context),
        )
    if isinstance(action, NotifyStaffAction):
        sender = email_sender or email_sender_from_settings(session, settings=settings)
        return await sender.send(
            assistant_id=assistant.id,
            to=[str(address) for address in action.to],
            subject=render_subject(action.subject_template, context),
            body=render_template(action.body_template, context),
        )
    if isinstance(action, SheetsRowAction):
        connection, writer = (
            await sheets_writer_for(session, assistant.id, settings=settings)
            if sheets_writer is None
            else (None, sheets_writer)
        )
        headers = [column.header for column in action.columns]
        row = [
            render_template(column.value_template, context, sheet_cell=True)
            for column in action.columns
        ]
        if connection is None:
            from inbound_api.integrations.google_auth import google_connection_for

            connection = await google_connection_for(session, assistant.id)
            if connection is None:
                raise ActionSkipped(
                    "Google Sheets is not connected",
                    {"message": "Connect Google Sheets for this assistant"},
                )
        return await writer.append_row(
            connection=connection,
            spreadsheet_id=action.spreadsheet_id,
            sheet_name=action.sheet_name,
            headers=headers,
            row=row,
        )
    if isinstance(action, WebhookAction):
        secret = await session.scalar(
            select(PostActionSecret).where(
                PostActionSecret.assistant_id == assistant.id,
                PostActionSecret.action_key == action.key,
            )
        )
        if secret is None:
            raise ActionSkipped(
                "Webhook signing secret is not configured",
                {"message": "Generate a signing secret for this webhook action"},
            )
        payload = {
            "event_id": f"{run.id}:{run.fire_count}",
            "action_key": action.key,
            "events": sorted(run.pending_events),
            "conversation_id": context["conversation.id"],
            "context": context,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "inbound-agent-post-actions/1.0",
            "X-Inbound-Event-Id": payload["event_id"],
            "X-Inbound-Signature": sign_webhook(decrypt_credential(secret.secret_ciphertext), body),
        }
        sender = webhook_sender or webhook_sender_from_settings(settings=settings)
        return await sender.post(url=str(action.url), body=body, headers=headers)
    raise PermanentActionError(f"Unsupported post-action kind: {action.kind}")


async def _merged_intake(session: AsyncSession, conversation_id: UUID) -> dict[str, Any]:
    rows = list(
        (
            await session.scalars(select(Intake).where(Intake.conversation_id == conversation_id))
        ).all()
    )
    preferred = next((row for row in rows if row.version == IntakeVersion.MERGED), None)
    if preferred is None:
        preferred = next((row for row in rows if row.version == IntakeVersion.POST_CALL), None)
    return preferred.data if preferred else {}


def _task_text(task: Task) -> str:
    payload = task.payload
    description = payload.get("reason") or payload.get("title") or payload.get("description") or ""
    return f"{task.kind}: {format_value(description)}"


def _service_key(intake: dict[str, Any]) -> str:
    value = intake.get("service_key", intake.get("service", ""))
    if isinstance(value, dict):
        value = value.get("key", value.get("service_key", ""))
    return str(value or "")


def _finish_run(run: ActionRun, status: str, output: dict[str, Any]) -> None:
    run.status = status
    run.output = output
    run.last_error = output.get("error")
    run.fired_events = sorted(set(run.fired_events) | set(run.pending_events))
    run.pending_events = []


def _add_action_task(
    session: AsyncSession,
    conversation: Conversation,
    *,
    kind: str,
    action_key: str,
    details: dict[str, Any],
) -> None:
    session.add(
        Task(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            person_id=conversation.person_id,
            kind=kind,
            source=TaskSource.LIVE,
            status=TaskStatus.OPEN,
            payload={"action_key": action_key, **details},
        )
    )


async def _current_attempt(session: AsyncSession) -> int:
    job_context = current_job.get()
    if job_context is None:
        return 1
    return int(await session.scalar(select(Job.attempts).where(Job.id == job_context[0])) or 1)


def _safe_error(error: Exception) -> str:
    text = str(error).strip()
    return f"{type(error).__name__}: {text[:500]}" if text else type(error).__name__
