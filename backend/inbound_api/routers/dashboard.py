from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.models import MembershipRole
from inbound_api.services.dashboard import (
    DashboardRangeError,
    DashboardSummary,
    RangePreset,
    dashboard_summary,
    resolve_range,
)

router = APIRouter(tags=["dashboard"])
_staff = Depends(require_role(MembershipRole.STAFF))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    range: RangePreset = "7d",
    start: date | None = None,
    end: date | None = None,
    timezone: str = Query("UTC", max_length=64),
    assistant_id: UUID | None = None,
    include_test: bool = False,
    ctx: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> DashboardSummary:
    try:
        window = resolve_range(range, timezone, start_date=start, end_date=end)
    except DashboardRangeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return await dashboard_summary(
        session,
        ctx.tenant_id,
        window,
        assistant_id=assistant_id,
        include_test=include_test,
    )
