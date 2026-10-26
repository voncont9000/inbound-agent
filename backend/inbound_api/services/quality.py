import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from inbound_core.config import AssistantConfig
from inbound_core.languages import DEFAULT_RECORDING_NOTICE, LOCALES
from inbound_core.prompts import render_business_context
from inbound_core.quality import (
    JUDGE_VERSION,
    build_judge_prompt,
    judge_conversation,
    needs_review,
    objective_metrics,
    split_platform_notices,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import PermanentJobError, register
from inbound_api.models import (
    Assistant,
    Conversation,
    ConversationChannel,
    ConversationScore,
    Intake,
    IntakeVersion,
    Message,
    MessageRole,
    Task,
)
from inbound_api.services.knowledge_import import make_genai_client
from inbound_api.settings import get_settings


@register("conversation_score")
async def conversation_score(
    session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any] | None:
    conversation_id = UUID(payload["conversation_id"])
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    assistant = await session.get(Assistant, conversation.assistant_id)
    if assistant is None:
        raise PermanentJobError("Assistant not found")

    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.ts, Message.id)
        )
    )
    transcript_rows = [
        {
            "role": "assistant" if message.role == MessageRole.ASSISTANT else "caller",
            "text": message.text,
        }
        for message in messages
        if message.role != MessageRole.SYSTEM
    ]
    merged_intake = await session.scalar(
        select(Intake).where(
            Intake.conversation_id == conversation_id,
            Intake.version == IntakeVersion.MERGED,
        )
    )
    tasks = list(
        await session.scalars(
            select(Task)
            .where(Task.conversation_id == conversation_id)
            .order_by(Task.created_at, Task.id)
        )
    )
    try:
        config = AssistantConfig.model_validate(assistant.config)
    except (ValidationError, ValueError, TypeError):
        config = None
        instructions = json.dumps(assistant.config, ensure_ascii=False, default=str)
    else:
        try:
            instructions = render_business_context(
                config,
                channel="phone" if conversation.channel == ConversationChannel.PHONE else "text",
            )
        except (ValidationError, ValueError, TypeError):
            instructions = json.dumps(assistant.config, ensure_ascii=False, default=str)

    notices = {DEFAULT_RECORDING_NOTICE}
    notices.update(locale.recording_notice for locale in LOCALES.values())
    if config is not None:
        notices.add(config.recording.consent_message)
    transcript_rows = split_platform_notices(transcript_rows, notices)

    intake_data = merged_intake.data if merged_intake is not None else None
    task_rows = [
        {"kind": task.kind, "status": task.status.value, "payload": task.payload} for task in tasks
    ]
    prompt = build_judge_prompt(
        instructions=instructions,
        transcript_rows=transcript_rows,
        intake=intake_data,
        tasks=task_rows,
        channel=conversation.channel.value,
    )
    settings = get_settings()
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        raise PermanentJobError("GOOGLE_API_KEY is not configured")
    client = make_genai_client(api_key)
    try:
        judgement = await judge_conversation(client, settings.quality_judge_model, prompt)
    finally:
        await client.aio.aclose()

    objective = objective_metrics(conversation.metrics)
    review = needs_review(judgement["overall"], judgement["checks"], objective)
    values = {
        "tenant_id": conversation.tenant_id,
        "conversation_id": conversation.id,
        "assistant_id": conversation.assistant_id,
        "judge_model": settings.quality_judge_model,
        "judge_version": JUDGE_VERSION,
        "overall": judgement["overall"],
        "needs_review": review,
        "reviewed_at": None,
        "reviewed_by": None,
        "checks": judgement["checks"],
        "issues": judgement["issues"],
        "objective": objective,
        "updated_at": datetime.now(UTC),
    }
    statement = pg_insert(ConversationScore).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[ConversationScore.conversation_id],
        set_={key: value for key, value in values.items() if key != "conversation_id"},
    )
    await session.execute(statement)
    return {"overall": judgement["overall"], "needs_review": review}
