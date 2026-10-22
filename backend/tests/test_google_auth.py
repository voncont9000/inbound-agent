from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest

from inbound_api.auth import TenantContext
from inbound_api.channels import gmail_oauth_state
from inbound_api.channels.gmail_client import GoogleGmailClient
from inbound_api.integrations import google_auth
from inbound_api.models import MembershipRole
from inbound_api.routers import gmail as gmail_router
from inbound_api.schemas import ChannelConnectionResponse, GmailOAuthStartRequest


def test_google_scope_set_building():
    assert google_auth.google_scopes_for_features(["sheets", "calendar"]) == [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ]
    assert google_auth.google_scopes_for_features() == [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
    ]


def test_google_oauth_state_carries_features_and_rejects_tampering(monkeypatch):
    monkeypatch.setattr(
        gmail_oauth_state,
        "get_settings",
        lambda: SimpleNamespace(voice_service_token="test-secret"),
    )
    ids = (uuid4(), uuid4(), uuid4())
    state = gmail_oauth_state.issue_state(*ids, features=["sheets", "calendar"])

    assert gmail_oauth_state.verify_state(state) == ids
    assert gmail_oauth_state.verify_state_with_features(state) == (*ids, ["sheets", "calendar"])
    encoded, signature = state.split(".")
    with pytest.raises(ValueError, match="Invalid Gmail OAuth state"):
        gmail_oauth_state.verify_state_with_features(f"{encoded}x.{signature}")


@pytest.mark.asyncio
async def test_google_access_token_refreshes_and_caches_through_gmail_client(monkeypatch):
    google_auth._ACCESS_TOKEN_CACHE.clear()
    settings = SimpleNamespace(
        gmail_mode="google",
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
    )
    monkeypatch.setattr(google_auth, "get_settings", lambda: settings)
    monkeypatch.setattr(google_auth, "decrypt_credential", lambda _: "refresh-token")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})

    transport = httpx.MockTransport(respond)
    async_client = google_auth.httpx.AsyncClient

    def client_with_transport(*args, **kwargs):
        return async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(google_auth.httpx, "AsyncClient", client_with_transport)
    connection = SimpleNamespace(id=uuid4(), credentials_ciphertext="encrypted-refresh-token")
    client = GoogleGmailClient(connection, settings)

    assert await client._token() == "access-token"
    assert await client._token() == "access-token"
    assert len(requests) == 1
    assert requests[0].url.host == "oauth2.googleapis.com"
    google_auth._ACCESS_TOKEN_CACHE.clear()


@pytest.mark.asyncio
async def test_fake_google_access_token_and_feature_checks(monkeypatch):
    connection = SimpleNamespace(
        kind="gmail",
        settings={"google_features": ["gmail", "sheets"]},
    )
    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )

    assert await google_auth.google_access_token(connection) == google_auth.FAKE_ACCESS_TOKEN
    assert google_auth.has_google_feature(connection, "gmail")
    assert google_auth.has_google_feature(connection, "sheets")
    assert not google_auth.has_google_feature(connection, "calendar")

    connection.settings = {
        "google_scopes": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.freebusy",
        ]
    }
    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="google"),
    )
    assert google_auth.has_google_feature(connection, "gmail")
    assert google_auth.has_google_feature(connection, "sheets")
    assert google_auth.has_google_feature(connection, "calendar")
    connection.settings["google_scopes"].remove("https://www.googleapis.com/auth/calendar.freebusy")
    assert not google_auth.has_google_feature(connection, "calendar")


@pytest.mark.asyncio
async def test_google_connection_for_returns_active_gmail_connection():
    connection = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(scalar=AsyncMock(return_value=connection))

    assert await google_auth.google_connection_for(session, uuid4()) is connection
    query = str(session.scalar.call_args.args[0])
    assert "channel_connections.kind" in query
    assert "channel_connections.status" in query


@pytest.mark.asyncio
async def test_action_google_connection_for_finds_unassigned_gmail_in_same_tenant(monkeypatch):
    tenant_id = uuid4()
    assistant_id = uuid4()
    unassigned = SimpleNamespace(
        assistant_id=None,
        tenant_id=tenant_id,
        kind="gmail",
        status="active",
        settings={"google_features": ["gmail"]},
    )
    other_tenant = SimpleNamespace(
        assistant_id=None,
        tenant_id=uuid4(),
        kind="gmail",
        status="active",
        settings={"google_features": ["gmail"]},
    )
    monkeypatch.setattr(
        google_auth,
        "has_google_feature",
        lambda connection, feature: feature == "gmail",
    )

    class Session:
        query = None

        async def scalar(self, _statement):
            return None

        async def scalars(self, statement):
            self.query = str(statement)
            return SimpleNamespace(
                all=lambda: [
                    row for row in (other_tenant, unassigned) if row.tenant_id == tenant_id
                ]
            )

    session = Session()
    result = await google_auth.action_google_connection_for(
        session,
        assistant_id=assistant_id,
        tenant_id=tenant_id,
        feature="gmail",
    )

    assert result is unassigned
    assert "channel_connections.tenant_id" in session.query


