from fastapi import APIRouter, Depends, status

from inbound_api.db import session_factory
from inbound_api.models import EvalRun
from inbound_api.routers.internal_voice import require_voice_service_token
from inbound_api.schemas import EvalRunCreate, EvalRunCreated

router = APIRouter(
    prefix="/internal",
    tags=["internal evals"],
    dependencies=[Depends(require_voice_service_token)],
)


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
