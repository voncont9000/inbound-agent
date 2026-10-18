from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import enqueue
from inbound_api.models import Job, JobStatus


async def enqueue_gmail_sync(
    session: AsyncSession,
    connection_id: UUID,
    *,
    run_after: datetime | None = None,
    received_at: str | None = None,
) -> None:
    existing = await session.scalar(
        select(Job)
        .where(
            Job.kind == "gmail_sync",
            Job.status == JobStatus.QUEUED,
            Job.payload["connection_id"].as_string() == str(connection_id),
        )
        .limit(1)
        .with_for_update()
    )
    if existing is None:
        await enqueue(
            session,
            "gmail_sync",
            {
                "connection_id": str(connection_id),
                **({"received_at": received_at} if received_at else {}),
            },
            run_after=run_after,
        )
        return
    immediate_or_requested = run_after or datetime.now(UTC)
    if existing.run_after is None or existing.run_after > immediate_or_requested:
        existing.run_after = immediate_or_requested
    if received_at:
        existing.payload = {**existing.payload, "received_at": received_at}
