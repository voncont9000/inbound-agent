import base64
import json
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from inbound_core.config import EmailTriageConfig, load_assistant_config
from inbound_core.extraction import build_intake_artifacts, extract_intake
from starlette.requests import Request

from inbound_api.channels import credentials, gmail_messages
from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.channels.gmail_oauth_state import issue_state, verify_state
from inbound_api.models import ConversationStatus, Message, MessageRole, Task
from inbound_api.routers import gmail as gmail_router
from inbound_api.routers import webhooks
from inbound_api.routers.gmail import exchange_google_code
from inbound_api.services import gmail_jobs
from inbound_api.services.gmail_jobs import _build_email_draft_prompt, _build_email_reply
from inbound_api.services.gmail_queue import enqueue_gmail_sync
from inbound_api.services.job_handlers import _message_extraction_rows


def make_message(headers=(), labels=("INBOX",)):
    return {
        "id": "m1",
        "labelIds": list(labels),
        "payload": {
            "headers": [{"name": key, "value": value} for key, value in headers],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"hello").decode().rstrip("=")},
        },
    }


@pytest.mark.parametrize(
    ("headers", "labels", "settings", "reason"),
    [
        ([], ["SENT"], {}, "provider_label"),
        ([("From", "owner@example.com")], ["INBOX"], {}, "self_sender"),
        ([("From", "user@example.com")], ["INBOX"], {}, "internal_sender"),
        ([("From", "noreply@outside.test")], ["INBOX"], {}, "automated_sender"),
        (
            [("From", "user@outside.test"), ("List-Unsubscribe", "<mailto:x>")],
            ["INBOX"],
            {},
            "mailing_list",
        ),
        ([("From", "user@outside.test"), ("Precedence", "bulk")], ["INBOX"], {}, "bulk_precedence"),
        (
            [("From", "user@outside.test"), ("Auto-Submitted", "auto-generated")],
            ["INBOX"],
            {},
            "auto_submitted",
        ),
        (
            [("From", "user@outside.test")],
            ["INBOX"],
            {"exclude_senders": ["user@outside.test"]},
            "excluded_sender",
        ),
        (
            [("From", "user@outside.test")],
            ["INBOX"],
            {"exclude_domains": ["outside.test"]},
            "excluded_domain",
        ),
    ],
)
def test_skip_rules(headers, labels, settings, reason):
    assert (
        gmail_messages.skip_reason(make_message(headers, labels), "owner@example.com", settings)
        == reason
    )


@pytest.mark.parametrize(
    ("mailbox", "sender", "settings", "expected"),
    [
        ("a@gmail.com", "b@gmail.com", {}, None),
        ("a@gmail.com", "a@gmail.com", {}, "self_sender"),
        ("a@firm.co.uk", "b@firm.co.uk", {}, "internal_sender"),
        ("a@firm.co.uk", "b@firm.co.uk", {"include_internal": True}, None),
    ],
)
def test_public_mailbox_domains_are_not_treated_as_internal(mailbox, sender, settings, expected):
    message = make_message([("From", sender)])

    assert gmail_messages.skip_reason(message, mailbox, settings) == expected


def test_include_internal_and_quote_stripping():
    message = make_message([("From", "user@example.com")])
    assert (
        gmail_messages.skip_reason(message, "owner@example.com", {"include_internal": True}) is None
    )
    assert gmail_messages.strip_quoted_history("New\n> quoted") == "New"
    assert gmail_messages.strip_quoted_history("New\nOn Tue, someone wrote:\nold") == "New"


@pytest.mark.parametrize(
    ("headers", "field", "contains"),
    [
        ([("To", "team@vinci-labs.tech")], "to", "@vinci-labs.tech"),
        ([("Delivered-To", "team@vinci-labs.tech")], "to", "TEAM@VINCI-LABS.TECH"),
        ([("Cc", "team@vinci-labs.tech")], "to", "@vinci-labs.tech"),
        ([("X-Original-To", "team@vinci-labs.tech")], "to", "@vinci-labs.tech"),
        ([("X-Forwarded-To", "team@vinci-labs.tech")], "to", "@vinci-labs.tech"),
        ([("Envelope-To", "team@vinci-labs.tech")], "to", "@vinci-labs.tech"),
        ([("Subject", "Question about support")], "subject", "QUESTION"),
        ([("From", "Alex Example <alex@example.net>")], "from", "alex example"),
    ],
)
def test_include_rules_match_headers_case_insensitively(headers, field, contains):
    message = make_message([("From", "customer@example.net"), *headers])
    settings = {"include_rules": [{"field": field, "contains": f" {contains} "}]}

    assert gmail_messages.skip_reason(message, "owner@example.com", settings) is None


