import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import (
    TenantContext,
    current_tenant,
    enforce_request_origin,
    get_db_session,
)
from inbound_api.db import session_factory, tenant_session
from inbound_api.models import Tenant, User, UserSession
from inbound_api.schemas import (
    AuthResponse,
    LoginRequest,
    LogoutResponse,
    MeResponse,
    SignupRequest,
)
from inbound_api.services.passwords import hash_password, verify_password
from inbound_api.settings import get_settings

router = APIRouter(tags=["auth"])
_DUMMY_PASSWORD_HASH = PasswordHasher().hash("not-a-real-account-password")
_db_session_dependency = Depends(get_db_session)
_current_tenant_dependency = Depends(current_tenant)


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    ttl = settings.session_ttl_hours * 60 * 60
    response.set_cookie(
        "inbound_session",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
        max_age=ttl,
    )


def _token_from_request(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    return request.cookies.get("inbound_session")


async def _create_session(session: AsyncSession, user_id) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)
    session.add(
        UserSession(
            user_id=user_id,
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires_at,
        )
    )
    return token


async def _tenant_responses(session: AsyncSession, email: str):
    memberships = await session.execute(
        text(
            "SELECT user_id, tenant_id, role "
            "FROM lookup_user_memberships(CAST(:email AS citext)) "
            "ORDER BY tenant_id"
        ),
        {"email": email},
    )
    rows = memberships.all()
    tenants_result = await session.execute(
        select(Tenant).where(Tenant.id.in_([row.tenant_id for row in rows]))
    )
    names = {tenant.id: tenant.name for tenant in tenants_result.scalars()}
    return [
        {
            "id": row.tenant_id,
            "name": names[row.tenant_id],
            "role": row.role,
        }
        for row in rows
    ]


@router.post("/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = _db_session_dependency,
) -> AuthResponse:
    enforce_request_origin(request, always=True)
    existing = await session.scalar(select(User.id).where(User.email == str(payload.email)))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already exists")
    tenant_id = uuid4()
    try:
        async with tenant_session(tenant_id) as tenant_db:
            await tenant_db.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": payload.tenant_name},
            )
            user = User(
                email=str(payload.email),
                password_hash=hash_password(payload.password),
                display_name=payload.display_name,
            )
            tenant_db.add(user)
            await tenant_db.flush()
            await tenant_db.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, role) "
                    "VALUES (:tenant_id, :user_id, 'owner')"
                ),
                {"tenant_id": tenant_id, "user_id": user.id},
            )
            token = await _create_session(tenant_db, user.id)
            result_user = user
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Email already exists") from None
    _set_session_cookie(response, token)
    return AuthResponse(
        user={
            "id": result_user.id,
            "email": result_user.email,
            "display_name": result_user.display_name,
        },
        tenants=[{"id": tenant_id, "name": payload.tenant_name, "role": "owner"}],
        access_token=token,
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = _db_session_dependency,
) -> AuthResponse:
    enforce_request_origin(request, always=True)
    user = await session.scalar(select(User).where(User.email == str(payload.email)))
    if user is None:
        verify_password(payload.password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = await _create_session(session, user.id)
    tenants = await _tenant_responses(session, user.email)
    await session.commit()
    _set_session_cookie(response, token)
    return AuthResponse(
        user={"id": user.id, "email": user.email, "display_name": user.display_name},
        tenants=tenants,
        access_token=token,
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = _db_session_dependency,
) -> LogoutResponse:
    enforce_request_origin(request)
    token = _token_from_request(request)
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        record = await session.scalar(
            select(UserSession).where(UserSession.token_sha256 == token_hash)
        )
        if record is not None:
            await session.delete(record)
            await session.commit()
    response.delete_cookie("inbound_session", path="/", httponly=True, samesite="lax")
    return LogoutResponse(logged_out=True)


@router.get("/me", response_model=MeResponse)
async def me(ctx: TenantContext = _current_tenant_dependency) -> MeResponse:
    async with session_factory() as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
    return MeResponse(
        user={
            "id": ctx.user.id,
            "email": ctx.user.email,
            "display_name": ctx.user.display_name,
        },
        tenant={"id": ctx.tenant_id, "name": tenant.name, "role": ctx.role.value},
    )
