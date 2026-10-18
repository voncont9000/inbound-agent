import asyncio
import hashlib
import hmac
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from inbound_core.config import AssistantConfig
from sqlalchemy import select

from inbound_api import graph
from inbound_api.db import tenant_session
from inbound_api.jobs import PermanentJobError, run_next_job
from inbound_api.models import (
    ActionRun,
    ChannelConnection,
    Conversation,
    Job,
    KnowledgeDigest,
    Message,
    MessageRole,
    Task,
    TaskStatus,
)
from inbound_api.post_actions.providers import FakeEmailSender
from inbound_api.post_actions.service import (
    execute_conversation_action,
    plan_conversation_actions,
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
async def test_whatsapp_off_disables_signed_post_and_verification(api_client, monkeypatch):
    from inbound_api.routers import webhooks

    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(
            whatsapp_mode="off",
            whatsapp_app_secret="app-secret",
            whatsapp_verify_token="verify-me",
        ),
    )
    body = b'{"entry":[]}'
    signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

    posted = await api_client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": f"sha256={signature}"},
    )
    verified = await api_client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "challenge",
        },
    )

    assert posted.status_code == 503
    assert posted.json() == {"detail": "WhatsApp is disabled"}
    assert verified.status_code == 503
    assert verified.json() == {"detail": "WhatsApp is disabled"}


def test_disabled_whatsapp_sender_raises_without_writing_outbox(tmp_path):
    from inbound_api.channels.whatsapp_client import (
        DisabledWhatsAppSender,
        WhatsAppDisabledError,
        sender_from_settings,
    )

    settings = SimpleNamespace(whatsapp_mode="off", fake_outbox_path=tmp_path / "outbox.jsonl")
    sender = sender_from_settings(settings)

    assert isinstance(sender, DisabledWhatsAppSender)
    with pytest.raises(WhatsAppDisabledError, match="WhatsApp is disabled"):
        asyncio.run(sender.send_text("phone-id", "447700900123", "Hello"))
    assert not settings.fake_outbox_path.exists()


@pytest.mark.asyncio
async def test_whatsapp_off_reply_is_permanent_and_does_not_write_outbox(
    graph_env, monkeypatch, tmp_path
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
        whatsapp_mode="off",
        fake_outbox_path=tmp_path / "outbox.jsonl",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "missing-prices.yaml",
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    _fake_genai(monkeypatch, job_handlers, [])
    route_config = AssistantConfig.model_validate(_assistant_config())

    async def load_route(*args, **kwargs):
        return SimpleNamespace(config=route_config, digest=None, context_card=None)

    monkeypatch.setattr(job_handlers, "load_assistant_route", load_route)
    message_id, conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) - timedelta(seconds=2),
        "wamid.disabled-reply",
    )

    async with tenant_session(tenant_id) as session:
        with pytest.raises(PermanentJobError, match="WhatsApp is disabled"):
            await job_handlers.message_reply(
                session,
                {"conversation_id": str(conversation_id), "message_id": str(message_id)},
            )

    assert not settings.fake_outbox_path.exists()
    assert graph_env["execute"](
        "SELECT count(*) FROM messages WHERE tenant_id = %s AND conversation_id = %s "
        "AND role = 'assistant'",
        (tenant_id, conversation_id),
    ) == [(0,)]


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


