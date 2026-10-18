from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import psycopg
import pytest

from inbound_api.db import tenant_session
from inbound_api.models import KnowledgeDigest
from inbound_api.settings import get_settings
from inbound_api.web_call_tokens import issue, verify


def test_web_call_token_round_trip_tamper_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    from inbound_api import web_call_tokens

    tenant_id = UUID("10000000-0000-4000-8000-000000000001")
    assistant_id = UUID("20000000-0000-4000-8000-000000000001")
    token, _ = issue(tenant_id, assistant_id)

    assert verify(token) == (tenant_id, assistant_id)
    with pytest.raises(ValueError):
        verify("not-a-token")
    payload, signature = token.split(".")
    changed_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    with pytest.raises(ValueError):
        verify(f"{changed_payload}.{signature}")
    changed_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(ValueError):
        verify(f"{payload}.{changed_signature}")

    monkeypatch.setattr(web_call_tokens.time, "time", lambda: 1_000)
    expired, expires_at = issue(tenant_id, assistant_id, ttl=1)
    assert expires_at == 1_001
    monkeypatch.setattr(web_call_tokens.time, "time", lambda: 1_002)
    with pytest.raises(ValueError):
        verify(expired)


@pytest.mark.asyncio
async def test_test_call_endpoint_owner_staff_unconfigured_and_other_tenant(
    tenant_pair,
    owner_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tenant_pair["first"]
    second = tenant_pair["second"]
    client = first["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "web_call_base_url", None)
    template = next(
        item for item in (await client.get("/templates")).json() if item["name"] == "uk_law_firm"
    )
    assistant = await client.post(
        "/assistants",
        json={
            "name": "Practice assistant",
            "template": template["name"],
            "config": template["config"],
        },
    )
    assert assistant.status_code == 201, assistant.text
    assistant_id = assistant.json()["id"]

    unconfigured = await client.post(f"/assistants/{assistant_id}/test-calls")
    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"] == "Test calls are not configured"

    other_template = next(
        item
        for item in (await second["client"].get("/templates")).json()
        if item["name"] == "uk_law_firm"
    )
    other_assistant = await second["client"].post(
        "/assistants",
        json={
            "name": "Other tenant assistant",
            "template": other_template["name"],
            "config": other_template["config"],
        },
    )
    assert other_assistant.status_code == 201
    monkeypatch.setattr(settings, "web_call_base_url", "https://practice.example/prefix/")

    missing = await client.post(f"/assistants/{other_assistant.json()['id']}/test-calls")
    assert missing.status_code == 404

    with psycopg.connect(owner_database_url) as connection:
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, 'staff')",
            (first["tenant_id"], second["user_id"]),
        )
    staff_attempt = await second["client"].post(
        f"/assistants/{assistant_id}/test-calls",
        headers={"X-Tenant-Id": first["tenant_id"]},
    )
    assert staff_attempt.status_code == 403

    created = await client.post(f"/assistants/{assistant_id}/test-calls")
    assert created.status_code == 200, created.text
    body = created.json()
    parsed = urlsplit(body["url"])
    token = parse_qs(parsed.query)["call_token"][0]
    assert parsed.scheme == "https"
    assert parsed.netloc == "practice.example"
    assert parsed.path == "/prefix/"
    assert verify(token) == (UUID(first["tenant_id"]), UUID(assistant_id))
    assert isinstance(body["expires_at"], int)
    with psycopg.connect(owner_database_url) as connection:
        actions = connection.execute(
            "SELECT action FROM audit_log WHERE target_id = %s",
            (assistant_id,),
        ).fetchall()
    assert ("assistant.test_call_created",) in actions


@pytest.mark.asyncio
async def test_web_route_and_start_call_use_token_tenant_and_assistant(
    tenant_pair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    template = next(
        item for item in (await client.get("/templates")).json() if item["name"] == "uk_law_firm"
    )
    created = await client.post(
        "/assistants",
        json={
            "name": "Token assistant",
            "template": template["name"],
            "config": template["config"],
        },
    )
    assert created.status_code == 201, created.text
    assistant_id = UUID(created.json()["id"])
    tenant_id = UUID(account["tenant_id"])
    async with tenant_session(tenant_id) as session:
        session.add(
            KnowledgeDigest(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                content="Assistant-specific knowledge digest.",
            )
        )

    token, _ = issue(tenant_id, assistant_id)
    service_headers = {"X-Service-Token": get_settings().voice_service_token}
    routed = await client.get(
        "/internal/voice/web-route",
        params={"token": token},
        headers=service_headers,
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["assistant_id"] == str(assistant_id)
    assert routed.json()["tenant_id"] == str(tenant_id)
    assert routed.json()["config"]["business_name"] == created.json()["config"]["business_name"]
    assert routed.json()["digest"] == "Assistant-specific knowledge digest."
    assert routed.json()["context_card"] is None
    bad_route = await client.get(
        "/internal/voice/web-route",
        params={"token": token + "tampered"},
        headers=service_headers,
    )
    assert bad_route.status_code == 401

    started = await client.post(
        "/internal/voice/calls",
        headers=service_headers,
        json={
            "to_number": "",
            "from_number": None,
            "external_call_id": "web-call-one",
            "channel": "web",
            "started_at": datetime.now(UTC).isoformat(),
            "web_call_token": token,
        },
    )
    assert started.status_code == 201, started.text
    conversation_id = started.json()["conversation_id"]
    second_started = await client.post(
        "/internal/voice/calls",
        headers=service_headers,
        json={
            "to_number": "",
            "from_number": None,
            "external_call_id": "web-call-two",
            "channel": "web",
            "started_at": datetime.now(UTC).isoformat(),
            "web_call_token": token,
        },
    )
    assert second_started.status_code == 201, second_started.text
    second_conversation_id = second_started.json()["conversation_id"]
    assert second_conversation_id != conversation_id

    for current_id, external_ref in (
        (conversation_id, "web-call-one"),
        (second_conversation_id, "web-call-two"),
    ):
        detail = await client.get(f"/conversations/{current_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["channel"] == "web"
        assert detail.json()["assistant_id"] == str(assistant_id)
        assert detail.json()["external_ref"] == external_ref
        assert detail.json()["external_ref"] != token
        assert detail.json()["metrics"]["test_call"] is True
        assert detail.json()["test_call"] is True

    summaries = await client.get("/conversations", params={"channel": "web"})
    assert summaries.status_code == 200, summaries.text
    assert {item["external_ref"]: item["test_call"] for item in summaries.json()["items"]} == {
        "web-call-one": True,
        "web-call-two": True,
    }
