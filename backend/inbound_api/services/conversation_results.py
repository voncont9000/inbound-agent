from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import enqueue
from inbound_api.models import (
    Conversation,
    ConversationStatus,
    Intake,
    IntakeVersion,
    KnowledgeGap,
    Message,
    MessageRole,
    Task,
    TaskSource,
    TaskStatus,
)


async def persist_voice_report(
    session, tenant_id: UUID, conversation: Conversation, payload
) -> None:
    for turn in payload.transcript:
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=MessageRole(turn.role),
                text=turn.text,
                ts=turn.timestamp,
                meta={},
            )
        )
    intakes = [
        (IntakeVersion.LIVE, payload.live_intake),
        (IntakeVersion.MERGED, payload.merged_intake),
    ]
    if payload.post_call_intake is not None:
        intakes.append((IntakeVersion.POST_CALL, payload.post_call_intake))
    for version, data in intakes:
        session.add(
            Intake(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                version=version,
                data=data,
            )
        )
    for item in payload.tasks:
        if item.get("type") in {"knowledge_gap", "end_call"} or item.get("kind") in {
            "knowledge_gap",
            "end_call",
        }:
            continue
        kind = item.get("type") or item.get("kind") or "task"
        source = item.get("source")
        if source not in {"live", "post_call"}:
            source = "live"
        task_payload = {
            key: value for key, value in item.items() if key not in {"type", "kind", "source"}
        }
        due_at = item.get("due_at")
        if isinstance(due_at, str):
            try:
                due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            except ValueError:
                due_at = None
        if not isinstance(due_at, datetime) or due_at.tzinfo is None:
            due_at = None
        session.add(
            Task(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                due_at=due_at,
                kind=kind,
                status=TaskStatus.OPEN,
                payload=task_payload,
                source=TaskSource(source),
            )
        )
    gaps: dict[str, str] = {}
    for item in payload.knowledge_gaps:
        question, evidence = item.get("question"), item.get("evidence")
        if isinstance(question, str) and isinstance(evidence, str):
            gaps.setdefault(question, evidence)
    for item in payload.tasks:
        if item.get("type") == "knowledge_gap" or item.get("kind") == "knowledge_gap":
            question, evidence = item.get("question"), item.get("evidence")
            if isinstance(question, str) and isinstance(evidence, str):
                gaps.setdefault(question, evidence)
    for question, evidence in gaps.items():
        session.add(
            KnowledgeGap(
                tenant_id=tenant_id,
                assistant_id=conversation.assistant_id,
                conversation_id=conversation.id,
                question=question,
                evidence=evidence,
            )
        )
    conversation.status = ConversationStatus.COMPLETED
    conversation.ended_at = payload.ended_at
    conversation.end_reason = payload.end_reason
    conversation.summary = payload.summary
    conversation.metrics = {**(conversation.metrics or {}), **payload.metrics}
    conversation.estimated_cost = payload.estimated_cost
    await enqueue(session, "graph_ingest", {"conversation_id": str(conversation.id)})
    await enqueue(session, "post_actions_run", {"conversation_id": str(conversation.id)})


async def persist_extracted_results(
    session: AsyncSession,
    tenant_id: UUID,
    conversation: Conversation,
    *,
    live_intake: dict[str, Any],
    post_call_intake: dict[str, Any],
    merged_intake: dict[str, Any],
    summary: str,
    tasks: list[dict[str, Any]],
    knowledge_gaps: list[dict[str, Any]],
) -> None:
    for version, data in (
        (IntakeVersion.LIVE, live_intake),
        (IntakeVersion.POST_CALL, post_call_intake),
        (IntakeVersion.MERGED, merged_intake),
    ):
        intake = await session.scalar(
            select(Intake)
            .where(Intake.conversation_id == conversation.id, Intake.version == version)
            .with_for_update()
        )
        if intake is None:
            intake = Intake(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                version=version,
                data=data,
            )
            session.add(intake)
        else:
            intake.data = data
    await session.execute(
        delete(Task).where(
            Task.conversation_id == conversation.id,
            Task.source == TaskSource.POST_CALL,
            Task.status == TaskStatus.OPEN,
        )
    )
    for item in tasks:
        kind = item.get("type") or item.get("kind") or "task"
        if kind in {"knowledge_gap", "end_call"}:
            continue
        due_at = item.get("due_at")
        if isinstance(due_at, str):
            try:
                due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            except ValueError:
                due_at = None
        if not isinstance(due_at, datetime) or due_at.tzinfo is None:
            due_at = None
        session.add(
            Task(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                kind=kind,
                status=TaskStatus.OPEN,
                due_at=due_at,
                payload={
                    key: value
                    for key, value in item.items()
                    if key not in {"type", "kind", "source"}
                },
                source=TaskSource.POST_CALL,
            )
        )
    seen_questions: set[str] = set()
    for gap in knowledge_gaps:
        question, evidence = gap.get("question"), gap.get("evidence")
        if (
            not isinstance(question, str)
            or not isinstance(evidence, str)
            or question in seen_questions
        ):
            continue
        seen_questions.add(question)
        exists = await session.scalar(
            select(KnowledgeGap.id).where(
                KnowledgeGap.conversation_id == conversation.id,
                KnowledgeGap.question == question,
            )
        )
        if exists is None:
            session.add(
                KnowledgeGap(
                    tenant_id=tenant_id,
                    assistant_id=conversation.assistant_id,
                    conversation_id=conversation.id,
                    question=question,
                    evidence=evidence,
                )
            )
    conversation.summary = summary
    await enqueue(session, "graph_ingest", {"conversation_id": str(conversation.id)})
    await enqueue(session, "post_actions_run", {"conversation_id": str(conversation.id)})
