import logging
from uuid import UUID

from fastapi import HTTPException
from inbound_core.call_report import RouteResponse
from inbound_core.config import AssistantConfig
from sqlalchemy import select

from inbound_api import graph
from inbound_api.db import tenant_session
from inbound_api.models import Assistant, KnowledgeDigest

LOGGER = logging.getLogger(__name__)


async def load_assistant_route(
    tenant_id: UUID,
    assistant_id: UUID,
    *,
    caller_phone: str | None,
    include_context_card: bool,
    source: str = "phone",
    whatsapp_id: str | None = None,
    caller_email: str | None = None,
) -> RouteResponse:
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
        context_card = None
        if include_context_card:
            try:
                async with session.begin_nested():
                    person = await graph.resolve_person(
                        session,
                        phone=caller_phone,
                        email=caller_email,
                        whatsapp_id=whatsapp_id,
                        source=source,
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
