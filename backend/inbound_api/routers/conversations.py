from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException

from inbound_api.schemas import (
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationReviewCreate,
    ConversationReviewResponse,
)

router = APIRouter(tags=["conversations"])


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    channel: Literal["phone", "web", "gmail", "whatsapp"] | None = None,
    status: Literal["active", "completed", "failed"] | None = None,
    has_review: bool | None = None,
    person_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ConversationListResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(conversation_id: UUID) -> ConversationDetailResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post(
    "/conversations/{conversation_id}/reviews",
    response_model=ConversationReviewResponse,
    status_code=201,
)
async def create_review(
    conversation_id: UUID, payload: ConversationReviewCreate
) -> ConversationReviewResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
