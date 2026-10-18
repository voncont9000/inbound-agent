from typing import Literal

from fastapi import APIRouter, Depends

from inbound_api.auth import TenantContext, require_role
from inbound_api.models import MembershipRole
from inbound_api.settings import get_settings

router = APIRouter(tags=["calendar"])
_owner = Depends(require_role(MembershipRole.OWNER))


@router.get("/calendar/mode")
async def calendar_mode(
    _: TenantContext = _owner,
) -> dict[str, Literal["off", "fake", "google"]]:
    return {"mode": get_settings().calendar_mode}
