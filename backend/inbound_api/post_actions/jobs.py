from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import register
from inbound_api.post_actions.service import (
    execute_conversation_action,
    plan_conversation_actions,
)


@register("post_actions_run")
async def post_actions_run(session: AsyncSession, payload: dict) -> dict[str, int]:
    return await plan_conversation_actions(session, UUID(payload["conversation_id"]))


@register("post_action_execute")
async def post_action_execute(session: AsyncSession, payload: dict):
    return await execute_conversation_action(
        session,
        conversation_id=UUID(payload["conversation_id"]),
        action_key=payload["action_key"],
        fire_count=int(payload["fire_count"]),
    )
