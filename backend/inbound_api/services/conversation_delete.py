from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext
from inbound_api.models import CallRecording, Conversation, ConversationStatus, Edge
from inbound_api.services.audit import write_audit
from inbound_api.services.recordings import LocalRecordingStore
from inbound_api.settings import get_settings


@dataclass(frozen=True)
class DeleteResult:
    deleted_ids: list[UUID]
    skipped_in_progress_ids: list[UUID]


async def delete_conversations(
    session: AsyncSession, ctx: TenantContext, conversation_ids: list[UUID]
) -> DeleteResult:
    requested_ids = list(dict.fromkeys(conversation_ids))
    if not requested_ids:
        return DeleteResult(deleted_ids=[], skipped_in_progress_ids=[])

    conversations = list(
        await session.scalars(
            select(Conversation)
            .where(Conversation.id.in_(requested_ids))
            .order_by(Conversation.created_at, Conversation.id)
            .with_for_update()
        )
    )
    deletable = [
        conversation
        for conversation in conversations
        if conversation.status != ConversationStatus.ACTIVE
    ]
    skipped_ids = [
        conversation.id
        for conversation in conversations
        if conversation.status == ConversationStatus.ACTIVE
    ]
    deleted_ids = [conversation.id for conversation in deletable]
    if not deleted_ids:
        return DeleteResult(deleted_ids=[], skipped_in_progress_ids=skipped_ids)

    storage_keys = list(
        await session.scalars(
            select(CallRecording.storage_key).where(CallRecording.conversation_id.in_(deleted_ids))
        )
    )
    await session.execute(
        delete(Edge).where(
            or_(
                and_(
                    Edge.src_type == "conversation",
                    Edge.src_id.in_(deleted_ids),
                ),
                and_(
                    Edge.dst_type == "conversation",
                    Edge.dst_id.in_(deleted_ids),
                ),
            )
        )
    )
    for conversation in deletable:
        await write_audit(
            session,
            ctx,
            "conversation.deleted",
            "conversation",
            conversation.id,
        )
        await session.delete(conversation)

    await session.flush()
    store = LocalRecordingStore(get_settings().upload_dir / "recordings")
    for storage_key in storage_keys:
        store.delete(storage_key)

    return DeleteResult(
        deleted_ids=deleted_ids,
        skipped_in_progress_ids=skipped_ids,
    )
