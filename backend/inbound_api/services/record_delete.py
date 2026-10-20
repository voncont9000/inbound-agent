from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext
from inbound_api.models import Edge, IdentityMergeLog, Person, Task
from inbound_api.services.audit import write_audit


async def _delete_edges(session: AsyncSession, node_type: str, ids: list[UUID]) -> None:
    await session.execute(
        delete(Edge).where(
            or_(
                and_(Edge.src_type == node_type, Edge.src_id.in_(ids)),
                and_(Edge.dst_type == node_type, Edge.dst_id.in_(ids)),
            )
        )
    )


async def delete_persons(
    session: AsyncSession, ctx: TenantContext, person_ids: list[UUID]
) -> list[UUID]:
    """Delete contacts. Conversations, tasks and bookings keep running with no contact."""
    requested = list(dict.fromkeys(person_ids))
    if not requested:
        return []
    persons = list(
        await session.scalars(select(Person).where(Person.id.in_(requested)).with_for_update())
    )
    ids = [person.id for person in persons]
    if not ids:
        return []
    await session.execute(
        update(Person).where(Person.merged_into_id.in_(ids)).values(merged_into_id=None)
    )
    await session.execute(
        delete(IdentityMergeLog).where(
            or_(IdentityMergeLog.survivor_id.in_(ids), IdentityMergeLog.merged_id.in_(ids))
        )
    )
    await _delete_edges(session, "person", ids)
    for person in persons:
        await write_audit(session, ctx, "contact.deleted", "person", person.id)
        await session.delete(person)
    await session.flush()
    return ids


async def delete_tasks(
    session: AsyncSession, ctx: TenantContext, task_ids: list[UUID]
) -> list[UUID]:
    requested = list(dict.fromkeys(task_ids))
    if not requested:
        return []
    tasks = list(
        await session.scalars(select(Task).where(Task.id.in_(requested)).with_for_update())
    )
    ids = [task.id for task in tasks]
    if not ids:
        return []
    await _delete_edges(session, "task", ids)
    for task in tasks:
        await write_audit(session, ctx, "task.deleted", "task", task.id)
        await session.delete(task)
    await session.flush()
    return ids
