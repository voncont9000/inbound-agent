import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from inbound_core.config import AssistantConfig
from sqlalchemy import select

from inbound_api import graph
from inbound_api.db import tenant_session
from inbound_api.jobs import PermanentJobError
from inbound_api.models import (
    Conversation,
    KnowledgeDigest,
    Task,
    TaskStatus,
)
from inbound_api.routers.webhooks import _store_inbound_message
from inbound_api.services.conversation_results import persist_extracted_results


def _assistant_config() -> dict:
    return {
        "business_name": "Shared Prompt Business",
        "greeting": "Hello",
        "office_hours": {"days": ["Monday"], "opens": "09:00", "closes": "17:00"},
        "price_list": [],
        "policies": [],
        "contacts": {},
        "services": {"general": {"label": "General", "fields": []}},
        "urgency_rules": [],
        "live_actions": [],
    }


def _fake_genai(monkeypatch, job_handlers, prompts: list[str]) -> None:
    class Models:
        async def generate_content(self, *, model: str, contents: str):
            prompts.append(contents)
            return SimpleNamespace(
                text="Thanks, I can help with that.",
                usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=8),
            )

    class Aio:
        models = Models()

        async def aclose(self):
            return None

    monkeypatch.setattr(job_handlers, "make_genai_client", lambda _key: SimpleNamespace(aio=Aio()))


async def _insert_test_message(graph_env, timestamp: datetime, wamid: str):
    tenant_id = graph_env["tenant_id"]
    await _store_inbound_message(
        tenant_id=tenant_id,
        assistant_id=graph_env["assistant_id"],
        connection_id=uuid4(),
        phone_number_id="123456",
        message={
            "id": wamid,
            "from": "447700900123",
            "timestamp": str(int(timestamp.timestamp())),
            "type": "text",
            "text": {"body": "Could you help me?"},
        },
        contact_name="Alex",
    )
    return graph_env["execute"](
        "SELECT id, conversation_id FROM messages WHERE tenant_id = %s AND external_id = %s",
        (tenant_id, wamid),
    )[0]


@pytest.mark.asyncio
async def test_whatsapp_verification_signature_and_unknown_number(api_client, monkeypatch):
    from inbound_api.routers import webhooks

    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(
            whatsapp_verify_token="verify-me", whatsapp_app_secret="app-secret"
        ),
    )
    verified = await api_client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "challenge-value",
        },
    )
    assert verified.status_code == 200
    assert verified.text == "challenge-value"
    rejected = await api_client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    )
    assert rejected.status_code == 403

    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "not-connected"},
                                "contacts": [
                                    {"wa_id": "447700900123", "profile": {"name": "Alex"}}
                                ],
                                "messages": [
                                    {
                                        "id": "wamid.unknown",
                                        "from": "447700900123",
                                        "timestamp": "1792320000",
                                        "type": "text",
                                        "text": {"body": "Hi"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    accepted = await api_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": f"sha256={signature}"},
    )
    assert accepted.status_code == 200
    rejected_signature = await api_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=bad"},
    )
    assert rejected_signature.status_code == 401

    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(whatsapp_verify_token="verify-me", whatsapp_app_secret=None),
    )
    unconfigured = await api_client.post("/webhooks/whatsapp", content=body)
    assert unconfigured.status_code == 503


