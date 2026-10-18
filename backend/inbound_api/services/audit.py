import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext


async def write_audit(
    session: AsyncSession,
    ctx: TenantContext,
    action: str,
    target_type: str,
    target_id: UUID,
    diff: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_user_id, action, target_type, target_id, "
            "diff) "
            "VALUES (:tenant_id, :actor_user_id, :action, :target_type, :target_id, "
            "CAST(:diff AS jsonb))"
        ),
        {
            "tenant_id": ctx.tenant_id,
            "actor_user_id": ctx.user.id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "diff": json.dumps(diff or {}),
        },
    )
