import os
from collections.abc import AsyncIterator
from uuid import uuid4

import psycopg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from inbound_api.db import engine
from inbound_api.main import app
from inbound_api.settings import get_settings


def _owner_database_url() -> str:
    return os.environ.get(
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound",
    ).replace("postgresql+psycopg://", "postgresql://")


@pytest_asyncio.fixture
async def owner_database_url() -> str:
    return _owner_database_url()


@pytest_asyncio.fixture(autouse=True)
async def dispose_async_engine(monkeypatch) -> AsyncIterator[None]:
    monkeypatch.setattr(get_settings(), "session_cookie_secure", False)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest_asyncio.fixture
async def tenant_pair() -> AsyncIterator[dict]:
    clients = [
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") for _ in range(2)
    ]
    created = []
    try:
        for client in clients:
            email = f"platform-{uuid4().hex}@example.com"
            response = await client.post(
                "/auth/signup",
                json={
                    "email": email,
                    "password": "valid-platform-password",
                    "display_name": "Platform User",
                    "tenant_name": "Platform Tenant",
                },
            )
            assert response.status_code == 201, response.text
            body = response.json()
            created.append(
                {
                    "client": client,
                    "email": email,
                    "user_id": body["user"]["id"],
                    "tenant_id": body["tenants"][0]["id"],
                }
            )
        yield {"first": created[0], "second": created[1]}
    finally:
        for client in clients:
            await client.aclose()
        tenant_ids = [item["tenant_id"] for item in created]
        emails = [item["email"] for item in created]
        if tenant_ids or emails:
            with psycopg.connect(_owner_database_url()) as connection:
                for tenant_id in tenant_ids:
                    connection.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (tenant_id,),
                    )
                    connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
                if emails:
                    connection.execute("DELETE FROM users WHERE email = ANY(%s)", (emails,))