@pytest.mark.asyncio
async def test_malformed_whatsapp_payload_shapes_are_ignored(api_client, monkeypatch):
    from inbound_api.routers import webhooks

    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(whatsapp_app_secret="app-secret"),
    )
    malformed_payloads = [
        [],
        {"entry": {}},
        {"entry": [None, {"changes": {}}, {"changes": [None, {"value": []}]}]},
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": [],
                                "contacts": {},
                                "messages": [
                                    None,
                                    ["not-a-message"],
                                    {
                                        "id": "wamid.malformed",
                                        "from": "447700900123",
                                        "type": "text",
                                        "text": [],
                                    },
                                ],
                            }
                        }
                    ]
                }
            ]
        },
    ]
    for payload in malformed_payloads:
        body = json.dumps(payload).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        response = await api_client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_whatsapp_wamid_deduplication_and_24_hour_rollover(graph_env):
    tenant_id = graph_env["tenant_id"]
    assistant_id = graph_env["assistant_id"]
    connection_id = uuid4()
    received_at = datetime.now(UTC).replace(microsecond=0)

    async def ingest(wamid: str, timestamp: datetime):
        await _store_inbound_message(
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            connection_id=connection_id,
            phone_number_id="123456",
            message={
                "id": wamid,
                "from": "447700900123",
                "timestamp": str(int(timestamp.timestamp())),
                "type": "text",
                "text": {"body": "Hello"},
            },
            contact_name="Alex",
        )

    await ingest("wamid.first", received_at)
    await ingest("wamid.first", received_at)
    await ingest("wamid.next", received_at + timedelta(hours=25))

    conversations = graph_env["execute"](
        "SELECT status, external_ref FROM conversations "
        "WHERE tenant_id = %s AND channel = 'whatsapp' ORDER BY started_at",
        (tenant_id,),
    )
    assert len(conversations) == 2
    assert conversations[0][0] == "completed"
    assert conversations[0][1] == "wa:123456:447700900123:wamid.first"
    assert conversations[1][1] == "wa:123456:447700900123:wamid.next"
    assert (
        graph_env["execute"](
            "SELECT count(*) FROM messages WHERE tenant_id = %s AND external_id = 'wamid.first'",
            (tenant_id,),
        )[0][0]
        == 1
    )
    assert (
        graph_env["execute"](
            "SELECT count(*) FROM jobs WHERE tenant_id = %s AND kind = 'message_reply'",
            (tenant_id,),
        )[0][0]
        == 2
    )


@pytest.mark.asyncio
async def test_whatsapp_conversations_are_scoped_to_receiving_number(graph_env):
    tenant_id = graph_env["tenant_id"]
    assistant_id = graph_env["assistant_id"]
    received_at = datetime.now(UTC).replace(microsecond=123456)
    conversation_ids = []
    for phone_number_id, wamid in (
        ("123456", "wamid.number-one"),
        ("654321", "wamid.number-two"),
    ):
        await _store_inbound_message(
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            connection_id=uuid4(),
            phone_number_id=phone_number_id,
            message={
                "id": wamid,
                "from": "447700900123",
                "timestamp": str(int(received_at.timestamp())),
                "type": "text",
                "text": {"body": "Hello"},
            },
            contact_name="Alex",
            received_at=received_at,
        )
        conversation_ids.append(
            graph_env["execute"](
                "SELECT conversation_id FROM messages WHERE tenant_id = %s AND external_id = %s",
                (tenant_id, wamid),
            )[0][0]
        )
    assert conversation_ids[0] != conversation_ids[1]
    refs = graph_env["execute"](
        "SELECT external_ref FROM conversations WHERE tenant_id = %s "
        "AND id = ANY(%s) ORDER BY external_ref",
        (tenant_id, conversation_ids),
    )
    assert refs == [
        ("wa:123456:447700900123:wamid.number-one",),
        ("wa:654321:447700900123:wamid.number-two",),
    ]
    inbound_meta = graph_env["execute"](
        "SELECT ts, meta->>'received_at', meta->>'provider_timestamp' FROM messages "
        "WHERE tenant_id = %s AND external_id = 'wamid.number-one'",
        (tenant_id,),
    )[0]
    assert inbound_meta[0] == received_at
    assert inbound_meta[1].endswith(".123456+00:00")
    assert inbound_meta[2].endswith(".000000+00:00")


