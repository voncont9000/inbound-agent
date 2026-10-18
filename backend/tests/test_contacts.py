from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from conftest import create_conversation, create_person

from inbound_api.main import app
from inbound_api.routers import contacts


def _add_identity(graph_env, person_id, identity_type, value):
    identity_id = uuid4()
    graph_env["execute"](
        "INSERT INTO identities "
        "(id, tenant_id, person_id, type, value_normalized, confidence, source) "
        "VALUES (%s, %s, %s, %s, %s, 1.0, 'test')",
        (identity_id, graph_env["tenant_id"], person_id, identity_type, value),
    )
    return identity_id


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def _override_auth(graph_env):
    app.dependency_overrides[contacts.current_user] = lambda: SimpleNamespace(
        id=graph_env["user_id"]
    )
    app.dependency_overrides[contacts.current_tenant] = lambda: graph_env["tenant_id"]


@pytest.mark.asyncio
async def test_contact_search_cursor_detail_and_tenant_404(graph_env):
    contact_a = create_person(graph_env, "Alice Example")
    contact_b = create_person(graph_env, "Bob Example")
    _add_identity(graph_env, contact_a, "phone", "+447700900128")
    _add_identity(graph_env, contact_a, "email", "alice@example.test")
    _add_identity(graph_env, contact_b, "email", "bob@example.test")
    foreign_tenant, foreign_person = uuid4(), uuid4()
    graph_env["execute"](
        "INSERT INTO tenants (id, name) VALUES (%s, 'Contacts foreign')",
        (foreign_tenant,),
    )
    graph_env["execute"](
        "INSERT INTO persons (id, tenant_id, display_name) VALUES (%s, %s, 'Foreign Contact')",
        (foreign_person, foreign_tenant),
    )
    _override_auth(graph_env)
    try:
        async with _client() as client:
            by_name = await client.get("/contacts", params={"q": "Alice"})
            assert by_name.status_code == 200
            assert [item["id"] for item in by_name.json()["items"]] == [str(contact_a)]
            by_phone = await client.get("/contacts", params={"q": "07700 900128"})
            assert [item["id"] for item in by_phone.json()["items"]] == [str(contact_a)]
            by_email = await client.get("/contacts", params={"q": "alice@example"})
            assert [item["id"] for item in by_email.json()["items"]] == [str(contact_a)]

            first_page = await client.get("/contacts", params={"limit": 1})
            assert first_page.status_code == 200
            first = first_page.json()
            assert len(first["items"]) == 1
            assert first["next_cursor"]
            second_page = await client.get(
                "/contacts",
                params={"limit": 1, "cursor": first["next_cursor"]},
            )
            assert second_page.status_code == 200
            second = second_page.json()
            assert len(second["items"]) == 1
            assert first["items"][0]["id"] != second["items"][0]["id"]
            assert second["next_cursor"] is None
            invalid_cursor = await client.get("/contacts", params={"cursor": "bad-cursor"})
            assert invalid_cursor.status_code == 400

            detail = await client.get(f"/contacts/{contact_a}")
            assert detail.status_code == 200
            assert detail.json()["display_name"] == "Alice Example"
            assert len(detail.json()["identities"]) == 2
            hidden = await client.get(f"/contacts/{foreign_person}")
            assert hidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        graph_env["execute"]("DELETE FROM tenants WHERE id = %s", (foreign_tenant,))


@pytest.mark.asyncio
async def test_contact_merge_undo_split_and_error_codes(graph_env):
    survivor_id = create_person(graph_env, "Primary Contact")
    merged_id = create_person(graph_env, "Other Contact")
    _add_identity(graph_env, merged_id, "email", "other@example.test")
    split_identity = _add_identity(graph_env, survivor_id, "phone", "+447700900129")
    conversation_id = create_conversation(graph_env, person_id=survivor_id)
    graph_env["execute"](
        "INSERT INTO edges "
        "(tenant_id, src_type, src_id, rel, dst_type, dst_id, valid_from) "
        "VALUES (%s, 'conversation', %s, 'via_identity', 'identity', %s, now())",
        (graph_env["tenant_id"], conversation_id, split_identity),
    )
    _override_auth(graph_env)
    try:
        async with _client() as client:
            merged = await client.post(
                f"/contacts/{survivor_id}/merge",
                json={"other_id": str(merged_id)},
            )
            assert merged.status_code == 200
            assert merged.json() == {
                "survivor_id": str(survivor_id),
                "merged_id": str(merged_id),
            }
            conflict = await client.post(
                f"/contacts/{survivor_id}/merge",
                json={"other_id": str(merged_id)},
            )
            assert conflict.status_code == 409
            undo = await client.post(f"/contacts/{survivor_id}/merge/undo")
            assert undo.status_code == 200
            assert undo.json()["merged_id"] == str(merged_id)

            split = await client.post(
                f"/contacts/{survivor_id}/split",
                json={"identity_ids": [str(split_identity)]},
            )
            assert split.status_code == 200
            assert split.json()["person_id"] == str(survivor_id)
            split_person_id = split.json()["split_person_id"]
            assert split_person_id != str(survivor_id)
            assert (
                await client.post(
                    f"/contacts/{survivor_id}/split",
                    json={"identity_ids": []},
                )
            ).status_code == 400
            assert (
                await client.post(
                    f"/contacts/{survivor_id}/merge",
                    json={"other_id": str(uuid4())},
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/contacts/{survivor_id}/merge",
                    json={"other_id": str(survivor_id)},
                )
            ).status_code == 400
            assert (await client.post(f"/contacts/{uuid4()}/merge/undo")).status_code == 404
            assert (await client.post(f"/contacts/{survivor_id}/merge/undo")).status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_contacts_auth_adapter_returns_501_without_overrides():
    app.dependency_overrides.clear()
    async with _client() as client:
        response = await client.get("/contacts")
    assert response.status_code == 501
