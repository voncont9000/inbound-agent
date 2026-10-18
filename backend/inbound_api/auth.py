from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.models import MembershipRole, User


async def current_user(session: AsyncSession) -> User:
    """Resolve the authenticated user from the request session."""
    raise NotImplementedError


async def current_tenant(session: AsyncSession, user: User) -> UUID:
    """Resolve the active tenant membership for the authenticated user."""
    raise NotImplementedError


def require_role(*roles: MembershipRole) -> Callable[..., Any]:
    """Build a dependency that requires one of the given tenant roles."""
    raise NotImplementedError