@pytest.mark.asyncio
async def test_shared_assistant_route_loads_digest_and_whatsapp_context(graph_env, monkeypatch):
    from inbound_api.services import assistant_context

    tenant_id = graph_env["tenant_id"]
    assistant_id = graph_env["assistant_id"]
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(_assistant_config()), assistant_id),
    )
    async with tenant_session(tenant_id) as session:
        session.add(
            KnowledgeDigest(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                content="The remortgage fee is £595.",
            )
        )

    resolved: dict = {}
    person_id = uuid4()

    async def resolve_person(_session, **kwargs):
        resolved.update(kwargs)
        return person_id

    async def build_context_card(_session, resolved_person):
        assert resolved_person == person_id
        return "Returning client context."

    monkeypatch.setattr(assistant_context.graph, "resolve_person", resolve_person)
    monkeypatch.setattr(assistant_context.graph, "build_context_card", build_context_card)
    route = await assistant_context.load_assistant_route(
        tenant_id,
        assistant_id,
        caller_phone="+447700900123",
        whatsapp_id="447700900123",
        source="whatsapp",
        include_context_card=True,
    )
    assert route.digest == "The remortgage fee is £595."
    assert route.context_card == "Returning client context."
    assert resolved == {
        "phone": "+447700900123",
        "email": None,
        "whatsapp_id": "447700900123",
        "source": "whatsapp",
    }


@pytest.mark.asyncio
async def test_non_text_messages_are_stored_as_typed_placeholders(graph_env):
    tenant_id = graph_env["tenant_id"]
    await _store_inbound_message(
        tenant_id=tenant_id,
        assistant_id=graph_env["assistant_id"],
        connection_id=uuid4(),
        phone_number_id="123456",
        message={
            "id": "wamid.image",
            "from": "447700900123",
            "timestamp": str(int(datetime.now(UTC).timestamp())),
            "type": "image",
            "image": {"id": "media-id"},
        },
        contact_name="Alex",
    )
    assert graph_env["execute"](
        "SELECT text, meta->>'type' FROM messages "
        "WHERE tenant_id = %s AND external_id = 'wamid.image'",
        (tenant_id,),
    ) == [("[image]", "image")]


