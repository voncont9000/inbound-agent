import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from inbound_api.db import session_factory, tenant_session
from inbound_api.jobs import HANDLERS, PermanentJobError, enqueue, run_next_job, worker
from inbound_api.models import Job
from inbound_api.settings import get_settings


async def _drain_claimable_jobs() -> None:
    for _ in range(100):
        if not await run_next_job():
            return


async def _enqueue_test_job(tenant_id: UUID, kind: str) -> UUID:
    async with tenant_session(tenant_id) as session:
        return await enqueue(session, kind, {"test_id": str(uuid4())})


async def _get_job(tenant_id: UUID, job_id: UUID) -> Job:
    async with tenant_session(tenant_id) as session:
        job = await session.scalar(select(Job).where(Job.id == job_id))
        assert job is not None
        return job


async def test_job_is_enqueued_and_processed_in_tenant_context(tenant_pair) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    kind = f"test.platform.success.{uuid4().hex}"
    handled = []

    async def handler(_session, payload):
        handled.append(payload)
        return {"ok": True}

    HANDLERS[kind] = handler
    try:
        async with tenant_session(tenant_id) as session:
            job_id = await enqueue(session, kind, {"value": 42})
        assert await run_next_job()
        async with tenant_session(tenant_id) as session:
            job = await session.scalar(select(Job).where(Job.id == job_id))
            assert job is not None
            assert job.status.value == "succeeded"
            assert job.result == {"ok": True}
            assert job.attempts == 1
        assert handled == [{"value": 42}]
    finally:
        HANDLERS.pop(kind, None)


async def test_job_retries_twice_then_succeeds(tenant_pair, monkeypatch) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    settings = get_settings()
    monkeypatch.setattr(settings, "job_max_attempts", 3)
    monkeypatch.setattr(settings, "job_retry_base_seconds", 0)
    kind = f"test.platform.retry.{uuid4().hex}"
    invocation_count = 0

    async def handler(_session, _payload):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count < 3:
            raise RuntimeError("temporary failure")
        return {"recovered": True}

    HANDLERS[kind] = handler
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        assert await run_next_job()
        assert await run_next_job()
        assert await run_next_job()
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "succeeded"
        assert job.attempts == 3
        assert job.result == {"recovered": True}
        assert invocation_count == 3
    finally:
        HANDLERS.pop(kind, None)


async def test_job_fails_after_maximum_attempts(tenant_pair, monkeypatch) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    settings = get_settings()
    monkeypatch.setattr(settings, "job_max_attempts", 3)
    monkeypatch.setattr(settings, "job_retry_base_seconds", 0)
    kind = f"test.platform.always-fails.{uuid4().hex}"
    invocation_count = 0

    async def handler(_session, _payload):
        nonlocal invocation_count
        invocation_count += 1
        raise RuntimeError("persistent failure")

    HANDLERS[kind] = handler
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        for _ in range(3):
            assert await run_next_job()
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "failed"
        assert job.attempts == 3
        assert "RuntimeError: persistent failure" in job.error
        assert invocation_count == 3
    finally:
        HANDLERS.pop(kind, None)


async def test_job_retry_uses_exponential_backoff(tenant_pair, monkeypatch) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    settings = get_settings()
    monkeypatch.setattr(settings, "job_max_attempts", 3)
    monkeypatch.setattr(settings, "job_retry_base_seconds", 10)
    kind = f"test.platform.backoff.{uuid4().hex}"

    async def handler(_session, _payload):
        raise RuntimeError("retry later")

    HANDLERS[kind] = handler
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        before = datetime.now(UTC)
        assert await run_next_job()
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "queued"
        assert job.run_after >= before + timedelta(seconds=9)
        assert "RuntimeError: retry later" in job.error
    finally:
        HANDLERS.pop(kind, None)