def test_include_rules_filter_non_matching_messages_and_empty_rules_are_unchanged():
    message = make_message(
        [("From", "customer@example.net"), ("To", "team@other.test"), ("Subject", "Question")]
    )
    assert (
        gmail_messages.skip_reason(
            message,
            "owner@example.com",
            {"include_rules": [{"field": "to", "contains": "@vinci-labs.tech"}]},
        )
        == "filtered_out"
    )
    assert gmail_messages.skip_reason(message, "owner@example.com", {"include_rules": []}) is None
    assert gmail_messages.skip_reason(message, "owner@example.com", {}) is None


def test_reply_thread_headers_and_re_subject():
    raw = _build_email_reply(
        to="customer@example.test",
        subject="Re: Re: Fees",
        body="Thank you",
        message_id="<inbound@local>",
        references="<prior@local>",
    )
    email = BytesParser(policy=default).parsebytes(raw)
    assert email["Subject"] == "Re: Fees"
    assert email["In-Reply-To"] == "<inbound@local>"
    assert email["References"] == "<prior@local> <inbound@local>"


def test_gmail_extraction_rows_include_headers_and_attachment_text_only_for_gmail():
    message = SimpleNamespace(
        role=MessageRole.CALLER,
        text="I was dismissed.",
        ts=datetime(2026, 10, 9, tzinfo=UTC),
        meta={
            "from": "siobhan.mcallister@example.com",
            "reply_to": "reply@example.test",
            "subject": "Urgent: dismissal claim",
            "attachments_text": {"dismissal-date.txt": "Termination date: 9 October 2026."},
        },
    )
    metrics = {
        "contact_name": "Siobhan McAllister",
        "from_email": "siobhan.mcallister@example.com",
        "subject": "Urgent: dismissal claim",
    }

    gmail_rows = _message_extraction_rows(
        [message],
        channel="gmail",
        conversation_metrics=metrics,
    )
    other_channel_rows = _message_extraction_rows(
        [message],
        channel="whatsapp",
        conversation_metrics=metrics,
    )

    assert (
        gmail_rows[0]["text"] == "From: Siobhan McAllister <siobhan.mcallister@example.com>\n"
        "Subject: Urgent: dismissal claim\n"
        "Reply-To: reply@example.test\n\n"
        "I was dismissed.\n\n[Attachment dismissal-date.txt]\n"
        "Termination date: 9 October 2026."
    )
    assert other_channel_rows[0]["text"] == "I was dismissed."
    same_reply_message = SimpleNamespace(
        role=MessageRole.CALLER,
        text="I was dismissed.",
        ts=message.ts,
        meta={
            **message.meta,
            "reply_to": "SIOBHAN.MCALLISTER@example.com",
        },
    )
    same_reply_row = _message_extraction_rows(
        [same_reply_message],
        channel="gmail",
        conversation_metrics=metrics,
    )
    assert "Reply-To:" not in same_reply_row[0]["text"]


