import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest_asyncio

from inbound_api.db import engine


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


OWNER_DATABASE_URL = _psycopg_url(
    os.environ.get(
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound",
    )
)


@pytest_asyncio.fixture
async def graph_env() -> AsyncIterator[dict[str, Any]]:
    await engine.dispose()
    tenant_id, user_id, assistant_id = uuid4(), uuid4(), uuid4()
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
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
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
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchall() if cursor.description else []

    yield {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "assistant_id": assistant_id,
        "execute": execute,
    }
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
    await engine.dispose()


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
