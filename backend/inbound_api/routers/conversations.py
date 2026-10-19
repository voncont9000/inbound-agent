from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from inbound_core.call_report import CallTransferMetrics
from pydantic import ValidationError
from sqlalchemy import literal_column, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.models import (
    ActionRun,
    Assistant,
    CallRecording,
    Conversation,
    Intake,
    IntakeReview,
    MembershipRole,
    Message,
    Person,
    Task,
)
from inbound_api.schemas import (
    ActionRunSummary,
    CallRecordingSummary,
    ConversationBulkDeleteRequest,
    ConversationBulkDeleteResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationReviewCreate,
    ConversationReviewResponse,
    ConversationSummary,
    IntakeResponse,
    MessageResponse,
    TaskSummary,
)
from inbound_api.services.audit import write_audit
from inbound_api.services.conversation_delete import delete_conversations
from inbound_api.services.pagination import page_query
from inbound_api.services.recordings import LocalRecordingStore
from inbound_api.services.review import merged_needs_review
from inbound_api.settings import get_settings

router = APIRouter(tags=["conversations"])
_staff = Depends(require_role(MembershipRole.STAFF))
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)

_HAS_REVIEW = literal_column(
    """
    EXISTS (
      SELECT 1 FROM intakes i
      CROSS JOIN LATERAL jsonb_each(
        CASE WHEN jsonb_typeof(i.data->'fields') = 'object'
             THEN i.data->'fields' ELSE '{}'::jsonb END
      ) f
      WHERE i.conversation_id = conversations.id
        AND i.version = 'merged'
        AND coalesce(f.value->>'status', '') <> 'human'
        AND (
          (f.value->'needs_review') = 'true'::jsonb
          OR (
            jsonb_typeof(f.value->'confidence') = 'number'
            AND (f.value->>'confidence')::float
              < coalesce((assistants.config->>'review_confidence_threshold')::float, 0.7)
          )
        )
    )
    """
)


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    channel: Literal["phone", "web", "gmail", "whatsapp"] | None = None,
    status: Literal["active", "completed", "failed"] | None = None,
    has_review: bool | None = None,
    person_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> ConversationListResponse:
    statement = select(Conversation).join(Assistant, Assistant.id == Conversation.assistant_id)
    if channel is not None:
        statement = statement.where(Conversation.channel == channel)
    if status is not None:
        statement = statement.where(Conversation.status == status)
    if person_id is not None:
        statement = statement.where(Conversation.person_id == person_id)
    if has_review is not None:
        statement = statement.where(_HAS_REVIEW if has_review else not_(_HAS_REVIEW))
    rows, next_cursor = await page_query(
        session,
        statement,
        created_at_column=Conversation.created_at,
        id_column=Conversation.id,
        cursor=cursor,
        limit=limit,
    )
    summary_rows = (
        await session.execute(
            select(
                Conversation.id,
                Person.display_name,
                _HAS_REVIEW.label("needs_review"),
            )
            .join(Assistant, Assistant.id == Conversation.assistant_id)
            .outerjoin(Person, Person.id == Conversation.person_id)
            .where(Conversation.id.in_([row.id for row in rows]))
        )
        if rows
        else None
    )
    summaries = (
        {
            conversation_id: (contact_name, needs_review)
            for conversation_id, contact_name, needs_review in summary_rows.all()
        }
        if summary_rows is not None
        else {}
    )
    return ConversationListResponse(
        items=[_conversation_summary(row, *summaries.get(row.id, (None, False))) for row in rows],
        next_cursor=next_cursor,
    )


@router.post(
    "/conversations/bulk-delete",
    response_model=ConversationBulkDeleteResponse,
)
async def bulk_delete_conversations(
    payload: ConversationBulkDeleteRequest,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ConversationBulkDeleteResponse:
    result = await delete_conversations(session, ctx, payload.ids)
    return ConversationBulkDeleteResponse(
        deleted=len(result.deleted_ids),
        skipped_in_progress=len(result.skipped_in_progress_ids),
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> Response:
    result = await delete_conversations(session, ctx, [conversation_id])
    if not result.deleted_ids and not result.skipped_in_progress_ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if result.skipped_in_progress_ids:
        raise HTTPException(status_code=409, detail="Conversation is still in progress")
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> ConversationDetailResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.ts, Message.id)
    )
    intakes = list(
        await session.scalars(
            select(Intake)
            .where(Intake.conversation_id == conversation_id)
            .order_by(Intake.created_at)
        )
    )
    tasks = await session.scalars(
        select(Task).where(Task.conversation_id == conversation_id).order_by(Task.created_at)
    )
    action_runs = await session.scalars(
        select(ActionRun)
        .where(ActionRun.conversation_id == conversation_id)
        .order_by(ActionRun.created_at, ActionRun.id)
    )
    merged = next((intake.data for intake in intakes if intake.version.value == "merged"), {})
    assistant = await session.get(Assistant, conversation.assistant_id)
    threshold = float(assistant.config.get("review_confidence_threshold", 0.7))
    contact_name = (
        await session.scalar(select(Person.display_name).where(Person.id == conversation.person_id))
        if conversation.person_id is not None
        else None
    )
    transfer = _parse_transfer_metrics(conversation.metrics.get("transfer"))
    recording = await session.scalar(
        select(CallRecording).where(CallRecording.conversation_id == conversation_id)
    )
    recording_summary = None
    if recording is not None:
        store = LocalRecordingStore(get_settings().upload_dir / "recordings")
        try:
            store.open(recording.storage_key)
        except (FileNotFoundError, ValueError):
            recording = None
        else:
            recording_summary = CallRecordingSummary(
                duration_seconds=recording.duration_seconds,
                size_bytes=recording.size_bytes,
                created_at=recording.created_at,
                expires_at=recording.expires_at,
                is_test=recording.is_test,
            )
    return ConversationDetailResponse(
        **_conversation_summary(
            conversation,
            contact_name,
            merged_needs_review(merged, threshold),
        ).model_dump(),
        messages=[MessageResponse.model_validate(message) for message in messages],
        intakes=[IntakeResponse(version=intake.version, data=intake.data) for intake in intakes],
        tasks=[TaskSummary.model_validate(task) for task in tasks],
        transfer=transfer,
        metrics=conversation.metrics,
        estimated_cost=conversation.estimated_cost,
        action_runs=[ActionRunSummary.model_validate(run) for run in action_runs],
        recording=recording_summary,
    )