@pytest.mark.asyncio
async def test_gmail_from_header_evidence_survives_extraction_and_merge():
    from_line = "From: Siobhan McAllister <siobhan.mcallister@example.com>"
    message = SimpleNamespace(
        role=MessageRole.CALLER,
        text="I was dismissed.",
        ts=datetime(2026, 10, 9, tzinfo=UTC),
        meta={
            "from": "siobhan.mcallister@example.com",
            "subject": "Urgent: dismissal claim",
            "attachments_text": {"dismissal-date.txt": "Termination date: 9 October 2026."},
        },
    )
    rows = _message_extraction_rows(
        [message],
        channel="gmail",
        conversation_metrics={"contact_name": "Siobhan McAllister"},
    )
    response = {
        "service": "employment",
        "fields": [
            {
                "name": "caller_name",
                "value": "Siobhan McAllister",
                "confidence": 0.99,
                "evidence": from_line,
                "confirmed_by_caller": False,
            },
            {
                "name": "email",
                "value": "siobhan.mcallister@example.com",
                "confidence": 0.99,
                "evidence": from_line,
                "confirmed_by_caller": False,
            },
        ],
        "summary": "The customer asks about a dismissal.",
    }
    generate_content = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(response), usage_metadata=None)
    )
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    config_path = (
        Path(__file__).resolve().parents[2] / "voice" / "config" / "assistants" / "demo_firm.yaml"
    )
    config = load_assistant_config(config_path)

    result = await extract_intake(
        rows,
        {},
        config,
        client,
        "gemini-3.8-flash",
        call_started_at=datetime(2026, 10, 9, tzinfo=UTC),
        channel="gmail",
    )
    post_call, merged = build_intake_artifacts({}, result, config)
    prompt = generate_content.await_args.kwargs["contents"]

    assert from_line in prompt
    assert "[Attachment dismissal-date.txt]\nTermination date: 9 October 2026." in prompt
    for field, value in (
        ("caller_name", "Siobhan McAllister"),
        ("email", "siobhan.mcallister@example.com"),
    ):
        assert post_call["fields"][field]["value"] == value
        assert post_call["fields"][field]["evidence"] == from_line
        assert merged["fields"][field]["value"] == value
        assert merged["fields"][field]["sources"]["post_call"]["evidence"] == from_line


def test_email_draft_prompt_requires_greeting_for_every_reply():
    prompt = _build_email_draft_prompt(
        "Business context",
        "Siobhan McAllister",
        "Demo & Co",
        "Customer: What is the fee?",
        EmailTriageConfig(),
    )

    assert (
        "Start every draft, including follow-ups in the same thread, with a greeting line."
        in prompt
    )
    assert 'Use the customer\'s name if known (for example, "Dear Siobhan,")' in prompt
    assert 'otherwise start with "Hello,".' in prompt
    assert "Customer name, if known: Siobhan McAllister." in prompt


def test_email_draft_prompt_includes_configured_triage_criteria_and_unsure_rule():
    triage = EmailTriageConfig(
        needs_human="A staff member must review a complaint about an ongoing matter."
    )
    prompt = _build_email_draft_prompt(
        "Business context",
        None,
        "Demo & Co",
        "Customer: I am unhappy with the service.",
        triage,
    )

    assert (
        "- needs_human: A staff member must review a complaint about an ongoing matter." in prompt
    )
    assert (
        "- If you are unsure whether an email is spam or not_relevant rather than enquiry or "
        "needs_human, choose needs_human."
    ) in prompt
    assert (
        "Return JSON only with category, reason (one short sentence saying why you chose the "
        "category, for staff) and reply (the email body for enquiry, otherwise null)."
    ) in prompt


