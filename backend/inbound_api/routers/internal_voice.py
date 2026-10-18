import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from inbound_core.call_report import RouteResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from inbound_api import graph
from inbound_api.db import session_factory, tenant_session
from inbound_api.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
)
from inbound_api.schemas import (
    CallReportAccepted,
    CallReportRequest,
    CallStartRequest,
    ConversationCreatedResponse,
)
from inbound_api.services.assistant_context import load_assistant_route
from inbound_api.services.conversation_results import persist_voice_report
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings
from inbound_api.web_call_tokens import verify as verify_web_call_token

router = APIRouter(prefix="/internal/voice", tags=["internal voice"])
LOGGER = logging.getLogger(__name__)


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
    try:
        external_id = normalise_phone(to)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown number") from None
    async with session_factory() as session:
        resolution = (
            await session.execute(
                text("SELECT * FROM resolve_channel('phone', :external_id)"),
                {"external_id": external_id},
            )
        ).first()
    if resolution is None:
        raise HTTPException(status_code=404, detail="Unknown number")
    tenant_id, assistant_id = resolution
    caller_phone = from_number
    if from_number:
        try:
            caller_phone = normalise_phone(from_number, strict=False)
        except ValueError:
            pass
    return await load_assistant_route(
        tenant_id,
        assistant_id,
        caller_phone=caller_phone,
        include_context_card=True,
    )


@router.get(
    "/web-route",
    response_model=RouteResponse,
    dependencies=[Depends(require_voice_service_token)],
)
async def route_web_call(token: str = Query(...)) -> RouteResponse:
    try:
        tenant_id, assistant_id = verify_web_call_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid web call token") from None
    return await load_assistant_route(
        tenant_id,
        assistant_id,
        caller_phone=None,
        include_context_card=False,
    )


@router.post(
    "/calls",
    response_model=ConversationCreatedResponse,
    status_code=201,
    dependencies=[Depends(require_voice_service_token)],
)
async def start_call(payload: CallStartRequest) -> ConversationCreatedResponse:
    if payload.channel == "web" and payload.web_call_token is not None:
        try:
            tenant_id, assistant_id = verify_web_call_token(payload.web_call_token)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid web call token") from None
        resolution = (tenant_id, assistant_id)
    else:
        if payload.channel == "phone":
            try:
                external_id = normalise_phone(payload.to_number)
            except ValueError:
                raise HTTPException(status_code=404, detail="Unknown number") from None
            kind = "phone"
        else:
            external_id = payload.to_number.strip()
            kind = "web"
        async with session_factory() as session:
            resolution = (
                await session.execute(
                    text("SELECT * FROM resolve_channel(:kind, :external_id)"),
                    {"kind": kind, "external_id": external_id},
                )
            ).first()
    if resolution is None:
        raise HTTPException(status_code=404, detail="Unknown channel")
    tenant_id, assistant_id = resolution
    async with tenant_session(tenant_id) as session:
        existing = await session.scalar(
            select(Conversation).where(
                Conversation.channel == payload.channel,
                Conversation.external_ref == payload.external_call_id,
            )
        )
        if existing is not None:
            return ConversationCreatedResponse(conversation_id=existing.id)
        person_id = None
        caller_phone = payload.from_number
        if caller_phone:
            try:
                caller_phone = normalise_phone(caller_phone, strict=False)
            except ValueError:
                pass
        try:
            async with session.begin_nested():
                person_id = await graph.resolve_person(
                    session,
                    phone=caller_phone,
                    email=None,
                    source=payload.channel,
                )
        except NotImplementedError:
            person_id = None
        except Exception:
            LOGGER.warning("Could not resolve caller person", exc_info=True)
            person_id = None
        conversation = Conversation(
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            person_id=person_id,
            channel=ConversationChannel(payload.channel),
            external_ref=payload.external_call_id,
            status=ConversationStatus.ACTIVE,
            started_at=payload.started_at,
        )
        metrics = {}
        if caller_phone:
            metrics["from_number"] = caller_phone
        if payload.channel == "web" and payload.web_call_token is not None:
            metrics["test_call"] = True
        if metrics:
            conversation.metrics = metrics
        try:
            async with session.begin_nested():
                session.add(conversation)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(Conversation).where(
                    Conversation.channel == payload.channel,
                    Conversation.external_ref == payload.external_call_id,
                )
            )
            if existing is not None:
                return ConversationCreatedResponse(conversation_id=existing.id)
            raise
        return ConversationCreatedResponse(conversation_id=conversation.id)


@router.post(
    "/calls/{conversation_id}/report",
    response_model=CallReportAccepted,
    status_code=202,
    dependencies=[Depends(require_voice_service_token)],
)
async def report_call(conversation_id: UUID, payload: CallReportRequest) -> CallReportAccepted:
    async with session_factory() as session:
        tenant_id = await session.scalar(
            text("SELECT conversation_tenant(:conversation_id)"),
            {"conversation_id": conversation_id},
        )
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    async with tenant_session(tenant_id) as session:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.external_ref != payload.external_call_id:
            raise HTTPException(status_code=409, detail="External call ID does not match")
        if conversation.status == ConversationStatus.COMPLETED:
            return CallReportAccepted(accepted=True)
        await persist_voice_report(session, tenant_id, conversation, payload)
    return CallReportAccepted(accepted=True)
