import psycopg


async def test_templates_include_metadata_in_configured_order(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]

    response = await client.get("/templates")

    assert response.status_code == 200
    templates = response.json()
    assert len(templates) == 6
    assert templates[0]["name"] == "blank"
    assert templates[0]["label"] == "Blank"
    assert templates[0]["sets_up"]
    law_template = next(item for item in templates if item["name"] == "uk_law_firm")
    assert law_template["label"] == "UK B2C law firm"
    assert law_template["sets_up"]
    assert law_template["sample_business_name"] == "Demo & Co Solicitors (sample law firm)"


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
    config_only_update = await client.put(
        f"/assistants/{assistant_id}",
        json={"template": "uk_law_firm", "config": updated_config},
    )
    assert config_only_update.status_code == 200
    assert config_only_update.json()["name"] == "Assistant"
    second_update = await client.put(
        f"/assistants/{assistant_id}",
        json={"name": "Updated assistant", "template": "uk_law_firm", "config": updated_config},
    )
    assert second_update.status_code == 200
    versions = await client.get(f"/assistants/{assistant_id}/versions")
    assert [item["version"] for item in versions.json()["items"]] == [4, 3, 2, 1]
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
        "assistant.updated",
    ]
    assert audit[1][1]["changes"]["greeting"]["to"] == "Welcome to the firm."
    assert "name" not in audit[2][1]["changes"]

    assert (
        await client.delete(f"/assistants/{assistant_id}/channels/{channel_id}")
    ).status_code == 204
    assert (await client.delete(f"/assistants/{assistant_id}")).status_code == 204


async def test_channel_include_rules_update(tenant_pair, owner_database_url) -> None:
    first = tenant_pair["first"]
    client = first["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    created = await client.post(
        "/assistants",
        json={"name": "Filter assistant", "template": "uk_law_firm", "config": config},
    )
    assert created.status_code == 201, created.text
    assistant_id = created.json()["id"]
    phone = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "+442079460000"},
    )
    assert phone.status_code == 201, phone.text
    gmail = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "gmail", "external_id": "inbox@example.com"},
    )
    assert gmail.status_code == 201, gmail.text
    channel_id = gmail.json()["id"]
    history_id = gmail.json()["settings"]["history_id"]
    rules = [{"field": "to", "contains": " @vinci-labs.tech "}]

    updated = await client.patch(
        f"/assistants/{assistant_id}/channels/{channel_id}",
        json={"include_rules": rules},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["include_rules"] == [
        {"field": "to", "contains": "@vinci-labs.tech"}
    ]
    assert updated.json()["settings"]["history_id"] == history_id
    with psycopg.connect(owner_database_url) as connection:
        audit_action = connection.execute(
            "SELECT action FROM audit_log WHERE tenant_id::text = %s "
            "AND target_id::text = %s AND action = 'channel.updated' LIMIT 1",
            (str(first["tenant_id"]), channel_id),
        ).fetchone()
    assert audit_action == ("channel.updated",)

    assert (
        await client.patch(
            f"/assistants/{assistant_id}/channels/{phone.json()['id']}",
            json={"include_rules": []},
        )
    ).status_code == 422
    assert (
        await client.patch(
            f"/assistants/{assistant_id}/channels/{phone.json()['id']}",
            json={"include_rules": [{"field": "to", "contains": "  "}]},
        )
    ).status_code == 422
    assert (
        await client.patch(
            f"/assistants/{assistant_id}/channels/{channel_id}",
            json={"include_rules": [{"field": "bad", "contains": "domain"}]},
        )
    ).status_code == 422

    other_assistant = await client.post(
        "/assistants",
        json={"name": "Other assistant", "template": "uk_law_firm", "config": config},
    )
    assert other_assistant.status_code == 201, other_assistant.text
    assert (
        await client.patch(
            f"/assistants/{other_assistant.json()['id']}/channels/{channel_id}",
            json={"include_rules": []},
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"/assistants/00000000-0000-4000-8000-000000000001/channels/{channel_id}",
            json={"include_rules": []},
        )
    ).status_code == 404
