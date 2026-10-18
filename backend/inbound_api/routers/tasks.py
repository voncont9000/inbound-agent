from uuid import UUID

from fastapi import APIRouter, HTTPException

from inbound_api.schemas import TaskListResponse, TaskSummary, TaskUpdate

router = APIRouter(tags=["tasks"])


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks() -> TaskListResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/tasks/{task_id}", response_model=TaskSummary)
async def update_task(task_id: UUID, payload: TaskUpdate) -> TaskSummary:
    raise HTTPException(status_code=501, detail="Not implemented")
