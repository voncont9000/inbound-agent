import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

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
                if tenant_ids:
                    connection.execute("DELETE FROM tenants WHERE id = ANY(%s)", (tenant_ids,))
                if emails:
                    connection.execute("DELETE FROM users WHERE email = ANY(%s)", (emails,))


@pytest_asyncio.fixture
async def graph_env() -> AsyncIterator[dict[str, Any]]:
    tenant_id, user_id, assistant_id = uuid4(), uuid4(), uuid4()
    owner_url = _owner_database_url()
    config = {
        "services": {
            "support": {
                "fields": [
                    {"name": "phone", "type": "phone"},
                    {"name": "email", "type": "email"},
                    {"name": "caller_name", "type": "text"},
                    {"name": "reason", "type": "text"},
                ]
            }
        }
    }
    with psycopg.connect(owner_url) as connection:
        connection.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tenant_id, "Graph"))
        connection.execute(
            "INSERT INTO users (id, email, password_hash, display_name) VALUES (%s, %s, %s, %s)",
            (user_id, f"{user_id}@example.test", "unused", "Graph tester"),
        )
        connection.execute(
            "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, %s::jsonb)",
            (assistant_id, tenant_id, "Graph assistant", json.dumps(config)),
        )

    def execute(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(owner_url) as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchall() if cursor.description else []

    yield {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "assistant_id": assistant_id,
        "execute": execute,
    }
    with psycopg.connect(owner_url) as connection:
        connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))


def create_person(graph_env: dict[str, Any], display_name: str) -> UUID:
    person_id = uuid4()
    graph_env["execute"](
        "INSERT INTO persons (id, tenant_id, display_name) VALUES (%s, %s, %s)",
        (person_id, graph_env["tenant_id"], display_name),
    )
    return person_id


def create_conversation(
    graph_env: dict[str, Any],
    *,
    merged: dict[str, Any] | None = None,
    live: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    person_id: UUID | None = None,
    started_at: str = "2026-10-17T10:00:00+00:00",
    ended_at: str | None = "2026-10-17T10:15:00+00:00",
) -> UUID:
    conversation_id = uuid4()
    graph_env["execute"](
        "INSERT INTO conversations "
        "(id, tenant_id, assistant_id, person_id, channel, external_ref, status, "
        "started_at, ended_at, summary, metrics) "
        "VALUES (%s, %s, %s, %s, 'phone', %s, 'completed', %s, %s, %s, %s::jsonb)",
        (
            conversation_id,
            graph_env["tenant_id"],
            graph_env["assistant_id"],
            person_id,
            str(uuid4()),
            started_at,
            ended_at,
            "Discussed account support",
            json.dumps(metrics or {}),
        ),
    )
    for version, data in (("merged", merged), ("live", live)):
        if data is not None:
            graph_env["execute"](
                "INSERT INTO intakes (tenant_id, conversation_id, version, data) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (graph_env["tenant_id"], conversation_id, version, json.dumps(data)),
            )
    return conversation_id
