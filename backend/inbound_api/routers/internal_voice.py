import hmac
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from inbound_core.call_report import RouteResponse
from inbound_core.config import AssistantConfig
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from inbound_api import graph
from inbound_api.db import session_factory, tenant_session
from inbound_api.jobs import enqueue
from inbound_api.models import (
    Assistant,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Intake,
    IntakeVersion,
    KnowledgeDigest,
    KnowledgeGap,
    Message,
    MessageRole,
    Task,
    TaskSource,
    TaskStatus,
)
from inbound_api.schemas import (
    CallReportAccepted,
    CallReportRequest,
    CallStartRequest,
    ConversationCreatedResponse,
)
from inbound_api.services.phone import normalise_phone
from inbound_api.settings import get_settings

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
    async with tenant_session(tenant_id) as session:
        assistant = await session.get(Assistant, assistant_id)
        if assistant is None:
            raise HTTPException(status_code=404, detail="Unknown number")
        config = AssistantConfig.model_validate(assistant.config)
        digest = await session.scalar(
            select(KnowledgeDigest.content)
            .where(KnowledgeDigest.assistant_id == assistant_id)
            .order_by(KnowledgeDigest.created_at.desc(), KnowledgeDigest.id.desc())
            .limit(1)
        )
        caller_phone = from_number
        if from_number:
            try:
                caller_phone = normalise_phone(from_number, strict=False)
            except ValueError:
                pass
        context_card = None
        try:
            async with session.begin_nested():
                person = await graph.resolve_person(
                    session, phone=caller_phone, email=None, source="phone"
                )
                if person:
                    context_card = await graph.build_context_card(session, person)
        except NotImplementedError:
            context_card = None
        except Exception:
            LOGGER.warning("Could not build caller context card", exc_info=True)
            context_card = None
    return RouteResponse(
        tenant_id=tenant_id,
        assistant_id=assistant_id,
        config=config,
        digest=digest,
        context_card=context_card,
    )


@router.post(
    "/calls",
    response_model=ConversationCreatedResponse,
    status_code=201,
    dependencies=[Depends(require_voice_service_token)],
)
async def start_call(payload: CallStartRequest) -> ConversationCreatedResponse:
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
        if caller_phone:
            conversation.metrics = {"from_number": caller_phone}
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
        for turn in payload.transcript:
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role=MessageRole(turn.role),
                    text=turn.text,
                    ts=turn.timestamp,
                    meta={},
                )
            )
        intakes = [
            (IntakeVersion.LIVE, payload.live_intake),
            (IntakeVersion.MERGED, payload.merged_intake),
        ]
        if payload.post_call_intake is not None:
            intakes.append((IntakeVersion.POST_CALL, payload.post_call_intake))
        for version, data in intakes:
            session.add(
                Intake(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    version=version,
                    data=data,
                )
            )
        for item in payload.tasks:
            if item.get("type") in {"knowledge_gap", "end_call"} or item.get("kind") in {
                "knowledge_gap",
                "end_call",
            }:
                continue
            kind = item.get("type") or item.get("kind") or "task"
            source = item.get("source")
            if source not in {"live", "post_call"}:
                source = "live"
            task_payload = {
                key: value for key, value in item.items() if key not in {"type", "kind", "source"}
            }
            due_at = item.get("due_at")
            if isinstance(due_at, str):
                try:
                    due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                except ValueError:
                    due_at = None
            if not isinstance(due_at, datetime) or due_at.tzinfo is None:
                due_at = None
            session.add(
                Task(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    due_at=due_at,
                    kind=kind,
                    status=TaskStatus.OPEN,
                    payload=task_payload,
                    source=TaskSource(source),
                )
            )
        gaps: dict[str, str] = {}
        for item in payload.knowledge_gaps:
            question, evidence = item.get("question"), item.get("evidence")
            if isinstance(question, str) and isinstance(evidence, str):
                gaps.setdefault(question, evidence)
        for item in payload.tasks:
            if item.get("type") == "knowledge_gap" or item.get("kind") == "knowledge_gap":
                question, evidence = item.get("question"), item.get("evidence")
                if isinstance(question, str) and isinstance(evidence, str):
                    gaps.setdefault(question, evidence)
        for question, evidence in gaps.items():
            session.add(
                KnowledgeGap(
                    tenant_id=tenant_id,
                    assistant_id=conversation.assistant_id,
                    conversation_id=conversation_id,
                    question=question,
                    evidence=evidence,
                )
            )
        conversation.status = ConversationStatus.COMPLETED
        conversation.ended_at = payload.ended_at
        conversation.end_reason = payload.end_reason
        conversation.summary = payload.summary
        conversation.metrics = {**(conversation.metrics or {}), **payload.metrics}
        conversation.estimated_cost = payload.estimated_cost
        await enqueue(session, "graph_ingest", {"conversation_id": str(conversation_id)})
    return CallReportAccepted(accepted=True)
