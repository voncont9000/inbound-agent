import psycopg


async def test_assistant_versions_channels_and_audit(tenant_pair, owner_database_url) -> None:
    first = tenant_pair["first"]
    client = first["client"]
    templates = (await client.get("/templates")).json()
    template = next(item for item in templates if item["name"] == "uk_law_firm")
    config = template["config"]
    created = await client.post(
        "/assistants",
        json={"name": "Assistant", "template": "uk_law_firm", "config": config},
    )
    assert created.status_code == 201, created.text
    assistant_id = created.json()["id"]

    updated_config = {**config, "greeting": "Welcome to the firm."}
    first_update = await client.put(
        f"/assistants/{assistant_id}",
        json={"name": "Assistant", "template": "uk_law_firm", "config": updated_config},
    )
    assert first_update.status_code == 200, first_update.text
    second_update = await client.put(
        f"/assistants/{assistant_id}",
        json={"name": "Updated assistant", "template": "uk_law_firm", "config": updated_config},
    )
    assert second_update.status_code == 200
    versions = await client.get(f"/assistants/{assistant_id}/versions")
    assert [item["version"] for item in versions.json()["items"]] == [3, 2, 1]
    assert (await client.get("/assistant-config/schema")).status_code == 200

    phone = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "020 7946 0000"},
    )
    assert phone.status_code == 201, phone.text
    channel_id = phone.json()["id"]
    assert phone.json()["external_id"] == "+442079460000"
    assert (await client.get(f"/assistants/{assistant_id}/channels")).json()["items"][0][
        "id"
    ] == channel_id
    invalid = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "invalid"},
    )
    assert invalid.status_code == 422

    second = tenant_pair["second"]
    second_templates = (await second["client"].get("/templates")).json()
    other_assistant = await second["client"].post(
        "/assistants",
        json={
            "name": "Other tenant assistant",
            "template": "uk_law_firm",
            "config": next(
                item["config"] for item in second_templates if item["name"] == "uk_law_firm"
            ),
        },
    )
    assert other_assistant.status_code == 201
    duplicate = await second["client"].post(
        f"/assistants/{other_assistant.json()['id']}/channels",
        json={"kind": "phone", "external_id": "+442079460000"},
    )
    assert duplicate.status_code == 409

    with psycopg.connect(owner_database_url) as connection:
        audit = connection.execute(
            "SELECT action, diff FROM audit_log WHERE tenant_id = %s AND target_id = %s "
            "ORDER BY ts, id",
            (first["tenant_id"], assistant_id),
        ).fetchall()
    assert [action for action, _ in audit] == [
        "assistant.created",
        "assistant.updated",
        "assistant.updated",
    ]
    assert audit[1][1]["changes"]["greeting"]["to"] == "Welcome to the firm."

    assert (
        await client.delete(f"/assistants/{assistant_id}/channels/{channel_id}")
    ).status_code == 204
    assert (await client.delete(f"/assistants/{assistant_id}")).status_code == 204
