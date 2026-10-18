from uuid import UUID

from fastapi import APIRouter, HTTPException

from inbound_api.schemas import (
    ContactDetailResponse,
    ContactMergeRequest,
    ContactMergeResponse,
    ContactSplitRequest,
    ContactSplitResponse,
    ContactsResponse,
)

router = APIRouter(tags=["contacts"])


@router.get("/contacts", response_model=ContactsResponse)
async def list_contacts(cursor: str | None = None, limit: int = 50) -> ContactsResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/contacts/{contact_id}", response_model=ContactDetailResponse)
async def get_contact(contact_id: UUID) -> ContactDetailResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/contacts/{contact_id}/merge", response_model=ContactMergeResponse)
async def merge_contact(contact_id: UUID, payload: ContactMergeRequest) -> ContactMergeResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/contacts/{contact_id}/split", response_model=ContactSplitResponse)
async def split_contact(contact_id: UUID, payload: ContactSplitRequest) -> ContactSplitResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
