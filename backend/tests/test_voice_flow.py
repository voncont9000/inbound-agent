from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from inbound_api.db import tenant_session
from inbound_api.jobs import run_next_job
from inbound_api.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Intake,
    IntakeVersion,
    Job,
    KnowledgeGap,
    Message,
    Person,
    Task,
)
from inbound_api.services import job_handlers as _job_handlers  # noqa: F401
from inbound_api.services.review import merged_needs_review
from inbound_api.settings import get_settings


async def test_voice_call_report_review_and_job_are_idempotent(tenant_pair) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Voice assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    channel = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": "020 7946 0000"},
    )
    assert channel.status_code == 201

    service_headers = {"X-Service-Token": get_settings().voice_service_token}
    wrong_token = await client.get(
        "/internal/voice/route",
        params={"to": "020 7946 0000"},
        headers={"X-Service-Token": "wrong"},
    )
    assert wrong_token.status_code == 401
    unknown = await client.get(
        "/internal/voice/route",
        params={"to": "020 7946 0001"},
        headers=service_headers,
    )
    assert unknown.status_code == 404
    route = await client.get(
        "/internal/voice/route",
        params={"to": "020 7946 0000", "from": "020 7946 0002"},
        headers=service_headers,
    )
    assert route.status_code == 200, route.text
    assert route.json()["assistant_id"] == assistant_id
    assert route.json()["context_card"] is None

    started_at = "2026-03-01T12:00:00Z"
    start_payload = {
        "to_number": "020 7946 0000",
        "from_number": "07700 900456",
        "external_call_id": "voice-flow-call",
        "channel": "phone",
        "started_at": started_at,
    }
    started = await client.post(
        "/internal/voice/calls", json=start_payload, headers=service_headers
    )
    repeated_start = await client.post(
        "/internal/voice/calls", json=start_payload, headers=service_headers
    )
    assert started.status_code == 201
    conversation_id = started.json()["conversation_id"]
    assert repeated_start.json()["conversation_id"] == conversation_id

    report = {
        "external_call_id": "voice-flow-call",
        "ended_at": "2026-03-01T12:05:00Z",
        "end_reason": "caller_finished",
        "summary": "The caller requested a return call about a service enquiry.",
        "transcript": [
            {
                "role": "caller",
                "text": "Please call me back.",
                "timestamp": "2026-03-01T12:01:00Z",
            }
        ],
        "live_intake": {"fields": {}},
        "post_call_intake": {"fields": {}},
        "merged_intake": {
            "service": "conveyancing",
            "fields": {
                "email": {
                    "value": "old@example.test",
                    "status": "model",
                    "confidence": 0.3,
                    "needs_review": True,
                    "sources": {"model": {"value": "old@example.test"}},
                }
            },
            "needs_review": ["email"],
        },
        "tasks": [
            {
                "type": "callback",
                "source": "live",
                "description": "Return the call",
                "due_at": "2026-03-02T10:00:00Z",
            },
            {"type": "task", "source": "post_call", "description": "Check the details"},
            {"type": "end_call", "source": "goodbye_detected"},
            {
                "type": "knowledge_gap",
                "question": "What is the completion estimate?",
                "evidence": "The caller asked for a completion estimate.",
            },
        ],
        "knowledge_gaps": [
            {
                "question": "What is the completion estimate?",
                "evidence": "The caller asked for a completion estimate.",
            }
        ],
        "metrics": {"duration_seconds": 300},
        "estimated_cost": {"currency": "USD", "amount": 0.02},
    }
    report_response = await client.post(
        f"/internal/voice/calls/{conversation_id}/report",
        json=report,
        headers=service_headers,
    )
    assert report_response.status_code == 202, report_response.text
    async with tenant_session(UUID(account["tenant_id"])) as session:
        conversation = await session.get(Conversation, UUID(conversation_id))
        person = Person(tenant_id=UUID(account["tenant_id"]), display_name="Caller Example")
        session.add(person)
        await session.flush()
        conversation.person_id = person.id
        persisted_tasks = list(
            await session.scalars(select(Task).where(Task.conversation_id == UUID(conversation_id)))
        )
    assert conversation is not None
    assistant_id = str(conversation.assistant_id)
    assert conversation.summary == "The caller requested a return call about a service enquiry."
    callback_task = next(task for task in persisted_tasks if task.kind == "callback")
    assert callback_task.due_at.isoformat() == "2026-03-02T10:00:00+00:00"
    task_kinds = [task.kind for task in persisted_tasks]
    assert conversation.metrics == {
        "from_number": "+447700900456",
        "duration_seconds": 300,
    }
    assert "end_call" not in task_kinds
    first_detail_response = await client.get(f"/conversations/{conversation_id}")
    assert first_detail_response.status_code == 200, first_detail_response.text
    first_detail = first_detail_response.json()
    assert first_detail["needs_review"] is True
    assert first_detail["summary"] == "The caller requested a return call about a service enquiry."
    list_item = next(
        item
        for item in (await client.get("/conversations?has_review=true")).json()["items"]
        if item["id"] == conversation_id
    )
    assert list_item["needs_review"] is True
    assert list_item["contact_name"] == "Caller Example"
    contact = await client.get(f"/contacts/{person.id}")
    assert contact.status_code == 200, contact.text
    assert contact.json()["conversations"][0]["needs_review"] is True
    first_counts = (
        len(first_detail["messages"]),
        len(first_detail["intakes"]),
        len(first_detail["tasks"]),
    )
    first_gaps_response = await client.get("/knowledge-gaps?status=open")
    assert first_gaps_response.status_code == 200
    first_gap_ids = {
        item["id"]
        for item in first_gaps_response.json()["items"]
        if item["conversation_id"] == conversation_id
    }
    assert first_gap_ids
    repeated_report = await client.post(
        f"/internal/voice/calls/{conversation_id}/report",
        json=report,
        headers=service_headers,
    )
    assert repeated_report.status_code == 202

    detail = await client.get(f"/conversations/{conversation_id}")
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()
    assert (
        len(detail_data["messages"]),
        len(detail_data["intakes"]),
        len(detail_data["tasks"]),
    ) == first_counts
    assert detail_data["needs_review"] is True
    assert len(detail_data["messages"]) == 1
    assert len(detail_data["intakes"]) == 3
    assert len(detail_data["tasks"]) == 2
    repeated_gaps_response = await client.get("/knowledge-gaps?status=open")
    repeated_gap_ids = {
        item["id"]
        for item in repeated_gaps_response.json()["items"]
        if item["conversation_id"] == conversation_id
    }
    assert repeated_gap_ids == first_gap_ids
    review_filter = await client.get("/conversations?has_review=true")
    assert conversation_id in {item["id"] for item in review_filter.json()["items"]}
    assert len((await client.get("/tasks?status=open")).json()["items"]) == 2
    assert len((await client.get("/knowledge-gaps?status=open")).json()["items"]) == 1
    assert len((await client.get("/tasks?kind=callback")).json()["items"]) == 1
    assert len((await client.get(f"/tasks?assistant_id={assistant_id}")).json()["items"]) == 2
    assert (
        len((await client.get(f"/knowledge-gaps?assistant_id={assistant_id}")).json()["items"]) == 1
    )

    reviewed = await client.post(
        f"/conversations/{conversation_id}/reviews",
        json={"field": "email", "resolved_value": "new@example.test", "note": "Caller confirmed"},
    )
    assert reviewed.status_code == 201, reviewed.text
    detail = (await client.get(f"/conversations/{conversation_id}")).json()
    assert detail["needs_review"] is False
    merged = next(item["data"] for item in detail["intakes"] if item["version"] == "merged")
    assert merged["fields"]["email"]["status"] == "human"
    assert merged["fields"]["email"]["previous_value"] == "old@example.test"
    assert merged["needs_review"] == []
    review_filter_after = await client.get("/conversations?has_review=true")
    no_review_filter = await client.get("/conversations?has_review=false")
    assert conversation_id not in {item["id"] for item in review_filter_after.json()["items"]}
    assert conversation_id in {item["id"] for item in no_review_filter.json()["items"]}

    for call_id in ("voice-flow-extra-1", "voice-flow-extra-2"):
        await client.post(
            "/internal/voice/calls",
            json={**start_payload, "external_call_id": call_id},
            headers=service_headers,
        )
    page = await client.get("/conversations?limit=2")
    assert len(page.json()["items"]) == 2
    assert page.json()["next_cursor"]
    next_page = await client.get(
        "/conversations", params={"limit": 2, "cursor": page.json()["next_cursor"]}
    )
    assert len(next_page.json()["items"]) == 1
    assert len({item["id"] for item in (*page.json()["items"], *next_page.json()["items"])}) == 3

    async with tenant_session(UUID(account["tenant_id"])) as session:
        graph_jobs = list(
            await session.scalars(
                select(Job).where(
                    Job.kind == "graph_ingest",
                    Job.payload == {"conversation_id": conversation_id},
                )
            )
        )
        assert len(graph_jobs) == 1
        job_id = graph_jobs[0].id
        message_count = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == UUID(conversation_id))
        )
        task_count = await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.conversation_id == UUID(conversation_id))
        )
        gap_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeGap)
            .where(KnowledgeGap.conversation_id == UUID(conversation_id))
        )
    assert (message_count, task_count, gap_count) == (1, 2, 1)
    assert await run_next_job()
    job = await client.get(f"/jobs/{job_id}")
    assert job.json()["status"] == "succeeded"
    assert job.json()["attempts"] == 1
    assert job.json()["error"] is None