@pytest.mark.asyncio
async def test_message_extract_replans_actions_only_for_new_text_events(
    graph_env, monkeypatch, tmp_path
):
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    config = _assistant_config()
    config["urgency_rules"] = [
        {
            "type": "date_deadline",
            "service": "general",
            "field": "deadline",
            "add_months": 0,
            "minus_days": 0,
            "urgent_within_days": 30,
            "reason": "Deadline is approaching",
        }
    ]
    config["post_actions"] = [
        {
            "kind": "notify_staff",
            "key": "completed-only",
            "trigger": {"events": ["conversation_completed"]},
            "to": ["staff@example.com"],
            "subject_template": "Completed",
            "body_template": "{{summary}}",
        },
        {
            "kind": "notify_staff",
            "key": "completed-and-urgent",
            "trigger": {"events": ["conversation_completed", "urgent"]},
            "to": ["staff@example.com"],
            "subject_template": "Follow up",
            "body_template": "{{summary}}",
        },
    ]
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config), graph_env["assistant_id"]),
    )
    monkeypatch.setattr(
        job_handlers,
        "get_settings",
        lambda: SimpleNamespace(
            google_api_key="test-key",
            extraction_model="gemini-3.8-flash",
            message_extract_delay_seconds=120,
        ),
    )

    class Aio:
        async def aclose(self):
            return None

    monkeypatch.setattr(job_handlers, "make_genai_client", lambda _key: SimpleNamespace(aio=Aio()))
    extraction_count = 0

    async def extract(*_args, **_kwargs):
        nonlocal extraction_count
        extraction_count += 1
        fields = {}
        if extraction_count == 2:
            fields["deadline"] = {
                "value": (datetime.now(UTC) + timedelta(days=5)).date().isoformat(),
                "confidence": 0.99,
            }
        return {
            "status": "ok",
            "service": "general",
            "model": "gemini-3.8-flash",
            "summary": "Customer needs help.",
            "fields": fields,
            "callback_requested": {"requested": False},
            "complaint": {"made": False},
            "unanswered_questions": [],
        }

    monkeypatch.setattr(job_handlers, "extract_intake", extract)
    _, conversation_id = await _insert_test_message(
        graph_env, datetime.now(UTC) - timedelta(seconds=180), "wamid.actions.first"
    )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_extract(session, {"conversation_id": str(conversation_id)})
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 2}
    for action_key in ("completed-only", "completed-and-urgent"):
        async with tenant_session(tenant_id) as session:
            result = await execute_conversation_action(
                session,
                conversation_id=conversation_id,
                action_key=action_key,
                fire_count=1,
                email_sender=FakeEmailSender(tmp_path),
            )
        assert result["status"] == "succeeded"
    async with tenant_session(tenant_id) as session:
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 0}
        execute_jobs = list(
            await session.scalars(select(Job).where(Job.kind == "post_action_execute"))
        )
    assert len(execute_jobs) == 2
    assert {job.payload["fire_count"] for job in execute_jobs} == {1}

    _, later_conversation_id = await _insert_test_message(
        graph_env, datetime.now(UTC) - timedelta(seconds=180), "wamid.actions.urgent"
    )
    assert later_conversation_id == conversation_id
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_extract(session, {"conversation_id": str(conversation_id)})
    assert extraction_count == 2
    async with tenant_session(tenant_id) as session:
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 1}
        execute_jobs = list(
            await session.scalars(select(Job).where(Job.kind == "post_action_execute"))
        )
        runs = {
            run.action_key: run
            for run in await session.scalars(
                select(ActionRun).where(ActionRun.conversation_id == conversation_id)
            )
        }
        urgent_tasks = list(
            await session.scalars(
                select(Task).where(
                    Task.conversation_id == conversation_id,
                    Task.kind == "urgent",
                )
            )
        )
    assert len(urgent_tasks) == 1
    assert len(execute_jobs) == 3
    assert [job.payload for job in execute_jobs if job.payload["fire_count"] == 2] == [
        {
            "conversation_id": str(conversation_id),
            "action_key": "completed-and-urgent",
            "fire_count": 2,
        }
    ]
    assert runs["completed-only"].fire_count == 1
    assert runs["completed-and-urgent"].fire_count == 2
    async with tenant_session(tenant_id) as session:
        result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="completed-and-urgent",
            fire_count=2,
            email_sender=FakeEmailSender(tmp_path),
        )
    assert result["status"] == "succeeded"
    assert len((tmp_path / "emails.jsonl").read_text().splitlines()) == 3