async def _run_email_draft_result(monkeypatch, result):
    now = datetime.now(UTC)
    inbound = SimpleNamespace(
        id=uuid4(),
        role=MessageRole.CALLER,
        text="Could you tell me your prices?",
        ts=now,
        meta={
            "gmail_message_id": "message-1",
            "from": "customer@example.test",
            "subject": "Question",
            "message_id": "<message-1@example.test>",
        },
    )
    conversation = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        assistant_id=uuid4(),
        person_id=None,
        status=ConversationStatus.ACTIVE,
        ended_at=None,
        metrics={
            "channel_connection_id": str(uuid4()),
            "from_email": "customer@example.test",
            "gmail_thread_id": "thread-1",
        },
    )
    connection = SimpleNamespace(settings={"signature": "Demo & Co"})
    config = SimpleNamespace(
        business_name="Demo & Co",
        email_triage=EmailTriageConfig(
            needs_human="A staff member must review a complaint about an ongoing matter."
        ),
    )
    route = SimpleNamespace(config=config, digest=None, context_card=None)
    additions = Mock()
    session = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda model, _id: {
                gmail_jobs.Conversation: conversation,
                gmail_jobs.Message: inbound,
                gmail_jobs.Assistant: SimpleNamespace(),
                gmail_jobs.ChannelConnection: connection,
            }[model]
        ),
        scalar=AsyncMock(side_effect=[inbound, None]),
        scalars=AsyncMock(return_value=[inbound]),
        add=additions,
    )
    generated = AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(result), usage_metadata=None)
    )
    genai_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generated),
            aclose=AsyncMock(),
        )
    )
    gmail_client = SimpleNamespace(
        create_draft=AsyncMock(return_value="draft-1"),
        delete_draft=AsyncMock(),
    )
    monkeypatch.setattr(
        gmail_jobs,
        "get_settings",
        lambda: SimpleNamespace(
            gmail_mode="fake",
            google_api_key="test-key",
            text_agent_model="test-model",
            message_extract_delay_seconds=30,
        ),
    )
    monkeypatch.setattr(gmail_jobs, "load_assistant_route", AsyncMock(return_value=route))
    monkeypatch.setattr(gmail_jobs, "render_business_context", lambda *args, **kwargs: "Context")
    monkeypatch.setattr(gmail_jobs, "make_genai_client", lambda _key: genai_client)
    monkeypatch.setattr(gmail_jobs, "gmail_client_for", lambda *_args: gmail_client)
    monkeypatch.setattr(gmail_jobs, "_label", AsyncMock())
    monkeypatch.setattr(gmail_jobs, "enqueue", AsyncMock())

    await gmail_jobs.email_draft(
        session,
        {"conversation_id": str(conversation.id), "message_id": str(inbound.id)},
    )
    return conversation, additions, generated


@pytest.mark.asyncio
async def test_email_draft_response_schema_requires_reason(monkeypatch):
    _, _, generated = await _run_email_draft_result(
        monkeypatch,
        {"category": "spam", "reason": "Bulk marketing.", "reply": None},
    )

    schema = generated.await_args.kwargs["config"]["response_schema"]
    assert "reason" in schema["properties"]
    assert schema["properties"]["reason"] == {"type": "STRING"}
    assert schema["required"] == ["category", "reason", "reply"]


@pytest.mark.asyncio
async def test_needs_human_task_uses_stripped_model_reason(monkeypatch):
    conversation, additions, _ = await _run_email_draft_result(
        monkeypatch,
        {
            "category": "needs_human",
            "reason": "  A person should review this complaint. \n",
            "reply": None,
        },
    )

    tasks = [call.args[0] for call in additions.call_args_list if isinstance(call.args[0], Task)]
    assert len(tasks) == 1
    assert tasks[0].payload == {"reason": "A person should review this complaint."}
    assert conversation.metrics["email_triage"] == {
        "category": "needs_human",
        "reason": "A person should review this complaint.",
    }


@pytest.mark.parametrize("reason", ["", None])
@pytest.mark.asyncio
async def test_needs_human_task_falls_back_when_reason_is_blank_or_missing(monkeypatch, reason):
    result = {"category": "needs_human", "reply": None}
    if reason is not None:
        result["reason"] = reason
    conversation, additions, _ = await _run_email_draft_result(monkeypatch, result)

    tasks = [call.args[0] for call in additions.call_args_list if isinstance(call.args[0], Task)]
    assert len(tasks) == 1
    assert tasks[0].payload == {"reason": "Email requires human review"}
    assert conversation.metrics["email_triage"] == {"category": "needs_human", "reason": ""}


@pytest.mark.asyncio
async def test_spam_stores_triage_metrics_without_task_or_draft(monkeypatch):
    conversation, additions, _ = await _run_email_draft_result(
        monkeypatch,
        {"category": "spam", "reason": "Unsolicited bulk marketing.", "reply": None},
    )

    added = [call.args[0] for call in additions.call_args_list]
    assert not any(isinstance(item, Task) for item in added)
    assert not any(isinstance(item, Message) for item in added)
    assert conversation.metrics["email_triage"] == {
        "category": "spam",
        "reason": "Unsolicited bulk marketing.",
    }
    assert conversation.status == ConversationStatus.COMPLETED