async def test_permanent_job_error_fails_without_retry(tenant_pair) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    kind = f"test.platform.permanent.{uuid4().hex}"

    async def handler(_session, _payload):
        raise PermanentJobError("invalid payload")

    HANDLERS[kind] = handler
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        assert await run_next_job()
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "failed"
        assert job.attempts == 1
        assert "PermanentJobError: invalid payload" in job.error
    finally:
        HANDLERS.pop(kind, None)


async def test_concurrent_workers_claim_a_job_only_once(tenant_pair) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    kind = f"test.platform.concurrent.{uuid4().hex}"
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    invocation_count = 0

    async def handler(_session, _payload):
        nonlocal invocation_count
        invocation_count += 1
        handler_started.set()
        await release_handler.wait()
        return {"claimed": True}

    async def second_worker():
        result = await run_next_job()
        release_handler.set()
        return result

    HANDLERS[kind] = handler
    first_worker = None
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        first_worker = asyncio.create_task(run_next_job())
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        first_result, second_result = await asyncio.gather(first_worker, second_worker())
        release_handler.set()
        job = await _get_job(tenant_id, job_id)
        assert first_result is True
        assert second_result is False
        assert invocation_count == 1
        assert job.status.value == "succeeded"
        assert job.attempts == 1
    finally:
        release_handler.set()
        if first_worker is not None and not first_worker.done():
            await first_worker
        HANDLERS.pop(kind, None)


async def test_slow_handler_heartbeat_prevents_reclaim(tenant_pair, monkeypatch) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    monkeypatch.setattr(get_settings(), "job_lease_seconds", 1)
    kind = f"test.platform.heartbeat.{uuid4().hex}"
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def handler(_session, _payload):
        handler_started.set()
        await release_handler.wait()
        return {"alive": True}

    HANDLERS[kind] = handler
    first_worker = None
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        first_worker = asyncio.create_task(run_next_job())
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        await asyncio.sleep(1.2)
        assert await run_next_job() is False
        release_handler.set()
        assert await first_worker is True
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "succeeded"
        assert job.attempts == 1
    finally:
        release_handler.set()
        if first_worker is not None and not first_worker.done():
            await first_worker
        HANDLERS.pop(kind, None)


async def test_stale_worker_rolls_back_when_lease_token_changes(tenant_pair) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    kind = f"test.platform.stale.{uuid4().hex}"
    side_effect_kind = f"{kind}.side-effect"
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def handler(session, payload):
        await enqueue(session, side_effect_kind, {"test_id": payload["test_id"]})
        handler_started.set()
        await release_handler.wait()
        return {"stale": True}

    HANDLERS[kind] = handler
    first_worker = None
    replacement_token = uuid4()
    try:
        job_id = await _enqueue_test_job(tenant_id, kind)
        first_worker = asyncio.create_task(run_next_job())
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        async with tenant_session(tenant_id) as session:
            await session.execute(
                text("UPDATE jobs SET lease_token = :token WHERE id = :id"),
                {"token": replacement_token, "id": job_id},
            )
        release_handler.set()
        assert await first_worker is True
        job = await _get_job(tenant_id, job_id)
        assert job.status.value == "running"
        assert job.lease_token == replacement_token
        async with tenant_session(tenant_id) as session:
            side_effect_count = await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == side_effect_kind)
            )
        assert side_effect_count == 0
    finally:
        release_handler.set()
        if first_worker is not None and not first_worker.done():
            await first_worker
        HANDLERS.pop(kind, None)


async def test_enqueue_requires_tenant_context() -> None:
    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="Tenant context"):
            await enqueue(session, "test.no_tenant", {})


async def test_worker_retries_after_unexpected_job_error(monkeypatch) -> None:
    stop = asyncio.Event()
    calls = 0

    async def fail_once_then_stop() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database error")
        stop.set()
        return False

    monkeypatch.setattr("inbound_api.jobs.run_next_job", fail_once_then_stop)
    monkeypatch.setattr(get_settings(), "worker_poll_seconds", 0.01)

    await worker(stop=stop)

    assert calls == 2
