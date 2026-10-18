from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from inbound_core.config import assistant_config_json_schema

from inbound_api.schemas import (
    AssistantCreate,
    AssistantListResponse,
    AssistantResponse,
    AssistantUpdate,
    AssistantVersionsResponse,
    TemplateResponse,
)

router = APIRouter(tags=["assistants"])


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates() -> list[TemplateResponse]:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/assistant-config/schema", response_model=dict[str, Any])
async def assistant_config_schema() -> dict[str, Any]:
    return assistant_config_json_schema()


@router.get("/assistants", response_model=AssistantListResponse)
async def list_assistants() -> AssistantListResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/assistants", response_model=AssistantResponse, status_code=201)
async def create_assistant(payload: AssistantCreate) -> AssistantResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/assistants/{assistant_id}", response_model=AssistantResponse)
async def get_assistant(assistant_id: UUID) -> AssistantResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.put("/assistants/{assistant_id}", response_model=AssistantResponse)
async def update_assistant(assistant_id: UUID, payload: AssistantUpdate) -> AssistantResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/assistants/{assistant_id}/versions", response_model=AssistantVersionsResponse)
async def list_assistant_versions(assistant_id: UUID) -> AssistantVersionsResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