def test_oauth_state_and_credentials(monkeypatch):
    monkeypatch.setattr(
        "inbound_api.channels.gmail_oauth_state.get_settings",
        lambda: SimpleNamespace(voice_service_token="test-secret"),
    )
    ids = (uuid4(), uuid4(), uuid4())
    state = issue_state(*ids)
    assert verify_state(state) == ids
    payload, signature = state.split(".")
    with pytest.raises(ValueError):
        verify_state(f"{payload}x.{signature}")
    with pytest.raises(ValueError):
        verify_state(issue_state(*ids, ttl=-1))
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        credentials, "get_settings", lambda: SimpleNamespace(credentials_encryption_key=key)
    )
    encrypted = credentials.encrypt_credential("refresh-token")
    assert "refresh-token" not in encrypted
    assert credentials.decrypt_credential(encrypted) == "refresh-token"


@pytest.mark.asyncio
async def test_oauth_exchange_uses_mock_transport():
    def respond(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh"})
        assert request.headers["authorization"] == "Bearer access"
        return httpx.Response(200, json={"emailAddress": "MAILBOX@example.test"})

    settings = SimpleNamespace(
        google_oauth_client_id="client",
        google_oauth_client_secret="secret",
        google_oauth_redirect_url="https://app.test/callback",
    )
    token, profile = await exchange_google_code(
        "auth-code",
        settings,
        transport=httpx.MockTransport(respond),
    )
    assert token["refresh_token"] == "refresh"
    assert profile["emailAddress"] == "MAILBOX@example.test"


@pytest.mark.asyncio
async def test_fake_provider_message_history(tmp_path):
    client = FakeGmailClient("mailbox@example.test", tmp_path)
    client.append_message(make_message([("From", "user@outside.test")]))
    assert await client.list_history("1") == (["m1"], "2")
    assert (await client.get_message("m1"))["payload"]["mimeType"] == "text/plain"


def test_production_requires_google_oauth_and_pubsub():
    from pydantic import ValidationError

    from inbound_api.settings import Settings

    with pytest.raises(ValidationError, match="Gmail production requires"):
        Settings(
            app_env="production",
            voice_service_token="production-secret",
            whatsapp_mode="graph",
            whatsapp_app_secret="app-secret",
            whatsapp_verify_token="verify-token",
            whatsapp_access_token="access-token",
        )


def test_production_allows_gmail_off_without_google_settings():
    from inbound_api.settings import Settings

    settings = Settings(
        app_env="production",
        voice_service_token="production-secret",
        whatsapp_mode="graph",
        whatsapp_app_secret="app-secret",
        whatsapp_verify_token="verify-token",
        whatsapp_access_token="access-token",
        gmail_mode="off",
        calendar_mode="off",
        email_sender="off",
        sheets_writer="off",
        webhook_sender="off",
    )
    assert settings.gmail_mode == "off"


@pytest.mark.asyncio
async def test_gmail_off_rejects_webhook_and_oauth_and_skips_sync(monkeypatch):
    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="off"),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/webhooks/gmail",
            "raw_path": b"/webhooks/gmail",
            "query_string": b"",
            "headers": [],
            "server": ("localhost", 8000),
            "client": ("test", 1234),
            "http_version": "1.1",
        }
    )
    with pytest.raises(HTTPException) as webhook_error:
        await webhooks.receive_gmail_webhook(request)
    assert webhook_error.value.status_code == 503

    monkeypatch.setattr(
        gmail_router,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="off"),
    )
    with pytest.raises(HTTPException) as oauth_error:
        await gmail_router.gmail_oauth_start(uuid4())
    assert oauth_error.value.status_code == 409
    assert oauth_error.value.detail == "Gmail is not enabled"

    monkeypatch.setattr(
        gmail_jobs,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="off"),
    )
    await gmail_jobs.gmail_sync(None, {})
    await gmail_jobs.email_draft(None, {})


