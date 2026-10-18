from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from inbound_api.schemas import (
    JobResponse,
    KnowledgeDigestResponse,
    KnowledgeDigestUpdate,
    KnowledgeGapResponse,
    KnowledgeGapsResponse,
    KnowledgeGapUpdate,
    KnowledgeSourceCreate,
    KnowledgeSourceResponse,
    KnowledgeSourcesResponse,
)

router = APIRouter(tags=["knowledge"])
_SOURCE_BODY = Body(...)


@router.get("/assistants/{assistant_id}/knowledge/sources", response_model=KnowledgeSourcesResponse)
async def list_sources(assistant_id: UUID) -> KnowledgeSourcesResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post(
    "/assistants/{assistant_id}/knowledge/sources",
    response_model=KnowledgeSourceResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/KnowledgeSourceCreate"}
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "required": ["file"],
                    }
                },
            },
        }
    },
)
async def create_source(
    assistant_id: UUID,
    source: KnowledgeSourceCreate = _SOURCE_BODY,
) -> KnowledgeSourceResponse:
    """Accept a URL JSON body or a document multipart upload."""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post(
    "/assistants/{assistant_id}/knowledge/import", response_model=JobResponse, status_code=202
)
async def import_knowledge(assistant_id: UUID) -> JobResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/assistants/{assistant_id}/knowledge/digest", response_model=KnowledgeDigestResponse)
async def get_digest(assistant_id: UUID) -> KnowledgeDigestResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.put("/assistants/{assistant_id}/knowledge/digest", response_model=KnowledgeDigestResponse)
async def update_digest(
    assistant_id: UUID, payload: KnowledgeDigestUpdate
) -> KnowledgeDigestResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/knowledge-gaps", response_model=KnowledgeGapsResponse)
async def list_knowledge_gaps() -> KnowledgeGapsResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/knowledge-gaps/{gap_id}", response_model=KnowledgeGapResponse)
async def update_knowledge_gap(gap_id: UUID, payload: KnowledgeGapUpdate) -> KnowledgeGapResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID) -> JobResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
