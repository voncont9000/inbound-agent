"""Context graph seams owned by the graph implementation agent."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_person(
    session: AsyncSession,
    *,
    phone: str | None,
    email: str | None,
    source: str,
) -> UUID | None:
    """Resolve an existing person from deterministic channel identities."""
    raise NotImplementedError


async def ingest_conversation(session: AsyncSession, conversation_id: UUID) -> None:
    """Extract and persist graph facts from a reported conversation."""
    raise NotImplementedError


async def build_context_card(session: AsyncSession, person_id: UUID) -> str:
    """Build a compact context card of at most approximately 800 tokens."""
    raise NotImplementedError


async def merge_persons(
    session: AsyncSession,
    *,
    survivor_id: UUID,
    merged_id: UUID,
    actor_user_id: UUID,
) -> UUID:
    """Merge two people and record a reversible audit snapshot."""
    raise NotImplementedError


async def split_person(
    session: AsyncSession,
    *,
    person_id: UUID,
    identity_ids: list[UUID],
    actor_user_id: UUID,
) -> UUID:
    """Split selected identities into a new person and record the operation."""
    raise NotImplementedError