@pytest.mark.asyncio
async def test_graph_sender_posts_meta_text_payload(monkeypatch):
    from inbound_api.channels import whatsapp_client

    requests = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            requests.append((url, headers, json))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"messages": [{"id": "wamid.sent"}]},
            )

    monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", FakeAsyncClient)
    sender = whatsapp_client.GraphWhatsAppSender(
        base_url="https://graph.facebook.com/v26.0/",
        access_token="test-token",
    )

    message_id = await sender.send_text("phone-id", "447700900123", "Hello")

    assert message_id == "wamid.sent"
    assert requests == [
        (
            "https://graph.facebook.com/v26.0/phone-id/messages",
            {"Authorization": "Bearer test-token"},
            {
                "messaging_product": "whatsapp",
                "to": "447700900123",
                "type": "text",
                "text": {"body": "Hello"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_whatsapp_identity_links_to_prior_phone_conversation(graph_env):
    tenant_id = graph_env["tenant_id"]
    assistant_id = graph_env["assistant_id"]
    phone_id, whatsapp_id = uuid4(), uuid4()
    started_at = datetime.now(UTC)
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(_assistant_config()), assistant_id),
    )
    for conversation_id, channel, external_ref, metrics in (
        (phone_id, "phone", "phone-call", {"from_number": "+447700900123"}),
        (
            whatsapp_id,
            "whatsapp",
            "wa:123456:447700900123:wamid.identity",
            {"from_number": "+447700900123", "whatsapp_wa_id": "447700900123"},
        ),
    ):
        graph_env["execute"](
            "INSERT INTO conversations "
            "(id, tenant_id, assistant_id, channel, external_ref, status, started_at, "
            "ended_at, summary, metrics) "
            "VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, '', %s::jsonb)",
            (
                conversation_id,
                tenant_id,
                assistant_id,
                channel,
                external_ref,
                started_at,
                started_at,
                json.dumps(metrics),
            ),
        )
    async with tenant_session(tenant_id) as session:
        await graph.ingest_conversation(session, phone_id)
    async with tenant_session(tenant_id) as session:
        await graph.ingest_conversation(session, whatsapp_id)

    people = graph_env["execute"](
        "SELECT id, person_id FROM conversations WHERE tenant_id = %s AND id = ANY(%s)",
        (tenant_id, [phone_id, whatsapp_id]),
    )
    assert len(people) == 2
    assert people[0][1] == people[1][1]
    assert graph_env["execute"](
        "SELECT value_normalized FROM identities WHERE tenant_id = %s AND type = 'whatsapp'",
        (tenant_id,),
    ) == [("447700900123",)]


@pytest.mark.asyncio
async def test_extracted_results_replace_open_tasks_and_dedupe_gaps(graph_env):
    tenant_id = graph_env["tenant_id"]
    received_at = datetime.now(UTC).replace(microsecond=0)
    await _store_inbound_message(
        tenant_id=tenant_id,
        assistant_id=graph_env["assistant_id"],
        connection_id=uuid4(),
        phone_number_id="123456",
        message={
            "id": "wamid.results",
            "from": "447700900123",
            "timestamp": str(int(received_at.timestamp())),
            "type": "text",
            "text": {"body": "Help"},
        },
        contact_name="Alex",
    )
    async with tenant_session(tenant_id) as session:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.external_ref == "wa:123456:447700900123:wamid.results"
            )
        )
        await persist_extracted_results(
            session,
            tenant_id,
            conversation,
            live_intake={},
            post_call_intake={"summary": "First"},
            merged_intake={"summary": "First"},
            summary="First summary",
            tasks=[{"kind": "follow_up", "note": "old"}],
            knowledge_gaps=[
                {"question": "How soon?", "evidence": "Customer asked"},
                {"question": "How soon?", "evidence": "Duplicate"},
            ],
        )
        old_task = await session.scalar(select(Task).where(Task.conversation_id == conversation.id))
        old_task.status = TaskStatus.DONE
    async with tenant_session(tenant_id) as session:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.external_ref == "wa:123456:447700900123:wamid.results"
            )
        )
        await persist_extracted_results(
            session,
            tenant_id,
            conversation,
            live_intake={},
            post_call_intake={"summary": "Second"},
            merged_intake={"summary": "Second"},
            summary="Second summary",
            tasks=[{"kind": "follow_up", "note": "new"}],
            knowledge_gaps=[{"question": "How soon?", "evidence": "Updated"}],
        )

    assert set(
        graph_env["execute"](
            "SELECT version FROM intakes WHERE tenant_id = %s AND conversation_id = "
            "(SELECT id FROM conversations WHERE "
            "external_ref = 'wa:123456:447700900123:wamid.results')",
            (tenant_id,),
        )
    ) == {("live",), ("post_call",), ("merged",)}
    assert set(
        graph_env["execute"](
            "SELECT status, payload->>'note' FROM tasks WHERE tenant_id = %s AND conversation_id = "
            "(SELECT id FROM conversations WHERE "
            "external_ref = 'wa:123456:447700900123:wamid.results')",
            (tenant_id,),
        )
    ) == {("done", "old"), ("open", "new")}
    assert (
        graph_env["execute"](
            "SELECT count(*) FROM knowledge_gaps WHERE tenant_id = %s AND question = 'How soon?'",
            (tenant_id,),
        )[0][0]
        == 1
    )
    assert graph_env["execute"](
        "SELECT summary FROM conversations WHERE tenant_id = %s AND external_ref = "
        "'wa:123456:447700900123:wamid.results'",
        (tenant_id,),
    ) == [("Second summary",)]


