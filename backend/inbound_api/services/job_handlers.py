from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api import graph
from inbound_api.jobs import register
from inbound_api.services import knowledge_import as knowledge_import_service  # noqa: F401


@register("graph_ingest")
async def graph_ingest(session: AsyncSession, payload: dict) -> None:
    await graph.ingest_conversation(session, UUID(payload["conversation_id"]))
