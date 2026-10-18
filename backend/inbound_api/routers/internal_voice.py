import hmac
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from inbound_core.booking import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookRequest,
    BookResponse,
)
from inbound_core.call_report import RouteResponse
from inbound_core.config import AssistantConfig
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from inbound_api import graph
from inbound_api.db import session_factory, tenant_session
from inbound_api.models import (
    Assistant,
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
from inbound_api.services.booking import book_slot, booking_context, fetch_busy
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


@router.post(
    "/availability",
    response_model=AvailabilityResponse,
    dependencies=[Depends(require_voice_service_token)],
)
async def availability(payload: AvailabilityRequest) -> AvailabilityResponse:
    try:
        if payload.channel == "web":
            if not payload.web_call_token:
                raise HTTPException(status_code=401, detail="Invalid web call token")
            route = await route_web_call(payload.web_call_token)
        else:
            if not payload.to_number:
                raise HTTPException(status_code=404, detail="Unknown number")
            route = await route_call(payload.to_number, None)
    except HTTPException:
        raise
    async with tenant_session(route.tenant_id) as session:
        context = await booking_context(session, route.assistant_id, route.config)
        if context is None:
            return AvailabilityResponse(available=False, reason="booking_disabled")
        try:
            window, busy, provider_ms = await fetch_busy(context, datetime.now(UTC))
        except Exception:
            LOGGER.warning("Calendar availability lookup failed", exc_info=True)
            return AvailabilityResponse(available=False, reason="calendar_error")
        return AvailabilityResponse(
            available=True,
            timezone=context.timezone,
            window_start=window.start,
            window_end=window.end,
            busy=busy,
            fetched_at=datetime.now(UTC),
            provider_ms=provider_ms,
        )


@router.post(
    "/book",
    response_model=BookResponse,
    dependencies=[Depends(require_voice_service_token)],
)
async def book(payload: BookRequest) -> BookResponse:
    async with session_factory() as session:
        tenant_id = await session.scalar(
            text("SELECT conversation_tenant(:conversation_id)"),
            {"conversation_id": payload.conversation_id},
        )
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    async with tenant_session(tenant_id) as session:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == payload.conversation_id).with_for_update()
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.external_ref != payload.external_call_id:
            raise HTTPException(status_code=409, detail="External call ID does not match")
        assistant = await session.get(Assistant, conversation.assistant_id)
        if assistant is None:
            return BookResponse(status="invalid", reason="booking_disabled")
        config = AssistantConfig.model_validate(assistant.config)
        context = await booking_context(session, assistant.id, config)
        if context is None:
            return BookResponse(status="invalid", reason="booking_disabled")
        return await book_slot(
            session,
            tenant_id,
            conversation,
            context,
            slot_id=payload.slot_id,
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
        )