@pytest.mark.asyncio
async def test_message_reply_coalesces_burst_and_persists_fake_send(
    graph_env, monkeypatch, tmp_path, caplog
):
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(_assistant_config()), graph_env["assistant_id"]),
    )
    settings = SimpleNamespace(
        google_api_key="test-key",
        text_agent_model="gemini-3.5-flash-lite",
        whatsapp_mode="fake",
        fake_outbox_path=tmp_path / "outbox.jsonl",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "missing-prices.yaml",
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    prompts: list[str] = []
    _fake_genai(monkeypatch, job_handlers, prompts)
    route_config = AssistantConfig.model_validate(_assistant_config())
    route_calls: list[dict] = []

    async def load_route(*args, **kwargs):
        route_calls.append(kwargs)
        return SimpleNamespace(
            config=route_config,
            digest="The remortgage fee is £595.",
            context_card="Returning client context.",
        )

    monkeypatch.setattr(job_handlers, "load_assistant_route", load_route)
    first_ts = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=3)
    first_message_id, conversation_id = await _insert_test_message(
        graph_env, first_ts, "wamid.burst.first"
    )
    second_message_id, second_conversation_id = await _insert_test_message(
        graph_env, first_ts + timedelta(seconds=1), "wamid.burst.second"
    )
    assert second_conversation_id == conversation_id

    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(first_message_id)},
        )
    assert not settings.fake_outbox_path.exists()

    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(second_message_id)},
        )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(second_message_id)},
        )

    outbox = [json.loads(line) for line in settings.fake_outbox_path.read_text().splitlines()]
    assert len(outbox) == 1
    assert outbox[0]["to"] == "447700900123"
    assert "Shared Prompt Business" in prompts[0]
    assert "WHATSAPP MESSAGE RULES" in prompts[0]
    assert "447700900123" in prompts[0]
    assert "The remortgage fee is £595." in prompts[0]
    assert "Returning client context." in prompts[0]
    assert "first reply only" in prompts[0]
    assert "Do not reuse, quote, or paraphrase it" in prompts[0]
    assert "How can I help?" in prompts[0]
    assert "callback offer" not in prompts[0].lower()
    assert route_calls[0]["caller_phone"] == "+447700900123"
    assert route_calls[0]["whatsapp_id"] == "447700900123"
    assert route_calls[0]["source"] == "whatsapp"
    assert "missing-prices.yaml" in caplog.text
    assert graph_env["execute"](
        "SELECT role, external_id, meta->>'model', meta->>'cost' FROM messages "
        "WHERE tenant_id = %s AND external_id = %s",
        (tenant_id, outbox[0]["id"]),
    ) == [("assistant", outbox[0]["id"], "gemini-3.5-flash-lite", None)]
    assert (
        graph_env["execute"](
            "SELECT count(*) FROM jobs WHERE tenant_id = %s AND kind = 'message_extract'",
            (tenant_id,),
        )[0][0]
        == 1
    )

    third_message_id, third_conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) + timedelta(seconds=1),
        "wamid.burst.third",
    )
    assert third_conversation_id == conversation_id
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(third_message_id)},
        )
    assert len(prompts) == 2
    assert "later reply" in prompts[1]
    assert "first reply only" not in prompts[1]
    assert len(settings.fake_outbox_path.read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_outside_window_creates_template_task_without_sending(
    graph_env, monkeypatch, tmp_path
):
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    settings = SimpleNamespace(
        google_api_key=None,
        whatsapp_mode="fake",
        fake_outbox_path=tmp_path / "outbox.jsonl",
        message_extract_delay_seconds=120,
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    message_id, conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) - timedelta(hours=25),
        "wamid.outside-window",
    )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(message_id)},
        )

    assert not settings.fake_outbox_path.exists()
    assert graph_env["execute"](
        "SELECT kind, payload->>'to' FROM tasks WHERE tenant_id = %s AND conversation_id = %s",
        (tenant_id, conversation_id),
    ) == [("whatsapp_template_needed", "447700900123")]


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        (400, PermanentJobError),
        (429, httpx.HTTPStatusError),
        (503, httpx.HTTPStatusError),
    ],
)
@pytest.mark.asyncio
async def test_sender_errors_are_classified_for_retry(
    graph_env, monkeypatch, tmp_path, status, expected_error
):
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(_assistant_config()), graph_env["assistant_id"]),
    )
    settings = SimpleNamespace(
        google_api_key="test-key",
        text_agent_model="gemini-3.5-flash-lite",
        whatsapp_mode="graph",
        whatsapp_graph_base_url="https://graph.facebook.com/v26.0",
        whatsapp_access_token="test-token",
        fake_outbox_path=tmp_path / "unused.jsonl",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "prices.yaml",
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    _fake_genai(monkeypatch, job_handlers, [])

    class FailingSender:
        async def send_text(self, phone_number_id, to, body):
            request = httpx.Request("POST", "https://graph.facebook.com/messages")
            response = httpx.Response(status_code=status, request=request)
            raise httpx.HTTPStatusError("send failed", request=request, response=response)

    monkeypatch.setattr(job_handlers, "sender_from_settings", lambda _settings: FailingSender())
    message_id, conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) - timedelta(seconds=2),
        f"wamid.sender-error-{status}",
    )
    async with tenant_session(tenant_id) as session:
        with pytest.raises(expected_error):
            await job_handlers.message_reply(
                session,
                {"conversation_id": str(conversation_id), "message_id": str(message_id)},
            )


