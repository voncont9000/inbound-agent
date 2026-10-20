from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from conftest import create_conversation, create_person

from inbound_api import auth
from inbound_api.auth import TenantContext
from inbound_api.main import app
from inbound_api.models import MembershipRole


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _override_auth(graph_env, role=MembershipRole.OWNER):
    app.dependency_overrides[auth.current_tenant] = lambda: TenantContext(
        user=SimpleNamespace(id=graph_env["user_id"]),
        tenant_id=graph_env["tenant_id"],
        role=role,
    )


def _fetch_one(graph_env, query, params):
    return graph_env["execute"](query, params)[0][0]


def _count(graph_env, table, column, value):
    return _fetch_one(graph_env, f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,))


def _create_task(graph_env, conversation_id, person_id):
    task_id = uuid4()
    graph_env["execute"](
        "INSERT INTO tasks (id, tenant_id, conversation_id, person_id, kind, source) "
        "VALUES (%s, %s, %s, %s, 'callback', 'post_call')",
        (task_id, graph_env["tenant_id"], conversation_id, person_id),
    )
    return task_id


@pytest.mark.asyncio
async def test_bulk_delete_contacts_keeps_conversations_and_tasks(graph_env):
    person_id = create_person(graph_env, "Alice Example")
    merged_id = create_person(graph_env, "Alice E.")
    graph_env["execute"](
        "UPDATE persons SET merged_into_id = %s WHERE id = %s", (person_id, merged_id)
    )
    conversation_id = create_conversation(graph_env, person_id=person_id)
    task_id = _create_task(graph_env, conversation_id, person_id)
    _override_auth(graph_env)
    try:
        async with _client() as client:
            response = await client.post(
                "/contacts/bulk-delete", json={"ids": [str(person_id), str(uuid4())]}
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1}
    assert _count(graph_env, "persons", "id", person_id) == 0
    assert (
        _fetch_one(graph_env, "SELECT merged_into_id FROM persons WHERE id = %s", (merged_id,))
        is None
    )
    assert (
        _fetch_one(
            graph_env, "SELECT person_id FROM conversations WHERE id = %s", (conversation_id,)
        )
        is None
    )
    assert _fetch_one(graph_env, "SELECT person_id FROM tasks WHERE id = %s", (task_id,)) is None
    assert _count(graph_env, "audit_log", "target_id", person_id) == 1


@pytest.mark.asyncio
async def test_bulk_delete_tasks_requires_owner(graph_env):
    conversation_id = create_conversation(graph_env)
    task_ids = [_create_task(graph_env, conversation_id, None) for _ in range(2)]
    _override_auth(graph_env, MembershipRole.STAFF)
    try:
        async with _client() as client:
            forbidden = await client.post("/tasks/bulk-delete", json={"ids": [str(task_ids[0])]})
            assert forbidden.status_code == 403, forbidden.text
        _override_auth(graph_env)
        async with _client() as client:
            response = await client.post(
                "/tasks/bulk-delete", json={"ids": [str(task_id) for task_id in task_ids]}
            )
            empty = await client.post("/tasks/bulk-delete", json={"ids": []})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 2}
    assert empty.status_code == 422
    for task_id in task_ids:
        assert _count(graph_env, "tasks", "id", task_id) == 0
    assert _count(graph_env, "conversations", "id", conversation_id) == 1
