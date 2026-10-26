from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text

from inbound_api.db import session_factory
from inbound_api.models import EvalCase, EvalRun
from inbound_api.routers.internal_voice import require_voice_service_token
from inbound_api.schemas import (
    EvalCaseChecks,
    EvalRunCreate,
    EvalRunCreated,
    InternalEvalCase,
    InternalEvalCaseList,
)
from inbound_api.services.assistant_context import load_assistant_route

router = APIRouter(
    prefix="/internal",
    tags=["internal evals"],
    dependencies=[Depends(require_voice_service_token)],
)


@router.get("/eval-cases", response_model=InternalEvalCaseList)
async def list_eval_cases() -> InternalEvalCaseList:
    async with session_factory() as session:
        cases = (
            await session.scalars(
                select(EvalCase)
                .where(EvalCase.enabled.is_(True))
                .order_by(EvalCase.created_at, EvalCase.id)
            )
        ).all()

    items = []
    for case in cases:
        route = None
        tenant_id = None
        if case.source_conversation_id is not None:
            async with session_factory() as session:
                tenant_id = await session.scalar(
                    text("SELECT conversation_tenant(:conversation_id)"),
                    {"conversation_id": case.source_conversation_id},
                )
        if tenant_id is not None and case.assistant_id is not None:
            try:
                route = await load_assistant_route(
                    tenant_id,
                    case.assistant_id,
                    caller_phone=None,
                    include_context_card=False,
                )
            except HTTPException as error:
                if error.status_code != 404:
                    raise
        items.append(
            InternalEvalCase(
                id=case.id,
                name=case.name,
                assistant_id=case.assistant_id,
                source_conversation_id=case.source_conversation_id,
                caller_utterances=case.caller_utterances,
                checks=EvalCaseChecks.model_validate(case.checks or {}),
                route=route,
            )
        )
    return InternalEvalCaseList(items=items)


@router.post(
    "/eval-runs",
    response_model=EvalRunCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_run(body: EvalRunCreate) -> EvalRunCreated:
    run = EvalRun(
        started_at=body.started_at,
        finished_at=body.finished_at,
        git_sha=body.git_sha,
        trigger=body.trigger,
        summary=body.summary.model_dump(),
        results=[result.model_dump(mode="json") for result in body.results],
    )
    async with session_factory() as session, session.begin():
        session.add(run)
        await session.flush()
        eval_run_id = run.id
    return EvalRunCreated(eval_run_id=eval_run_id)