@pytest.mark.asyncio
async def test_whatsapp_fake_actions_complete_and_are_visible(tenant_pair, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from inbound_api.post_actions import service as post_action_service
    from inbound_api.services import job_handlers
    from inbound_api.settings import Settings, get_settings

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((body, self.headers.get("X-Inbound-Signature")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        account = tenant_pair["first"]
        client = account["client"]
        templates = (await client.get("/templates")).json()
        config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
        service_name = next(iter(config["services"]))
        service_fields = config["services"][service_name]["fields"]
        if not any(field["name"] == "email" for field in service_fields):
            service_fields.append({"name": "email", "label": "Email address", "type": "email"})
        config["post_actions"] = [
            {
                "kind": "email_customer",
                "key": "customer-email",
                "trigger": {"events": ["conversation_completed"]},
                "subject_template": "Thanks",
                "body_template": "Hello {{contact.email}}",
            },
            {
                "kind": "sheets_row",
                "key": "customer-sheet",
                "trigger": {"events": ["conversation_completed"]},
                "spreadsheet_id": "fake-sheet",
                "sheet_name": "Enquiries",
                "columns": [
                    {"header": "Email", "value_template": "{{contact.email}}"},
                    {"header": "Summary", "value_template": "{{summary}}"},
                ],
            },
            {
                "kind": "webhook",
                "key": "customer-hook",
                "trigger": {"events": ["conversation_completed"]},
                "url": f"http://127.0.0.1:{server.server_port}/hook",
            },
        ]
        assistant_response = await client.post(
            "/assistants",
            json={"name": "WhatsApp actions", "template": "uk_law_firm", "config": config},
        )
        assert assistant_response.status_code == 201, assistant_response.text
        assistant_id = UUID(assistant_response.json()["id"])
        channel_response = await client.post(
            f"/assistants/{assistant_id}/channels",
            json={"kind": "whatsapp", "external_id": "123456"},
        )
        assert channel_response.status_code == 201, channel_response.text
        channel_id = UUID(channel_response.json()["id"])
        settings = Settings(
            app_env="test",
            whatsapp_mode="fake",
            fake_outbox_path=tmp_path / "whatsapp.jsonl",
            email_sender="fake",
            sheets_writer="fake",
            webhook_sender="http",
            post_actions_fake_dir=tmp_path,
            post_action_webhook_allow_private_urls=True,
            google_api_key="test-key",
            message_extract_delay_seconds=0,
        )
        monkeypatch.setattr(
            get_settings(), "credentials_encryption_key", Fernet.generate_key().decode()
        )
        secret_response = await client.post(
            f"/assistants/{assistant_id}/post-actions/customer-hook/webhook-secret"
        )
        assert secret_response.status_code == 200, secret_response.text
        secret = secret_response.json()["secret"]

        tenant_id = UUID(account["tenant_id"])
        async with tenant_session(tenant_id) as session:
            session.add(
                ChannelConnection(
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    kind="gmail",
                    external_id="firm@example.com",
                    status="active",
                    settings={"google_features": ["sheets"]},
                )
            )
        monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
        monkeypatch.setattr(post_action_service, "get_settings", lambda: settings)
        monkeypatch.setattr("inbound_api.jobs.get_settings", lambda: settings)
        _fake_genai(monkeypatch, job_handlers, [])

        async def extract(*_args, **_kwargs):
            return {
                "status": "ok",
                "service": service_name,
                "model": "gemini-3.8-flash",
                "summary": "The customer needs a conveyancing quote.",
                "fields": {
                    "email": {"value": "caller@example.com", "confidence": 0.99},
                },
                "callback_requested": {"requested": False},
                "complaint": {"made": False},
                "unanswered_questions": [],
            }

        monkeypatch.setattr(job_handlers, "extract_intake", extract)
        wamid = "wamid.post-actions-e2e"
        await _store_inbound_message(
            tenant_id=tenant_id,
            assistant_id=assistant_id,
            connection_id=channel_id,
            phone_number_id="123456",
            message={
                "id": wamid,
                "from": "447700900123",
                "timestamp": str(int((datetime.now(UTC) - timedelta(minutes=3)).timestamp())),
                "type": "text",
                "text": {"body": "Please send a quote"},
            },
            contact_name="Alex",
        )
        async with tenant_session(tenant_id) as session:
            message = await session.scalar(select(Message).where(Message.external_id == wamid))
            assert message is not None
            conversation_id = message.conversation_id
        for _ in range(20):
            if not await run_next_job():
                break

        async with tenant_session(tenant_id) as session:
            runs = list(
                await session.scalars(
                    select(ActionRun).where(ActionRun.conversation_id == conversation_id)
                )
            )
        assert {run.action_key: run.status for run in runs} == {
            "customer-email": "succeeded",
            "customer-sheet": "succeeded",
            "customer-hook": "succeeded",
        }
        assert len((tmp_path / "emails.jsonl").read_text().splitlines()) == 1
        assert len((tmp_path / "sheets.jsonl").read_text().splitlines()) == 1
        assert len(received) == 1
        body, signature = received[0]
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert signature == expected
        assert json.loads(body)["schema_version"] == "1"
        detail = await client.get(f"/conversations/{conversation_id}")
        assert detail.status_code == 200, detail.text
        assert {run["action_key"]: run["status"] for run in detail.json()["action_runs"]} == {
            "customer-email": "succeeded",
            "customer-sheet": "succeeded",
            "customer-hook": "succeeded",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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
        calendar_mode="off",
        email_sender="off",
        sheets_writer="off",
        webhook_sender="off",
    )
    assert configured.whatsapp_mode == "graph"


def _whatsapp_booking_config() -> dict:
    return {
        **_assistant_config(),
        "live_actions": ["check_availability", "book_appointment"],
        "booking": {
            "calendar_id": "primary",
            "appointment_types": [
                {
                    "key": "consultation",
                    "label": "Consultation",
                    "duration_minutes": 30,
                    "when_to_use": "Initial appointment",
                    "location_or_link": "Online",
                }
            ],
            "min_notice_hours": 0,
            "horizon_days": 14,
            "buffer_minutes": 0,
            "max_slots_offered": 3,
            "slot_step_minutes": 30,
        },
    }


def _tool_response(name: str, args: dict):
    return SimpleNamespace(
        text=None,
        function_calls=[SimpleNamespace(name=name, args=args)],
        candidates=[SimpleNamespace(content=f"function-call:{name}")],
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1),
    )


def _text_response(text: str):
    return SimpleNamespace(
        text=text,
        function_calls=[],
        candidates=[],
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1),
    )


@pytest.mark.asyncio
async def test_signed_whatsapp_jobs_preserve_booking_offers_across_messages(
    graph_env, api_client, monkeypatch, tmp_path
):
    from inbound_core.config import AssistantConfig

    from inbound_api import settings as settings_module
    from inbound_api.db import tenant_session
    from inbound_api.integrations import google_auth
    from inbound_api.models import CalendarBooking, Commitment, Conversation, Message
    from inbound_api.routers import webhooks
    from inbound_api.services import booking as booking_service
    from inbound_api.services import job_handlers

    tenant_id = graph_env["tenant_id"]
    assistant_id = graph_env["assistant_id"]
    config = AssistantConfig.model_validate(_whatsapp_booking_config())
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config.model_dump(mode="json")), assistant_id),
    )
    graph_env["execute"](
        "INSERT INTO channel_connections "
        "(id, tenant_id, assistant_id, kind, external_id, status, settings) "
        "VALUES (%s, %s, %s, 'whatsapp', '123456', 'active', '{}')",
        (uuid4(), tenant_id, assistant_id),
    )
    graph_env["execute"](
        "INSERT INTO channel_connections "
        "(id, tenant_id, assistant_id, kind, external_id, status, settings) "
        "VALUES (%s, %s, %s, 'gmail', 'booking@example.test', 'active', %s::jsonb)",
        (
            uuid4(),
            tenant_id,
            assistant_id,
            json.dumps({"google_features": ["calendar"]}),
        ),
    )
    settings = SimpleNamespace(
        google_api_key="test-key",
        text_agent_model="gemini-test",
        whatsapp_mode="fake",
        fake_outbox_path=tmp_path / "outbox.jsonl",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "missing-prices.yaml",
        calendar_fake_path=tmp_path / "calendar.json",
        calendar_fake_delay_ms=0,
        calendar_mode="fake",
        gmail_mode="fake",
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(booking_service, "get_settings", lambda: settings)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(google_auth, "get_settings", lambda: settings)

    async def load_route(*_args, **_kwargs):
        return SimpleNamespace(config=config, digest=None, context_card=None)

    monkeypatch.setattr(job_handlers, "load_assistant_route", load_route)
    sequences = [
        [
            _tool_response(
                "check_availability",
                {"appointment_type": "consultation", "preference_text": "Tuesday"},
            ),
            _text_response("I can offer one of those times."),
        ]
    ]
    prompts: list[str] = []
    calls: list[dict] = []

    class Models:
        def __init__(self, responses):
            self.responses = list(responses)

        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            contents = kwargs.get("contents")
            if isinstance(contents, str):
                prompts.append(contents)
            else:
                prompts.append(str(contents[0]))
            return self.responses.pop(0)

    def make_client(_api_key):
        models = Models(sequences.pop(0))
        return SimpleNamespace(
            aio=SimpleNamespace(models=models, aclose=AsyncMock(return_value=None))
        )

    monkeypatch.setattr(job_handlers, "make_genai_client", make_client)

    async def signed_message(message_id: str, body_text: str):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "123456"},
                                "contacts": [
                                    {
                                        "wa_id": "447700900123",
                                        "profile": {"name": "Alex Example"},
                                    }
                                ],
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": "447700900123",
                                        "timestamp": str(int(datetime.now(UTC).timestamp())),
                                        "type": "text",
                                        "text": {"body": body_text},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        body = json.dumps(payload).encode()
        signature = hmac.new(b"booking-secret", body, hashlib.sha256).hexdigest()
        monkeypatch.setattr(
            webhooks,
            "get_settings",
            lambda: SimpleNamespace(whatsapp_mode="fake", whatsapp_app_secret="booking-secret"),
        )
        response = await api_client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
        )
        assert response.status_code == 200, response.text
        return graph_env["execute"](
            "SELECT id, conversation_id FROM messages WHERE tenant_id = %s AND external_id = %s",
            (tenant_id, message_id),
        )[0]

    first_message_id, conversation_id = await signed_message(
        "wamid.booking-first", "What appointment times are available?"
    )
    assert graph_env["execute"](
        "SELECT count(*) FROM jobs WHERE tenant_id = %s AND kind = 'message_reply'",
        (tenant_id,),
    ) == [(1,)]
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(first_message_id)},
        )

    async with tenant_session(tenant_id) as session:
        first_assistant = await session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.ts.desc(), Message.id.desc())
        )
        availability_call = next(
            call
            for call in first_assistant.meta["tool_calls"]
            if call["name"] == "check_availability"
        )
        assert availability_call["result"]["matched_preference"] is False
        offered_slot = availability_call["result"]["slots"][0]

    second_message_id, second_conversation_id = await signed_message(
        "wamid.booking-second", "Yes, book that time. My name is Alex Example."
    )
    assert second_conversation_id == conversation_id
    assert graph_env["execute"](
        "SELECT count(*) FROM jobs WHERE tenant_id = %s AND kind = 'message_reply'",
        (tenant_id,),
    ) == [(2,)]
    sequences.append(
        [
            _tool_response(
                "book_appointment",
                {"slot_id": offered_slot["slot_id"], "name": "Alex Example"},
            ),
            _text_response("Your consultation is booked."),
        ]
    )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(second_message_id)},
        )

    assert offered_slot["slot_id"] in prompts[-2]
    assert "Note: slots offered —" in prompts[-2]
    assert all("config" in call for call in calls)
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        booking = await session.scalar(
            select(CalendarBooking).where(CalendarBooking.conversation_id == conversation_id)
        )
        system_message = await session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.SYSTEM,
            )
            .order_by(Message.ts.desc(), Message.id.desc())
        )
        commitment = await session.scalar(
            select(Commitment).where(Commitment.source_message_id == system_message.id)
        )
    assert booking is not None
    assert commitment is not None and commitment.kind == "appointment"
    assert conversation.metrics["booking"]["booking_id"] == str(booking.id)
    outbox = [json.loads(line) for line in settings.fake_outbox_path.read_text().splitlines()]
    assert len(outbox) == 2
    assert outbox[-1]["body"] == "Your consultation is booked."


