from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.channels.credentials import encrypt_credential
from inbound_api.channels.gmail_client import GoogleGmailClient
from inbound_api.channels.gmail_oauth_state import (
    issue_state,
    verify_state_with_features,
)
from inbound_api.db import tenant_session
from inbound_api.integrations.google_auth import google_scopes_for_features
from inbound_api.models import Assistant, ChannelConnection, MembershipRole
from inbound_api.schemas import GmailOAuthStartRequest
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.settings import get_settings

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["gmail"])
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/channels/gmail/mode")
async def gmail_mode() -> dict[str, Literal["off", "fake", "google"]]:
    return {"mode": get_settings().gmail_mode}


@router.post("/assistants/{assistant_id}/channels/gmail/oauth-start")
async def gmail_oauth_start(
    assistant_id: UUID,
    payload: GmailOAuthStartRequest | None = None,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> dict[str, str]:
    settings = get_settings()
    if settings.gmail_mode == "off":
        raise HTTPException(status_code=409, detail="Gmail is not enabled")
    if settings.gmail_mode != "google":
        raise HTTPException(status_code=422, detail="Google OAuth is not enabled")
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    features = payload.features if payload else []
    state = issue_state(ctx.tenant_id, assistant_id, ctx.user.id, features=features)
    params = urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": settings.google_oauth_redirect_url,
            "response_type": "code",
            "scope": " ".join(google_scopes_for_features(features)),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}


@router.get("/oauth/google/callback")
async def gmail_oauth_callback(code: str, state: str):
    if get_settings().gmail_mode == "off":
        raise HTTPException(status_code=409, detail="Gmail is not enabled")
    try:
        tenant_id, assistant_id, _user_id, features = verify_state_with_features(state)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid or expired Gmail OAuth state"
        ) from None
    settings = get_settings()
    try:
        token_data, profile = await exchange_google_code(code, settings)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        LOGGER.warning("Google Gmail OAuth exchange failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="Google Gmail authorization failed") from error
    email = str(profile.get("emailAddress", "")).strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=502, detail="Google returned an invalid mailbox")
    base = (settings.allowed_origins or ["http://localhost:5173"])[0].rstrip("/")
    async with tenant_session(tenant_id) as session:
        if await session.get(Assistant, assistant_id) is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        refresh_token = token_data.get("refresh_token")
        if features:
            connection = await session.scalar(
                select(ChannelConnection).where(
                    ChannelConnection.tenant_id == tenant_id,
                    ChannelConnection.kind == "google",
                    ChannelConnection.external_id == email,
                )
            )
            if not refresh_token and (connection is None or not connection.credentials_ciphertext):
                raise HTTPException(status_code=502, detail="Google did not return a refresh token")
            if connection is None:
                connection = ChannelConnection(
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    kind="google",
                    external_id=email,
                    status="active",
                    settings={},
                )
                session.add(connection)
            connection.assistant_id = assistant_id
            connection.status = "active"
            if refresh_token:
                connection.credentials_ciphertext = encrypt_credential(refresh_token)
            connection.settings = {
                **(connection.settings or {}),
                "google_scopes": str(token_data.get("scope", "")).split(),
            }
        else:
            connection = await session.scalar(
                select(ChannelConnection).where(
                    ChannelConnection.kind == "gmail",
                    ChannelConnection.external_id == email,
                )
            )
            if connection is None:
                try:
                    async with session.begin_nested():
                        connection = ChannelConnection(
                            tenant_id=tenant_id,
                            assistant_id=assistant_id,
                            kind="gmail",
                            external_id=email,
                            status="active",
                            settings={},
                        )
                        session.add(connection)
                        await session.flush()
                except IntegrityError:
                    connection = await session.scalar(
                        select(ChannelConnection).where(
                            ChannelConnection.kind == "gmail",
                            ChannelConnection.external_id == email,
                        )
                    )
                    if connection is None:
                        return RedirectResponse(
                            f"{base}/channels?error=gmail_in_use", status_code=303
                        )
            connection.assistant_id = assistant_id
            connection.status = "active"
            if refresh_token:
                connection.credentials_ciphertext = encrypt_credential(refresh_token)
            elif not connection.credentials_ciphertext:
                raise HTTPException(status_code=502, detail="Google did not return a refresh token")
            client = GoogleGmailClient(connection, settings)
            history_id = await client.watch(settings.gmail_pubsub_topic or "")
            connection.settings = {
                **(connection.settings or {}),
                "google_scopes": str(token_data.get("scope", "")).split(),
                "history_id": history_id,
                "watch_renewed_at": datetime.now(UTC).isoformat(),
            }
            await enqueue_gmail_sync(session, connection.id)
    destination = (
        f"{base}/channels?connected=gmail"
        if not features
        else f"{base}/assistants/{assistant_id}/settings"
    )
    return RedirectResponse(destination, status_code=303)


async def exchange_google_code(code: str, settings, *, transport=None):
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_url,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        profile_response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        profile_response.raise_for_status()
    return token_data, profile_response.json()