async def test_oauth_start_requests_features_and_signed_state(monkeypatch):
    tenant_id, assistant_id, user_id = uuid4(), uuid4(), uuid4()
    settings = SimpleNamespace(
        gmail_mode="google",
        google_oauth_client_id="client",
        google_oauth_redirect_url="https://app.test/callback",
    )
    monkeypatch.setattr(gmail_router, "get_settings", lambda: settings)
    monkeypatch.setattr(
        gmail_oauth_state,
        "get_settings",
        lambda: SimpleNamespace(voice_service_token="test-secret"),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=object()))
    context = TenantContext(
        user=SimpleNamespace(id=user_id),
        tenant_id=tenant_id,
        role=MembershipRole.OWNER,
    )

    result = await gmail_router.gmail_oauth_start(
        assistant_id,
        GmailOAuthStartRequest(features=["sheets", "calendar"]),
        context,
        session,
    )
    params = parse_qs(urlparse(result["url"]).query)

    assert set(params["scope"][0].split()) == set(
        google_auth.google_scopes_for_features(["sheets", "calendar"])
    )
    assert params["include_granted_scopes"] == ["true"]
    assert gmail_oauth_state.verify_state_with_features(params["state"][0]) == (
        tenant_id,
        assistant_id,
        user_id,
        ["sheets", "calendar"],
    )


async def test_unknown_oauth_feature_returns_422(tenant_pair):
    client = tenant_pair["first"]["client"]
    response = await client.post(
        f"/assistants/{uuid4()}/channels/gmail/oauth-start",
        json={"features": ["drive"]},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_callback_stores_granted_scopes_and_keeps_existing_refresh_token(monkeypatch):
    tenant_id, assistant_id, user_id = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        gmail_oauth_state,
        "get_settings",
        lambda: SimpleNamespace(voice_service_token="test-secret"),
    )
    state = gmail_oauth_state.issue_state(
        tenant_id,
        assistant_id,
        user_id,
        features=["sheets"],
    )
    granted_scopes = [
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    settings = SimpleNamespace(
        gmail_mode="google",
        gmail_pubsub_topic="projects/example/topics/gmail",
        allowed_origins=["https://app.test"],
    )
    connection = SimpleNamespace(
        id=uuid4(),
        assistant_id=assistant_id,
        status="active",
        settings={"keep": True},
        credentials_ciphertext="existing-encrypted-refresh-token",
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=object()),
        scalar=AsyncMock(return_value=connection),
    )

    @asynccontextmanager
    async def fake_tenant_session(requested_tenant_id):
        assert requested_tenant_id == tenant_id
        yield session

    class FakeGmailClient:
        def __init__(self, _connection, _settings):
            pass

        async def watch(self, _topic):
            return "history-42"

    monkeypatch.setattr(gmail_router, "get_settings", lambda: settings)
    monkeypatch.setattr(gmail_router, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(
        gmail_router,
        "exchange_google_code",
        AsyncMock(
            return_value=(
                {"access_token": "access-token", "scope": " ".join(granted_scopes)},
                {"emailAddress": "owner@example.test"},
            )
        ),
    )
    monkeypatch.setattr(gmail_router, "GoogleGmailClient", FakeGmailClient)
    monkeypatch.setattr(gmail_router, "enqueue_gmail_sync", AsyncMock())

    response = await gmail_router.gmail_oauth_callback("auth-code", state)

    assert response.status_code == 303
    assert response.headers["location"] == f"https://app.test/assistants/{assistant_id}/settings"
    assert connection.credentials_ciphertext == "existing-encrypted-refresh-token"
    assert connection.settings["google_scopes"] == granted_scopes
    assert connection.settings["history_id"] == "history-42"


@pytest.mark.asyncio
async def test_plain_gmail_oauth_redirects_to_workspace_channels(monkeypatch):
    tenant_id, assistant_id, user_id = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        gmail_oauth_state,
        "get_settings",
        lambda: SimpleNamespace(voice_service_token="test-secret"),
    )
    state = gmail_oauth_state.issue_state(tenant_id, assistant_id, user_id)
    settings = SimpleNamespace(
        gmail_mode="google",
        gmail_pubsub_topic="projects/example/topics/gmail",
        allowed_origins=["https://app.test"],
    )
    connection = SimpleNamespace(
        id=uuid4(),
        assistant_id=assistant_id,
        status="active",
        settings={},
        credentials_ciphertext="existing-encrypted-refresh-token",
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=object()),
        scalar=AsyncMock(return_value=connection),
    )

    @asynccontextmanager
    async def fake_tenant_session(requested_tenant_id):
        assert requested_tenant_id == tenant_id
        yield session

    class FakeGmailClient:
        def __init__(self, _connection, _settings):
            pass

        async def watch(self, _topic):
            return "history-42"

    monkeypatch.setattr(gmail_router, "get_settings", lambda: settings)
    monkeypatch.setattr(gmail_router, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(
        gmail_router,
        "exchange_google_code",
        AsyncMock(
            return_value=(
                {"access_token": "access-token", "scope": "openid email"},
                {"emailAddress": "owner@example.test"},
            )
        ),
    )
    monkeypatch.setattr(gmail_router, "GoogleGmailClient", FakeGmailClient)
    monkeypatch.setattr(gmail_router, "enqueue_gmail_sync", AsyncMock())

    response = await gmail_router.gmail_oauth_callback("auth-code", state)

    assert response.status_code == 303
    assert response.headers["location"] == "https://app.test/channels?connected=gmail"


def test_gmail_connection_response_exposes_features_not_scopes(monkeypatch):
    scopes = [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="google"),
    )
    response = ChannelConnectionResponse(
        id=uuid4(),
        assistant_id=uuid4(),
        kind="gmail",
        external_id="mailbox@example.test",
        status="active",
        settings={"google_scopes": scopes},
        created_at=datetime.now(UTC),
    )
    serialized = response.model_dump(mode="json")

    assert serialized["google_features"] == ["gmail", "sheets"]
    assert "google_scopes" not in serialized["settings"]
    assert not any(scope in str(serialized) for scope in scopes)
