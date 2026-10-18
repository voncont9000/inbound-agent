import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from inbound_core.call_report import RouteResponse

from inbound_api.schemas import (
    CallReportAccepted,
    CallReportRequest,
    CallStartRequest,
    ConversationCreatedResponse,
)
from inbound_api.settings import get_settings

router = APIRouter(prefix="/internal/voice", tags=["internal voice"])


async def require_voice_service_token(
    x_service_token: str = Header(alias="X-Service-Token"),
) -> None:
    if not hmac.compare_digest(x_service_token, get_settings().voice_service_token):
        raise HTTPException(status_code=401, detail="Invalid service token")


@router.get(
    "/route", response_model=RouteResponse, dependencies=[Depends(require_voice_service_token)]
)
async def route_call(
    to: str = Query(...),
    from_number: str | None = Query(default=None, alias="from"),
) -> RouteResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post(
    "/calls",
    response_model=ConversationCreatedResponse,
    status_code=201,
    dependencies=[Depends(require_voice_service_token)],
)
async def start_call(payload: CallStartRequest) -> ConversationCreatedResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post(
    "/calls/{conversation_id}/report",
    response_model=CallReportAccepted,
    status_code=202,
    dependencies=[Depends(require_voice_service_token)],
)
async def report_call(conversation_id: UUID, payload: CallReportRequest) -> CallReportAccepted:
    raise HTTPException(status_code=501, detail="Not implemented")
