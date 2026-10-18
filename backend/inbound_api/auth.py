import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.db import session_factory, tenant_session
from inbound_api.models import MembershipRole, User, UserSession
from inbound_api.settings import get_settings


@dataclass(frozen=True)
class TenantContext:
    user: User
    tenant_id: UUID
    role: MembershipRole


async def get_db_session():
    async with session_factory() as session:
        yield session


_DB_SESSION_DEPENDENCY = Depends(get_db_session)


def _request_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    return request.cookies.get("inbound_session")


def enforce_request_origin(request: Request, *, always: bool = False) -> None:
    authorization = request.headers.get("authorization", "")
    scheme = authorization.partition(" ")[0].casefold()
    if not always and (scheme == "bearer" or not request.cookies.get("inbound_session")):
        return
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed")
    if origin is None and request.headers.get("sec-fetch-site", "").casefold() in {
        "cross-site",
        "same-site",
    }:
        raise HTTPException(status_code=403, detail="Cross-site request is not allowed")


async def current_user(request: Request, session: AsyncSession = _DB_SESSION_DEPENDENCY) -> User:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        enforce_request_origin(request)
    token = _request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_sha256 == token_hash, UserSession.expires_at > func.now())
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    session_record, user = row
    session_record.last_seen_at = datetime.now(UTC)
    await session.commit()
    return user


_CURRENT_USER_DEPENDENCY = Depends(current_user)


async def _memberships(session: AsyncSession, email: str) -> list[tuple[UUID, MembershipRole]]:
    result = await session.execute(
        text("SELECT tenant_id, role FROM lookup_user_memberships(CAST(:email AS citext))"),
        {"email": email},
    )
    return [(row.tenant_id, MembershipRole(row.role)) for row in result]


async def current_tenant(
    user: User = _CURRENT_USER_DEPENDENCY,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    session: AsyncSession = _DB_SESSION_DEPENDENCY,
) -> TenantContext:
    memberships = await _memberships(session, user.email)
    if not memberships:
        raise HTTPException(status_code=403, detail="User has no tenant memberships")
    if x_tenant_id is not None:
        try:
            requested_id = UUID(x_tenant_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id header") from None
        match = next((item for item in memberships if item[0] == requested_id), None)
        if match is None:
            raise HTTPException(status_code=403, detail="User is not a member of this tenant")
    elif len(memberships) == 1:
        match = memberships[0]
    else:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header required")
    return TenantContext(user=user, tenant_id=match[0], role=match[1])


_CURRENT_TENANT_DEPENDENCY = Depends(current_tenant)


async def get_tenant_session(ctx: TenantContext = _CURRENT_TENANT_DEPENDENCY):
    async with tenant_session(ctx.tenant_id) as session:
        yield session


def require_role(*roles: MembershipRole | str) -> Callable[..., Any]:
    """Build a dependency that requires one of the given tenant roles."""

    required = {MembershipRole(role) for role in roles}

    async def dependency(ctx: TenantContext = _CURRENT_TENANT_DEPENDENCY) -> TenantContext:
        if ctx.role not in required and not (
            ctx.role == MembershipRole.OWNER and MembershipRole.STAFF in required
        ):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return ctx

    return dependency
