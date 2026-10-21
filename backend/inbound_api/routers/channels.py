from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.models import ChannelConnection, MembershipRole
from inbound_api.schemas import (
    ChannelConnectionResponse,
    ChannelConnectionsResponse,
    ChannelConnectionUpdate,
    WorkspaceChannelCreate,
)
from inbound_api.services.channel_connections import (
    create_channel_connection,
    delete_channel_connection,
    update_channel_connection,
)

router = APIRouter(tags=["channels"])
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/channels", response_model=ChannelConnectionsResponse)
async def list_channels(
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ChannelConnectionsResponse:
    rows = await session.scalars(
        select(ChannelConnection).order_by(ChannelConnection.created_at.desc())
    )
    return ChannelConnectionsResponse(
        items=[ChannelConnectionResponse.model_validate(row) for row in rows]
    )


@router.post("/channels", response_model=ChannelConnectionResponse, status_code=201)
async def create_channel(
    payload: WorkspaceChannelCreate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ChannelConnectionResponse:
    connection = await create_channel_connection(session, ctx, payload.assistant_id, payload)
    return ChannelConnectionResponse.model_validate(connection)


@router.patch("/channels/{channel_id}", response_model=ChannelConnectionResponse)
async def update_channel(
    channel_id: UUID,
    payload: ChannelConnectionUpdate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> ChannelConnectionResponse:
    connection = await session.scalar(
        select(ChannelConnection).where(ChannelConnection.id == channel_id)
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Channel connection not found")
    await update_channel_connection(
        session, ctx, connection, payload, allow_assistant_reassignment=True
    )
    return ChannelConnectionResponse.model_validate(connection)


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: UUID,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> None:
    connection = await session.scalar(
        select(ChannelConnection).where(ChannelConnection.id == channel_id)
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Channel connection not found")
    await delete_channel_connection(session, ctx, connection)
