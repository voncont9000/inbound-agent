from uuid import UUID

from sqlalchemy import select

from inbound_api.db import tenant_session
from inbound_api.models import Job, KnowledgeGap, Task
from inbound_api.settings import get_settings


async def test_tenant_id_routes_and_lists_are_isolated(tenant_pair, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "knowledge_allow_private_urls", True)
    first = tenant_pair["first"]
    second = tenant_pair["second"]
    owner_client = first["client"]
    other_client = second["client"]

    templates = (await owner_client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await owner_client.post(
        "/assistants",
        json={"name": "Tenant A assistant", "template": "uk_law_firm", "config": config},
    )
    assert assistant.status_code == 201, assistant.text
    assistant_id = assistant.json()["id"]
    channel = await owner_client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "020 7946 0000"},
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    source = await owner_client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        json={"kind": "url", "uri": "https://example.com/tenant-a"},
    )
    assert source.status_code == 201, source.text

    service_headers = {"X-Service-Token": get_settings().voice_service_token}
    start_payload = {
        "to_number": "020 7946 0000",
        "from_number": "020 7946 0002",
        "external_call_id": "tenant-isolation-call",
        "channel": "phone",
        "started_at": "2026-03-01T12:00:00Z",
    }
    started = await owner_client.post(
        "/internal/voice/calls", json=start_payload, headers=service_headers
    )
    assert started.status_code == 201, started.text
    conversation_id = started.json()["conversation_id"]
    report = await owner_client.post(
        f"/internal/voice/calls/{conversation_id}/report",
        headers=service_headers,
        json={
            "external_call_id": start_payload["external_call_id"],
            "ended_at": "2026-03-01T12:05:00Z",
            "end_reason": "caller_finished",
            "transcript": [],
            "live_intake": {"fields": {}},
            "post_call_intake": {"fields": {}},
            "merged_intake": {
                "service": "conveyancing",
                "fields": {
                    "email": {
                        "value": None,
                        "status": "model",
                        "confidence": 0.2,
                        "needs_review": True,
                    }
                },
                "needs_review": ["email"],
            },
            "tasks": [{"type": "callback", "source": "live", "description": "Call back"}],
            "knowledge_gaps": [{"question": "When will it complete?", "evidence": "Caller asked."}],
            "metrics": {},
        },
    )
    assert report.status_code == 202, report.text

    async with tenant_session(UUID(first["tenant_id"])) as session:
        task_id = await session.scalar(
            select(Task.id).where(Task.conversation_id == UUID(conversation_id)).limit(1)
        )
        gap_id = await session.scalar(
            select(KnowledgeGap.id)
            .where(KnowledgeGap.conversation_id == UUID(conversation_id))
            .limit(1)
        )
        job_id = await session.scalar(
            select(Job.id).where(
                Job.kind == "graph_ingest",
                Job.payload == {"conversation_id": conversation_id},
            )
        )
    assert task_id is not None
    assert gap_id is not None
    assert job_id is not None

    update_payload = {"name": "Changed", "template": "uk_law_firm", "config": config}
    requests = [
        ("GET", f"/assistants/{assistant_id}", None),
        ("PUT", f"/assistants/{assistant_id}", update_payload),
        ("DELETE", f"/assistants/{assistant_id}", None),
        ("GET", f"/assistants/{assistant_id}/versions", None),
        ("GET", f"/assistants/{assistant_id}/channels", None),
        (
            "POST",
            f"/assistants/{assistant_id}/channels",
            {"kind": "web", "external_id": "tenant-a-web"},
        ),
        (
            "DELETE",
            f"/assistants/{assistant_id}/channels/{channel_id}",
            None,
        ),
        ("GET", f"/assistants/{assistant_id}/knowledge/sources", None),
        (
            "POST",
            f"/assistants/{assistant_id}/knowledge/sources",
            {"kind": "url", "uri": "https://example.com/other"},
        ),
        ("POST", f"/assistants/{assistant_id}/knowledge/import", None),
        ("GET", f"/assistants/{assistant_id}/knowledge/digest", None),
        (
            "PUT",
            f"/assistants/{assistant_id}/knowledge/digest",
            {"content": "Tenant A digest"},
        ),
        ("GET", f"/conversations/{conversation_id}", None),
        (
            "POST",
            f"/conversations/{conversation_id}/reviews",
            {"field": "email", "resolved_value": "a@example.com"},
        ),
        ("PATCH", f"/tasks/{task_id}", {"status": "done"}),
        ("PATCH", f"/knowledge-gaps/{gap_id}", {"status": "answered"}),
        ("GET", f"/jobs/{job_id}", None),
    ]
    for method, path, payload in requests:
        response = await other_client.request(method, path, json=payload)
        assert response.status_code == 404, f"{method} {path}: {response.text}"

    list_responses = [
        await other_client.get("/assistants"),
        await other_client.get("/conversations"),
        await other_client.get("/tasks"),
        await other_client.get("/knowledge-gaps"),
    ]
    for response in list_responses:
        assert response.status_code == 200, response.text
        ids = {item["id"] for item in response.json()["items"]}
        assert assistant_id not in ids
        assert conversation_id not in ids
        assert str(task_id) not in ids
        assert str(gap_id) not in ids