@pytest.mark.asyncio
async def test_gmail_draft_generation_never_passes_booking_tools(graph_env, monkeypatch, tmp_path):
    from sqlalchemy import select

    from inbound_api.db import tenant_session
    from inbound_api.models import Message

    config_path = (
        Path(__file__).resolve().parents[2] / "voice" / "config" / "assistants" / "demo_firm.yaml"
    )
    base_config = load_assistant_config(config_path)
    config = type(base_config).model_validate(
        {
            **base_config.model_dump(mode="json"),
            "live_actions": ["check_availability", "book_appointment"],
            "booking": {
                "calendar_id": "primary",
                "appointment_types": [
                    {
                        "key": "consultation",
                        "label": "Consultation",
                        "duration_minutes": 30,
                        "when_to_use": "Initial appointment",
                    }
                ],
            },
        }
    )
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config.model_dump(mode="json")), graph_env["assistant_id"]),
    )
    connection_id = uuid4()
    graph_env["execute"](
        "INSERT INTO channel_connections "
        "(id, tenant_id, assistant_id, kind, external_id, status, settings) "
        "VALUES (%s, %s, %s, 'gmail', 'booking@example.test', 'active', '{}')",
        (connection_id, graph_env["tenant_id"], graph_env["assistant_id"]),
    )
    conversation_id, message_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    graph_env["execute"](
        "INSERT INTO conversations "
        "(id, tenant_id, assistant_id, channel, external_ref, status, started_at, metrics) "
        "VALUES (%s, %s, %s, 'gmail', 'gmail-booking-thread', 'active', %s, %s::jsonb)",
        (
            conversation_id,
            graph_env["tenant_id"],
            graph_env["assistant_id"],
            now,
            json.dumps(
                {
                    "gmail_thread_id": "thread-booking",
                    "channel_connection_id": str(connection_id),
                    "from_email": "alex@example.test",
                    "contact_name": "Alex",
                    "subject": "Appointment",
                }
            ),
        ),
    )
    graph_env["execute"](
        "INSERT INTO messages "
        "(id, tenant_id, conversation_id, role, text, ts, meta) "
        "VALUES (%s, %s, %s, 'caller', 'Please book a consultation.', %s, %s::jsonb)",
        (
            message_id,
            graph_env["tenant_id"],
            conversation_id,
            now,
            json.dumps(
                {
                    "from": "alex@example.test",
                    "subject": "Appointment",
                    "gmail_message_id": "gmail-message",
                }
            ),
        ),
    )
    settings = SimpleNamespace(
        gmail_mode="fake",
        google_api_key="test-key",
        text_agent_model="gemini-test",
        message_extract_delay_seconds=120,
        price_table_path=tmp_path / "missing-prices.yaml",
    )
    monkeypatch.setattr(gmail_jobs, "get_settings", lambda: settings)

    async def load_route(*_args, **_kwargs):
        return SimpleNamespace(config=config, digest=None, context_card=None)

    monkeypatch.setattr(gmail_jobs, "load_assistant_route", load_route)
    generation_calls = []

    class Models:
        async def generate_content(self, **kwargs):
            generation_calls.append(kwargs)
            return SimpleNamespace(
                text=json.dumps({"category": "enquiry", "reply": "Your consultation is booked."}),
                usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1),
            )

    monkeypatch.setattr(
        gmail_jobs,
        "make_genai_client",
        lambda _key: SimpleNamespace(
            aio=SimpleNamespace(models=Models(), aclose=AsyncMock(return_value=None))
        ),
    )

    class GmailClient:
        async def create_draft(self, thread_id, _raw):
            assert thread_id == "thread-booking"
            return "draft-1"

        async def ensure_label(self, name):
            return name

        async def add_labels(self, _message_id, _labels):
            return None

        async def delete_draft(self, _draft_id):
            return None

    monkeypatch.setattr(gmail_jobs, "gmail_client_for", lambda *_args: GmailClient())
    async with tenant_session(graph_env["tenant_id"]) as session:
        await gmail_jobs.email_draft(
            session,
            {"conversation_id": str(conversation_id), "message_id": str(message_id)},
        )
        draft = await session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
            )
        )

    assert len(generation_calls) == 1
    assert "tools" not in generation_calls[0]
    assert draft is not None and draft.meta["draft"] is True


@pytest.mark.asyncio
async def test_push_wakes_queued_reconciliation_job():
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    job = SimpleNamespace(run_after=scheduled_for, payload={"connection_id": "connection"})
    session = SimpleNamespace(scalar=AsyncMock(return_value=job))
    received_at = datetime.now(UTC).isoformat(timespec="microseconds")
    await enqueue_gmail_sync(session, uuid4(), received_at=received_at)
    assert job.run_after < scheduled_for
    assert job.payload["received_at"] == received_at
