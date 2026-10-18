from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.models import Conversation, MembershipRole, Task
from inbound_api.schemas import TaskListResponse, TaskSummary, TaskUpdate
from inbound_api.services.pagination import page_query

router = APIRouter(tags=["tasks"])
_staff = Depends(require_role(MembershipRole.STAFF))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = None,
    kind: str | None = None,
    assistant_id: UUID | None = None,
    conversation_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> TaskListResponse:
    statement = select(Task)
    if status is not None:
        if status not in {"open", "done", "dismissed"}:
            raise HTTPException(status_code=422, detail="Invalid task status")
        statement = statement.where(Task.status == status)
    if kind is not None:
        statement = statement.where(Task.kind == kind)
    if assistant_id is not None:
        statement = statement.join(
            Conversation,
            Conversation.id == Task.conversation_id,
        ).where(Conversation.assistant_id == assistant_id)
    if conversation_id is not None:
        statement = statement.where(Task.conversation_id == conversation_id)
    rows, next_cursor = await page_query(
        session,
        statement,
        created_at_column=Task.created_at,
        id_column=Task.id,
        cursor=cursor,
        limit=limit,
    )
    return TaskListResponse(
        items=[TaskSummary.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.patch("/tasks/{task_id}", response_model=TaskSummary)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> TaskSummary:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = payload.status
    return TaskSummary.model_validate(task)