@router.get("/conversations/{conversation_id}/recording")
async def get_conversation_recording(
    conversation_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> FileResponse:
    recording = await session.scalar(
        select(CallRecording).where(CallRecording.conversation_id == conversation_id)
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    store = LocalRecordingStore(get_settings().upload_dir / "recordings")
    try:
        path = store.open(recording.storage_key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Recording not found") from None
    return FileResponse(
        path,
        media_type=recording.content_type,
        headers={"Cache-Control": "private, no-store"},
    )


def _parse_transfer_metrics(value: object) -> CallTransferMetrics | None:
    if not isinstance(value, dict):
        return None
    try:
        return CallTransferMetrics.model_validate(value)
    except ValidationError:
        return None


def _conversation_summary(
    conversation: Conversation,
    contact_name: str | None = None,
    needs_review: bool = False,
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        assistant_id=conversation.assistant_id,
        person_id=conversation.person_id,
        channel=conversation.channel.value,
        external_ref=conversation.external_ref,
        status=conversation.status.value,
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
        summary=conversation.summary,
        end_reason=conversation.end_reason,
        needs_review=needs_review,
        test_call=conversation.metrics.get("test_call") is True,
        contact_name=contact_name,
    )


@router.post(
    "/conversations/{conversation_id}/reviews",
    response_model=ConversationReviewResponse,
    status_code=201,
)
async def create_review(
    conversation_id: UUID,
    payload: ConversationReviewCreate,
    ctx: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> ConversationReviewResponse:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    intake = await session.scalar(
        select(Intake)
        .where(Intake.conversation_id == conversation_id, Intake.version == "merged")
        .with_for_update()
    )
    if intake is None:
        raise HTTPException(status_code=409, detail="Conversation has no merged intake")
    data = deepcopy(intake.data)
    fields = data.get("fields")
    existing = fields.get(payload.field) if isinstance(fields, dict) else None
    assistant = await session.get(Assistant, conversation.assistant_id)
    service_name = data.get("service")
    service = assistant.config.get("services", {}).get(service_name, {})
    configured_fields = {
        item.get("name")
        for item in service.get("fields", [])
        if isinstance(item, dict) and item.get("name")
    }
    if existing is None and payload.field not in configured_fields:
        raise HTTPException(status_code=422, detail="Unknown intake field")
    previous = existing.get("value") if isinstance(existing, dict) else None
    replacement = {
        **(existing if isinstance(existing, dict) else {}),
        "value": payload.resolved_value,
        "status": "human",
        "provenance": "human",
        "confidence": 1.0,
        "needs_review": False,
        "reviewed_by": str(ctx.user.id),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "sources": {
            **(existing.get("sources", {}) if isinstance(existing, dict) else {}),
            "human": {
                "value": payload.resolved_value,
                "user_id": str(ctx.user.id),
                "note": payload.note,
            },
        },
    }
    if previous is not None and previous != payload.resolved_value:
        replacement["previous_value"] = previous
    data.setdefault("fields", {})[payload.field] = replacement
    if isinstance(data.get("needs_review"), list):
        data["needs_review"] = [field for field in data["needs_review"] if field != payload.field]
    intake.data = data
    review = IntakeReview(
        tenant_id=ctx.tenant_id,
        conversation_id=conversation_id,
        field=payload.field,
        resolved_value=payload.resolved_value,
        resolved_by=ctx.user.id,
        note=payload.note,
    )
    session.add(review)
    await session.flush()
    await write_audit(
        session,
        ctx,
        "intake.reviewed",
        "conversation",
        conversation_id,
        {"field": payload.field, "resolved_value": payload.resolved_value},
    )
    return ConversationReviewResponse.model_validate(review)