@pytest.mark.asyncio
async def test_whatsapp_provider_error_is_returned_as_tool_error_not_job_failure(
    graph_env, monkeypatch, tmp_path
):
    from inbound_core.config import AssistantConfig

    from inbound_api.db import tenant_session
    from inbound_api.integrations.calendar import CalendarError
    from inbound_api.services import job_handlers
    from inbound_api.services.booking import BookingContext

    tenant_id = graph_env["tenant_id"]
    config = AssistantConfig.model_validate(_whatsapp_booking_config())
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config.model_dump(mode="json")), graph_env["assistant_id"]),
    )
    settings = SimpleNamespace(
        google_api_key="test-key",
        text_agent_model="gemini-test",
        whatsapp_mode="fake",
        fake_outbox_path=tmp_path / "error-outbox.jsonl",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "missing-prices.yaml",
    )
    monkeypatch.setattr(job_handlers, "get_settings", lambda: settings)

    async def load_route(*_args, **_kwargs):
        return SimpleNamespace(config=config, digest=None, context_card=None)

    class FailingProvider:
        name = "fake"

        async def free_busy(self, *_args, **_kwargs):
            raise CalendarError("calendar offline")

    async def context(*_args, **_kwargs):
        return BookingContext(
            config=config,
            booking=config.booking,
            connection=None,
            provider=FailingProvider(),
            timezone=config.office_hours.timezone,
        )

    monkeypatch.setattr(job_handlers, "load_assistant_route", load_route)
    monkeypatch.setattr(job_handlers, "booking_context", context)
    responses = [
        _tool_response(
            "check_availability",
            {"appointment_type": "consultation", "preference_text": "any time"},
        ),
        _text_response("I could not check the diary just now."),
    ]

    class Models:
        async def generate_content(self, **_kwargs):
            return responses.pop(0)

    monkeypatch.setattr(
        job_handlers,
        "make_genai_client",
        lambda _key: SimpleNamespace(
            aio=SimpleNamespace(
                models=Models(),
                aclose=AsyncMock(return_value=None),
            )
        ),
    )
    message_id, conversation_id = await _insert_test_message(
        graph_env,
        datetime.now(UTC) - timedelta(seconds=2),
        "wamid.booking-provider-error",
    )
    async with tenant_session(tenant_id) as session:
        await job_handlers.message_reply(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(message_id)},
        )
        assistant = await session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.ts.desc(), Message.id.desc())
        )
    call = assistant.meta["tool_calls"][0]
    assert call["result"] == {
        "ok": False,
        "error": "The diary can't be checked right now.",
    }
    assert assistant.text == "I could not check the diary just now."
    assert settings.fake_outbox_path.exists()
