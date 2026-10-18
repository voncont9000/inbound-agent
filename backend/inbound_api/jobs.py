from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

JobHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, JobHandler] = {}


async def enqueue(session: AsyncSession, kind: str, payload: dict[str, Any]) -> UUID:
    """Add a tenant-scoped durable job to the Postgres-backed queue."""
    raise NotImplementedError


async def worker() -> None:
    """Claim jobs with FOR UPDATE SKIP LOCKED and dispatch registered handlers."""
    raise NotImplementedError