@pytest.mark.asyncio
async def test_message_extract_persists_results_and_tasks(graph_env, monkeypatch):
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(_assistant_config()), graph_env["assistant_id"]),
    )
    settings = SimpleNamespace(
        google_api_key="test-key",
        extraction_model="gemini-3.8-flash",
        message_extract_delay_seconds=120,
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)

    class Aio:
        async def aclose(self):
            return None

    monkeypatch.setattr(job_handlers, "make_genai_client", lambda _key: SimpleNamespace(aio=Aio()))

    async def extract(*args, **kwargs):
        assert kwargs["channel"] == "whatsapp"
        return {
            "status": "ok",
            "service": "general",
            "model": "gemini-3.8-flash",
            "summary": "The customer requested a callback.",
            "fields": {},
            "callback_requested": {
                "requested": True,
                "reason": "Customer asked to speak with the team",
                "preferred_time": None,
                "preferred_time_iso": None,
            },
            "complaint": {"made": False, "summary": None},
            "unanswered_questions": [
                {"question": "Can you confirm the fee?", "evidence": "Asked about a fee"}
            ],
        }

    monkeypatch.setattr(job_handlers, "extract_intake", extract)
    _, conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) - timedelta(seconds=180),
        "wamid.extract",
    )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_extract(
            session,
            {"conversation_id": str(conversation_id)},
        )

    assert graph_env["execute"](
        "SELECT version, data->>'summary' FROM intakes WHERE tenant_id = %s "
        "AND conversation_id = %s ORDER BY version",
        (tenant_id, conversation_id),
    ) == [
        ("live", None),
        ("post_call", "The customer requested a callback."),
        ("merged", None),
    ]
    assert graph_env["execute"](
        "SELECT summary FROM conversations WHERE tenant_id = %s AND id = %s",
        (tenant_id, conversation_id),
    ) == [("The customer requested a callback.",)]
    assert graph_env["execute"](
        "SELECT kind, source, payload->>'reason' FROM tasks "
        "WHERE tenant_id = %s AND conversation_id = %s",
        (tenant_id, conversation_id),
    ) == [("callback", "post_call", "Customer asked to speak with the team")]
    assert graph_env["execute"](
        "SELECT question FROM knowledge_gaps WHERE tenant_id = %s AND conversation_id = %s",
        (tenant_id, conversation_id),
    ) == [("Can you confirm the fee?",)]


def test_production_requires_graph_whatsapp_credentials():
    from inbound_api.settings import Settings

    with pytest.raises(ValueError, match="WhatsApp production requires"):
        Settings(app_env="production", voice_service_token="separate-voice-token")
    configured = Settings(
        app_env="production",
        voice_service_token="separate-voice-token",
        whatsapp_mode="graph",
        whatsapp_app_secret="secret",
        whatsapp_verify_token="verify-token",
        whatsapp_access_token="access-token",
        gmail_mode="off",
        email_sender="off",
        sheets_writer="off",
        webhook_sender="off",
    )
    assert configured.whatsapp_mode == "graph"
