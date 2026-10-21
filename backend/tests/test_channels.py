from uuid import uuid4

import psycopg

from inbound_api.services import channel_connections


async def create_assistant(client, name: str) -> str:
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    response = await client.post(
        "/assistants",
        json={"name": name, "template": "uk_law_firm", "config": config},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_channel(client, assistant_id: str, kind: str, external_id: str):
    return await client.post(
        "/channels",
        json={
            "assistant_id": assistant_id,
            "kind": kind,
            "external_id": external_id,
            "settings": {},
        },
    )


async def test_workspace_channel_list_includes_connections_for_multiple_assistants(tenant_pair):
    client = tenant_pair["first"]["client"]
    first_assistant = await create_assistant(client, "First assistant")
    second_assistant = await create_assistant(client, "Second assistant")

    phone = await create_channel(client, first_assistant, "phone", "+442079460001")
    gmail = await create_channel(client, second_assistant, "gmail", "second@example.com")
    assert phone.status_code == 201, phone.text
    assert gmail.status_code == 201, gmail.text

    response = await client.get("/channels")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["assistant_id"] for item in items] == [
        second_assistant,
        first_assistant,
    ]
    assert [item["id"] for item in items] == [gmail.json()["id"], phone.json()["id"]]
    assert [item["created_at"] for item in items] == sorted(
        (item["created_at"] for item in items), reverse=True
    )


async def test_workspace_channel_create_validates_assistant_and_normalizes_phone(
    tenant_pair, monkeypatch
):
    first = tenant_pair["first"]
    client = first["client"]
    assistant_id = await create_assistant(client, "Phone assistant")
    phone = await create_channel(client, assistant_id, "phone", "020 7946 0000")

    assert phone.status_code == 201, phone.text
    assert phone.json()["external_id"] == "+442079460000"
    assert phone.json()["assistant_id"] == assistant_id
    duplicate = await create_channel(client, assistant_id, "phone", "+44 20 7946 0000")
    assert duplicate.status_code == 409

    monkeypatch.setattr(
        channel_connections,
        "get_settings",
        lambda: type("Settings", (), {"gmail_mode": "google"})(),
    )
    google_gmail = await create_channel(client, assistant_id, "gmail", "mailbox@example.com")
    assert google_gmail.status_code == 422

    unknown = await create_channel(client, str(uuid4()), "phone", "+442079460002")
    assert unknown.status_code == 404

    second_templates = (await tenant_pair["second"]["client"].get("/templates")).json()
    second_config = next(
        item["config"] for item in second_templates if item["name"] == "uk_law_firm"
    )
    foreign_assistant = await tenant_pair["second"]["client"].post(
        "/assistants",
        json={
            "name": "Other tenant assistant",
            "template": "uk_law_firm",
            "config": second_config,
        },
    )
    assert foreign_assistant.status_code == 201
    foreign = await create_channel(client, foreign_assistant.json()["id"], "phone", "+442079460003")
    assert foreign.status_code == 404


async def test_workspace_channel_reassignment_preserves_gmail_settings(
    tenant_pair, owner_database_url
):
    client = tenant_pair["first"]["client"]
    first_assistant = await create_assistant(client, "First assistant")
    second_assistant = await create_assistant(client, "Second assistant")
    gmail = await create_channel(client, first_assistant, "gmail", "move@example.com")
    assert gmail.status_code == 201, gmail.text
    channel_id = gmail.json()["id"]
    history_id = gmail.json()["settings"]["history_id"]

    updated = await client.patch(
        f"/channels/{channel_id}",
        json={
            "assistant_id": second_assistant,
            "include_rules": [{"field": "to", "contains": "@example.test"}],
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["assistant_id"] == second_assistant
    assert updated.json()["settings"]["history_id"] == history_id
    assert updated.json()["settings"]["include_rules"] == [
        {"field": "to", "contains": "@example.test"}
    ]
    assert (await client.get(f"/assistants/{first_assistant}/channels")).json()["items"] == []
    assert [item["id"] for item in (await client.get("/channels")).json()["items"]] == [channel_id]
    with psycopg.connect(owner_database_url) as connection:
        updated_audit = connection.execute(
            "SELECT action FROM audit_log WHERE tenant_id = %s AND target_id = %s "
            "AND action = 'channel.updated'",
            (tenant_pair["first"]["tenant_id"], channel_id),
        ).fetchone()
    assert updated_audit == ("channel.updated",)

    scoped_update = await client.patch(
        f"/assistants/{second_assistant}/channels/{channel_id}",
        json={"assistant_id": first_assistant},
    )
    assert scoped_update.status_code == 422


async def test_workspace_channel_reassignment_to_foreign_tenant_is_not_found(tenant_pair):
    first = tenant_pair["first"]
    client = first["client"]
    assistant_id = await create_assistant(client, "First assistant")
    phone = await create_channel(client, assistant_id, "phone", "+442079460004")
    assert phone.status_code == 201, phone.text

    second = tenant_pair["second"]
    foreign_assistant = await create_assistant(second["client"], "Foreign assistant")
    response = await client.patch(
        f"/channels/{phone.json()['id']}",
        json={"assistant_id": foreign_assistant},
    )

    assert response.status_code == 404


async def test_workspace_channel_delete_audits_channel(tenant_pair, owner_database_url):
    first = tenant_pair["first"]
    client = first["client"]
    assistant_id = await create_assistant(client, "Phone assistant")
    phone = await create_channel(client, assistant_id, "phone", "+442079460005")
    channel_id = phone.json()["id"]

    deleted = await client.delete(f"/channels/{channel_id}")

    assert deleted.status_code == 204
    assert (await client.get("/channels")).json()["items"] == []
    with psycopg.connect(owner_database_url) as connection:
        actions = connection.execute(
            "SELECT action FROM audit_log WHERE tenant_id = %s AND target_id = %s ORDER BY ts, id",
            (first["tenant_id"], channel_id),
        ).fetchall()
    assert [action for (action,) in actions] == ["channel.created", "channel.deleted"]
