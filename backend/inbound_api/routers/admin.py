import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text

from inbound_api.auth import require_platform_admin
from inbound_api.db import session_factory, tenant_session
from inbound_api.models import (
    Assistant,
    Conversation,
    ConversationScore,
    EvalCase,
    EvalRun,
    Intake,
    IntakeVersion,
    Message,
    MessageRole,
    Tenant,
)
from inbound_api.schemas import (
    AdminConversationDetail,
    AdminConversationItem,
    AdminConversationListResponse,
    AdminDaySummary,
    AdminOverview,
    AdminTenantSummary,
    ConversationScoreResponse,
    EvalCaseResponse,
    EvalRunResponse,
    EvalRunSummary,
    MessageResponse,
)
from inbound_api.services.conversation_results import enqueue_conversation_score
from inbound_api.services.pagination import decode_cursor, encode_cursor
from inbound_api.settings import get_settings

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)


def _mean(values: list[int | float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


@router.get("/overview", response_model=AdminOverview)
async def overview(days: int = Query(default=7, ge=1, le=90)) -> AdminOverview:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text("SELECT * FROM admin_conversation_facts(:since)"),
                    {"since": since},
                )
            )
            .mappings()
            .all()
        )

    overall_values = [int(row["overall"]) for row in rows if row["overall"] is not None]
    p50_values = [float(row["response_p50_s"]) for row in rows if row["response_p50_s"] is not None]
    p95_values = [float(row["response_p95_s"]) for row in rows if row["response_p95_s"] is not None]

    daily: dict[str, list[Any]] = defaultdict(list)
    tenants: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        started_at = row["started_at"]
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        daily[started_at.astimezone(UTC).date().isoformat()].append(row)
        tenants[row["tenant_id"]].append(row)

    by_day = []
    day = since.date()
    while day <= now.date():
        day_rows = daily.get(day.isoformat(), [])
        day_scores = [int(row["overall"]) for row in day_rows if row["overall"] is not None]
        by_day.append(
            AdminDaySummary(
                date=day.isoformat(),
                conversations=len(day_rows),
                avg_overall=_mean(day_scores),
                needs_review=sum(row["needs_review"] is True for row in day_rows),
            )
        )
        day += timedelta(days=1)

    by_tenant = []
    for tenant_id, tenant_rows in tenants.items():
        scores = [int(row["overall"]) for row in tenant_rows if row["overall"] is not None]
        by_tenant.append(
            AdminTenantSummary(
                tenant_id=tenant_id,
                tenant_name=tenant_rows[0]["tenant_name"],
                conversations=len(tenant_rows),
                avg_overall=_mean(scores),
                needs_review=sum(row["needs_review"] is True for row in tenant_rows),
            )
        )
    by_tenant.sort(key=lambda item: (-item.conversations, item.tenant_name))

    return AdminOverview(
        days=days,
        conversations=len(rows),
        scored=sum(row["overall"] is not None for row in rows),
        needs_review=sum(row["needs_review"] is True for row in rows),
        avg_overall=_mean(overall_values),
        response_p50_s=round(median(p50_values), 2) if p50_values else None,
        response_p95_s=round(median(p95_values), 2) if p95_values else None,
        by_day=by_day,
        by_tenant=by_tenant,
    )


@router.get("/conversations", response_model=AdminConversationListResponse)
async def list_conversations(
    needs_review: bool | None = None,
    tenant_id: UUID | None = None,
    assistant_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> AdminConversationListResponse:
    cursor_started: datetime | None = None
    cursor_id: UUID | None = None
    if cursor:
        cursor_started, cursor_id = decode_cursor(cursor)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM admin_conversation_list("
                        ":needs_review, :tenant_id, :assistant_id, "
                        ":cursor_started, :cursor_id, :limit)"
                    ),
                    {
                        "needs_review": needs_review,
                        "tenant_id": tenant_id,
                        "assistant_id": assistant_id,
                        "cursor_started": cursor_started,
                        "cursor_id": cursor_id,
                        "limit": limit + 1,
                    },
                )
            )
            .mappings()
            .all()
        )
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        AdminConversationItem(
            conversation_id=row["conversation_id"],
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            assistant_id=row["assistant_id"],
            assistant_name=row["assistant_name"],
            channel=row["channel"],
            started_at=row["started_at"],
            is_test=row["is_test"],
            overall=row["overall"],
            needs_review=row["needs_review"],
            issues=row["issues"] if isinstance(row["issues"], list) else [],
            objective=row["objective"],
        )
        for row in page
    ]
    next_cursor = (
        encode_cursor(page[-1]["started_at"], page[-1]["conversation_id"])
        if has_more and page
        else None
    )
    return AdminConversationListResponse(items=items, next_cursor=next_cursor)


async def _conversation_tenant_id(conversation_id: UUID) -> UUID:
    async with session_factory() as session:
        tenant_id = await session.scalar(
            text("SELECT conversation_tenant(:conversation_id)"),
            {"conversation_id": conversation_id},
        )
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return tenant_id