async def test_sql_review_filter_matches_python_review_policy(tenant_pair) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    config["review_confidence_threshold"] = 0.7
    assistant = await client.post(
        "/assistants",
        json={"name": "Review filter assistant", "template": "uk_law_firm", "config": config},
    )
    assert assistant.status_code == 201, assistant.text

    field_fixtures = {
        "human_low_confidence": {
            "status": "human",
            "confidence": 0.1,
            "needs_review": True,
        },
        "below_threshold": {
            "status": "model",
            "confidence": 0.69,
            "needs_review": False,
        },
        "explicit_review": {
            "status": "model",
            "confidence": 0.9,
            "needs_review": True,
        },
        "clean": {
            "status": "model",
            "confidence": 0.9,
            "needs_review": False,
        },
    }
    assistant_id = UUID(assistant.json()["id"])
    tenant_id = UUID(account["tenant_id"])
    expected_review_ids = set()
    expected_clear_ids = set()
    async with tenant_session(tenant_id) as session:
        for field_name, field_data in field_fixtures.items():
            data = {"fields": {field_name: field_data}}
            conversation = Conversation(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.PHONE,
                external_ref=f"review-predicate-{uuid4()}",
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
            )
            session.add(conversation)
            await session.flush()
            session.add(
                Intake(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    version=IntakeVersion.MERGED,
                    data=data,
                )
            )
            if merged_needs_review(data, 0.7):
                expected_review_ids.add(str(conversation.id))
            else:
                expected_clear_ids.add(str(conversation.id))

    review_response = await client.get("/conversations?has_review=true")
    clear_response = await client.get("/conversations?has_review=false")
    assert review_response.status_code == 200, review_response.text
    assert clear_response.status_code == 200, clear_response.text
    sql_review_ids = {item["id"] for item in review_response.json()["items"]}
    sql_clear_ids = {item["id"] for item in clear_response.json()["items"]}
    assert sql_review_ids == expected_review_ids
    assert sql_clear_ids == expected_clear_ids


async def test_malformed_list_cursors_return_400(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]
    assert (await client.get("/tasks?cursor=A")).status_code == 400
    assert (await client.get("/conversations?cursor=A")).status_code == 400
