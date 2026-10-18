import hashlib
from uuid import uuid4

import psycopg
from httpx import AsyncClient

from inbound_api.settings import get_settings


async def test_signup_cookie_bearer_login_and_logout(tenant_pair, owner_database_url) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    token = client.cookies["inbound_session"]
    assert token
    with psycopg.connect(owner_database_url) as connection:
        stored_tokens = connection.execute(
            "SELECT token_sha256 FROM user_sessions WHERE user_id = %s",
            (account["user_id"],),
        ).fetchall()
    assert stored_tokens == [(hashlib.sha256(token.encode()).hexdigest(),)]

    response_cookie = client.cookies.jar
    cookie = next(item for item in response_cookie if item.name == "inbound_session")
    assert "HttpOnly" in cookie._rest
    assert cookie._rest["SameSite"] == "lax"
    me = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["tenant"]["id"] == account["tenant_id"]

    wrong_password = await client.post(
        "/auth/login",
        json={"email": account["email"], "password": "incorrect-password"},
    )
    assert wrong_password.status_code == 401
    login = await client.post(
        "/auth/login",
        json={"email": account["email"], "password": "valid-platform-password"},
    )
    assert login.status_code == 200
    logged_in_token = login.json()["access_token"]
    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert (
        await client.get("/me", headers={"Authorization": f"Bearer {logged_in_token}"})
    ).status_code == 401


async def test_unauthenticated_request_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.get("/me")
    assert response.status_code == 401


async def test_tenant_selection_and_staff_role_policy(tenant_pair, owner_database_url) -> None:
    first = tenant_pair["first"]
    second = tenant_pair["second"]
    templates = await first["client"].get("/templates")
    assert templates.status_code == 200
    template = next(item for item in templates.json() if item["name"] == "uk_law_firm")
    created_assistant = await first["client"].post(
        "/assistants",
        json={
            "name": "First tenant assistant",
            "template": "uk_law_firm",
            "config": template["config"],
        },
    )
    assert created_assistant.status_code == 201, created_assistant.text
    assistant_id = created_assistant.json()["id"]
    assert (await second["client"].get(f"/assistants/{assistant_id}")).status_code == 404

    with psycopg.connect(owner_database_url) as connection:
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'staff')",
            (second["tenant_id"], first["user_id"]),
        )
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'staff')",
            (first["tenant_id"], second["user_id"]),
        )

    no_selection = await first["client"].get("/me")
    assert no_selection.status_code == 400
    selected = await first["client"].get("/me", headers={"X-Tenant-Id": second["tenant_id"]})
    assert selected.status_code == 200
    assert selected.json()["tenant"]["role"] == "staff"
    malformed = await first["client"].get("/me", headers={"X-Tenant-Id": "not-a-uuid"})
    assert malformed.status_code == 400
    non_member = await first["client"].get(
        "/me", headers={"X-Tenant-Id": "00000000-0000-0000-0000-000000000001"}
    )
    assert non_member.status_code == 403

    staff_client = second["client"]
    tenant_headers = {"X-Tenant-Id": first["tenant_id"]}
    templates = await staff_client.get("/templates", headers=tenant_headers)
    assert templates.status_code == 200
    assistants = await staff_client.get("/assistants", headers=tenant_headers)
    assert assistants.status_code == 200
    assert assistants.json()["items"][0]["id"] == assistant_id
    create = await staff_client.post(
        "/assistants",
        headers=tenant_headers,
        json={"name": "Forbidden", "config": templates.json()[0]["config"]},
    )
    assert create.status_code == 403


async def test_staff_role_policy_covers_owner_routes_and_reviews(
    tenant_pair, owner_database_url
) -> None:
    owner = tenant_pair["first"]
    staff = tenant_pair["second"]
    owner_client = owner["client"]
    templates = (await owner_client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant_response = await owner_client.post(
        "/assistants",
        json={"name": "Staff policy assistant", "template": "uk_law_firm", "config": config},
    )
    assert assistant_response.status_code == 201, assistant_response.text
    assistant_id = assistant_response.json()["id"]
    channel_response = await owner_client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "020 7946 0300"},
    )
    assert channel_response.status_code == 201, channel_response.text
    channel_id = channel_response.json()["id"]

    service_headers = {"X-Service-Token": get_settings().voice_service_token}
    call_id = str(uuid4())
    start_payload = {
        "to_number": "020 7946 0300",
        "from_number": "020 7946 0301",
        "external_call_id": call_id,
        "channel": "phone",
        "started_at": "2026-03-01T12:00:00Z",
    }
    started = await owner_client.post(
        "/internal/voice/calls", json=start_payload, headers=service_headers
    )
    assert started.status_code == 201, started.text
    conversation_id = started.json()["conversation_id"]
    reported = await owner_client.post(
        f"/internal/voice/calls/{conversation_id}/report",
        headers=service_headers,
        json={
            "external_call_id": call_id,
            "ended_at": "2026-03-01T12:05:00Z",
            "end_reason": "caller_finished",
            "transcript": [],
            "live_intake": {"fields": {}},
            "post_call_intake": {"fields": {}},
            "merged_intake": {
                "service": "conveyancing",
                "fields": {
                    "email": {
                        "value": "old@example.com",
                        "status": "model",
                        "confidence": 0.2,
                        "needs_review": True,
                    }
                },
                "needs_review": ["email"],
            },
            "tasks": [],
            "knowledge_gaps": [],
            "metrics": {},
        },
    )
    assert reported.status_code == 202, reported.text

    with psycopg.connect(owner_database_url) as connection:
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'staff')",
            (owner["tenant_id"], staff["user_id"]),
        )
    client = staff["client"]
    headers = {"X-Tenant-Id": owner["tenant_id"]}

    for path in (
        "/me",
        "/templates",
        "/assistant-config/schema",
        "/assistants",
        "/conversations",
        f"/conversations/{conversation_id}",
        "/tasks",
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, f"GET {path}: {response.text}"

    assistant_payload = {
        "name": "Forbidden update",
        "template": "uk_law_firm",
        "config": config,
    }
    forbidden_requests = [
        ("POST", "/assistants", {"name": "Forbidden", "config": config}),
        ("PUT", f"/assistants/{assistant_id}", assistant_payload),
        ("DELETE", f"/assistants/{assistant_id}", None),
        (
            "POST",
            f"/assistants/{assistant_id}/knowledge/sources",
            {"kind": "url", "uri": "https://example.com/staff"},
        ),
        ("POST", f"/assistants/{assistant_id}/knowledge/import", None),
        (
            "PUT",
            f"/assistants/{assistant_id}/knowledge/digest",
            {"content": "Forbidden edit"},
        ),
        ("GET", f"/assistants/{assistant_id}/channels", None),
        (
            "POST",
            f"/assistants/{assistant_id}/channels",
            {"kind": "web", "external_id": "forbidden-web"},
        ),
        (
            "DELETE",
            f"/assistants/{assistant_id}/channels/{channel_id}",
            None,
        ),
    ]
    for method, path, payload in forbidden_requests:
        response = await client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, f"{method} {path}: {response.text}"

    review = await client.post(
        f"/conversations/{conversation_id}/reviews",
        headers=headers,
        json={"field": "email", "resolved_value": "confirmed@example.com"},
    )
    assert review.status_code == 201, review.text