async def _load_conversation_detail(conversation_id: UUID) -> tuple[Any, ...]:
    tenant_id = await _conversation_tenant_id(conversation_id)
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Not Found")
        assistant = await session.get(Assistant, conversation.assistant_id)
        tenant = await session.get(Tenant, tenant_id)
        messages = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.ts, Message.id)
            )
        )
        intake = await session.scalar(
            select(Intake).where(
                Intake.conversation_id == conversation_id,
                Intake.version == IntakeVersion.MERGED,
            )
        )
        score = await session.scalar(
            select(ConversationScore).where(ConversationScore.conversation_id == conversation_id)
        )
        return conversation, assistant, tenant, messages, intake, score


@router.get("/conversations/{conversation_id}", response_model=AdminConversationDetail)
async def conversation_detail(conversation_id: UUID) -> AdminConversationDetail:
    conversation, assistant, tenant, messages, intake, score = await _load_conversation_detail(
        conversation_id
    )
    metrics = conversation.metrics or {}
    trace_id = metrics.get("trace_id")
    host = get_settings().langfuse_host
    trace_url = f"{host.rstrip('/')}/trace/{trace_id}" if host and trace_id else None
    score_response = (
        ConversationScoreResponse(
            judge_model=score.judge_model,
            judge_version=score.judge_version,
            overall=score.overall,
            needs_review=score.needs_review,
            checks=score.checks,
            issues=score.issues,
            objective=score.objective,
            created_at=score.created_at,
            updated_at=score.updated_at,
        )
        if score is not None
        else None
    )
    return AdminConversationDetail(
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id,
        tenant_name=tenant.name if tenant else "",
        assistant_id=conversation.assistant_id,
        assistant_name=assistant.name if assistant else "",
        channel=conversation.channel.value,
        status=conversation.status.value,
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
        summary=conversation.summary,
        end_reason=conversation.end_reason,
        is_test=bool(metrics.get("test_call", False)),
        messages=[MessageResponse.model_validate(message) for message in messages],
        intake=intake.data if intake is not None else None,
        score=score_response,
        trace_url=trace_url,
    )


@router.post("/conversations/{conversation_id}/rescore", status_code=status.HTTP_202_ACCEPTED)
async def rescore(conversation_id: UUID) -> dict[str, bool]:
    tenant_id = await _conversation_tenant_id(conversation_id)
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Not Found")
        await enqueue_conversation_score(session, conversation_id)
    return {"queued": True}


@router.post(
    "/conversations/{conversation_id}/eval-case",
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_case(conversation_id: UUID) -> dict[str, UUID]:
    conversation, assistant, _, messages, intake, _ = await _load_conversation_detail(
        conversation_id
    )
    if assistant is None:
        raise HTTPException(status_code=404, detail="Not Found")
    utterances = [
        message.text
        for message in messages
        if message.role == MessageRole.CALLER and message.text.strip()
    ]
    merged = intake.data if intake is not None and isinstance(intake.data, dict) else {}
    fields = merged.get("fields", {})
    intake_checks = {}
    if isinstance(fields, dict):
        for name, field_value in fields.items():
            value = field_value.get("value") if isinstance(field_value, dict) else field_value
            if value is not None and value != "":
                intake_checks[name] = "(?i)^" + re.escape(str(value)) + "$"
    checks = {
        "transcript_checks": {},
        "assistant_checks": {},
        "intake_checks": intake_checks,
    }
    eval_case = EvalCase(
        source_conversation_id=conversation.id,
        name=f"{assistant.name} – {conversation.started_at:%Y-%m-%d}",
        assistant_id=assistant.id,
        caller_utterances=utterances,
        checks=checks,
        enabled=True,
    )
    async with session_factory() as session, session.begin():
        session.add(eval_case)
        await session.flush()
        eval_case_id = eval_case.id
    return {"eval_case_id": eval_case_id}


@router.get("/eval-cases", response_model=list[EvalCaseResponse])
async def list_eval_cases() -> list[EvalCaseResponse]:
    async with session_factory() as session:
        cases = list(await session.scalars(select(EvalCase).order_by(EvalCase.created_at.desc())))
    return [EvalCaseResponse.model_validate(item, from_attributes=True) for item in cases]


@router.get("/eval-runs", response_model=list[EvalRunSummary])
async def list_eval_runs(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[EvalRunSummary]:
    async with session_factory() as session:
        runs = list(
            await session.scalars(
                select(EvalRun).order_by(EvalRun.started_at.desc(), EvalRun.id.desc()).limit(limit)
            )
        )
    return [
        EvalRunSummary(
            id=run.id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            git_sha=run.git_sha,
            trigger=run.trigger,
            summary=run.summary,
        )
        for run in runs
    ]


@router.get("/eval-runs/{eval_run_id}", response_model=EvalRunResponse)
async def get_eval_run(eval_run_id: UUID) -> EvalRunResponse:
    async with session_factory() as session:
        run = await session.get(EvalRun, eval_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return EvalRunResponse(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        git_sha=run.git_sha,
        trigger=run.trigger,
        summary=run.summary,
        results=run.results,
    )
