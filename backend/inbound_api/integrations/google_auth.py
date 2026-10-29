from __future__ import annotations

import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.channels.credentials import decrypt_credential
from inbound_api.jobs import PermanentJobError
from inbound_api.models import ChannelConnection
from inbound_api.settings import get_settings

GOOGLE_SCOPE_SETS: dict[str, list[str]] = {
    "gmail": ["https://www.googleapis.com/auth/gmail.modify"],
    "sheets": ["https://www.googleapis.com/auth/spreadsheets"],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ],
}
GOOGLE_IDENTITY_SCOPES = ["openid", "email"]
FAKE_ACCESS_TOKEN = "fake-google-access-token"

_ACCESS_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


def google_scopes_for_features(features: list[str] | None = None) -> list[str]:
    requested = list(dict.fromkeys(features or []))
    allowed = GOOGLE_SCOPE_SETS.keys() - {"gmail"}
    unknown = set(requested) - allowed
    if unknown:
        raise ValueError(f"Unsupported Google feature(s): {', '.join(sorted(unknown))}")
    scopes = [*GOOGLE_IDENTITY_SCOPES, *GOOGLE_SCOPE_SETS["gmail"]]
    for feature in requested:
        scopes.extend(GOOGLE_SCOPE_SETS[feature])
    return list(dict.fromkeys(scopes))


async def google_access_token(connection: ChannelConnection) -> str:
    settings = get_settings()
    if settings.gmail_mode == "fake":
        return FAKE_ACCESS_TOKEN
    if settings.gmail_mode != "google":
        raise RuntimeError("Google integrations are not enabled")
    ciphertext = connection.credentials_ciphertext
    if not ciphertext:
        raise ValueError("Google refresh token is missing")

    cache_key = (str(connection.id), ciphertext)
    cached = _ACCESS_TOKEN_CACHE.get(cache_key)
    if cached and time.monotonic() < cached[1] - 30:
        return cached[0]

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": decrypt_credential(ciphertext),
                "grant_type": "refresh_token",
            },
        )
    _raise_token_response(response)
    data = response.json()
    token = data["access_token"]
    _ACCESS_TOKEN_CACHE[cache_key] = (
        token,
        time.monotonic() + int(data.get("expires_in", 3600)),
    )
    return token


async def google_connection_for(
    session: AsyncSession, assistant_id: Any
) -> ChannelConnection | None:
    return await session.scalar(
        select(ChannelConnection)
        .where(
            ChannelConnection.assistant_id == assistant_id,
            ChannelConnection.kind.in_(("gmail", "google")),
            ChannelConnection.status == "active",
        )
        .order_by(ChannelConnection.created_at.desc())
        .limit(1)
    )


async def action_google_connection_for(
    session: AsyncSession,
    *,
    assistant_id: Any,
    tenant_id: Any,
    feature: str,
) -> ChannelConnection | None:
    assistant_connection = await session.scalar(
        select(ChannelConnection)
        .where(
            ChannelConnection.assistant_id == assistant_id,
            ChannelConnection.tenant_id == tenant_id,
            ChannelConnection.kind.in_(("gmail", "google")),
            ChannelConnection.status == "active",
        )
        .order_by(ChannelConnection.created_at.desc())
        .limit(1)
    )
    if assistant_connection is not None and has_google_feature(assistant_connection, feature):
        return assistant_connection

    tenant_connections = (
        await session.scalars(
            select(ChannelConnection)
            .where(
                ChannelConnection.tenant_id == tenant_id,
                ChannelConnection.kind.in_(("gmail", "google")),
                ChannelConnection.status == "active",
            )
            .order_by(ChannelConnection.created_at.desc())
        )
    ).all()
    return next(
        (
            connection
            for connection in tenant_connections
            if has_google_feature(connection, feature)
        ),
        None,
    )


def has_google_feature(connection: Any, feature: str) -> bool:
    required_scopes = GOOGLE_SCOPE_SETS.get(feature)
    if required_scopes is None:
        return False
    if getattr(connection, "kind", "gmail") not in {"gmail", "google"}:
        return False
    connection_settings = getattr(connection, "settings", None) or {}
    if get_settings().gmail_mode == "fake":
        fake_features = connection_settings.get("google_features", [])
        return isinstance(fake_features, list) and feature in fake_features
    granted_scopes = connection_settings.get("google_scopes", [])
    return isinstance(granted_scopes, list) and all(
        scope in granted_scopes for scope in required_scopes
    )


def _raise_token_response(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = response.status_code
        if status != 429 and status < 500:
            raise PermanentJobError(f"Google OAuth token refresh failed ({status})") from error
        raise
