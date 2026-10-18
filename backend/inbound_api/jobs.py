import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.db import session_factory, tenant_session
from inbound_api.settings import get_settings

LOGGER = logging.getLogger(__name__)
JobHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, JobHandler] = {}


class PermanentJobError(Exception):
    pass


def register(kind: str):
    def decorator(handler: JobHandler) -> JobHandler:
        HANDLERS[kind] = handler
        return handler

    return decorator


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    run_after=None,
) -> UUID:
    """Add a tenant-scoped durable job to the Postgres-backed queue."""
    tenant_id = await session.scalar(
        text("SELECT nullif(current_setting('app.tenant_id', true), '')::uuid")
    )
    if tenant_id is None:
        raise RuntimeError("Tenant context is required to enqueue a job")
    result = await session.execute(
        text(
            "INSERT INTO jobs (tenant_id, kind, payload, run_after) "
            "VALUES (:tenant_id, :kind, CAST(:payload AS jsonb), coalesce(:run_after, now())) "
            "RETURNING id"
        ),
        {
            "tenant_id": tenant_id,
            "kind": kind,
            "payload": json.dumps(payload),
            "run_after": run_after,
        },
    )
    return result.scalar_one()


async def run_next_job() -> bool:
    settings = get_settings()
    async with session_factory() as session:
        claimed = (
            await session.execute(
                text("SELECT * FROM claim_next_job(:lease_seconds)"),
                {"lease_seconds": settings.job_lease_seconds},
            )
        ).first()
        await session.commit()
    if claimed is None:
        return False
    job = claimed._mapping
    job_id = job["id"]
    tenant_id = job["tenant_id"]
    attempts = job["attempts"]
    if attempts > settings.job_max_attempts:
        async with tenant_session(tenant_id) as session:
            await session.execute(
                text("UPDATE jobs SET status = 'failed', error = :error WHERE id = :id"),
                {"id": job_id, "error": "Job exceeded maximum attempts"},
            )
        return True
    handler = HANDLERS.get(job["kind"])
    try:
        if handler is None:
            raise PermanentJobError(f"Unknown job kind: {job['kind']}")
        async with tenant_session(tenant_id) as session:
            result = await handler(session, job["payload"])
            await session.execute(
                text(
                    "UPDATE jobs SET status = 'succeeded', result = CAST(:result AS jsonb), "
                    "error = NULL WHERE id = :id"
                ),
                {
                    "id": job_id,
                    "result": json.dumps(result) if result is not None else None,
                },
            )
    except Exception as error:
        LOGGER.exception("Job %s (%s) failed", job_id, job["kind"])
        message = str(error)
        if isinstance(error, NotImplementedError) and not message:
            message = (
                "graph.ingest_conversation is not implemented yet"
                if job["kind"] == "graph_ingest"
                else f"{job['kind']} is not implemented yet"
            )
        error_text = f"{type(error).__name__}: {message}"
        permanent = (
            isinstance(error, (PermanentJobError, NotImplementedError))
            or attempts >= settings.job_max_attempts
        )
        async with tenant_session(tenant_id) as session:
            if permanent:
                await session.execute(
                    text("UPDATE jobs SET status = 'failed', error = :error WHERE id = :id"),
                    {"id": job_id, "error": error_text},
                )
            else:
                delay = settings.job_retry_base_seconds * 2 ** (attempts - 1)
                await session.execute(
                    text(
                        "UPDATE jobs SET status = 'queued', error = :error, "
                        "run_after = now() + make_interval(secs => :delay) WHERE id = :id"
                    ),
                    {"id": job_id, "error": error_text, "delay": delay},
                )
    return True


async def worker(*, stop: asyncio.Event | None = None) -> None:
    settings = get_settings()
    stop = stop or asyncio.Event()
    while not stop.is_set():
        processed = await run_next_job()
        if not processed:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
