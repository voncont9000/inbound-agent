import base64
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request

from inbound_api.channels import credentials, gmail_messages
from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.channels.gmail_oauth_state import issue_state, verify_state
from inbound_api.models import MessageRole
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


def test_include_internal_and_quote_stripping():
    message = make_message([("From", "user@example.com")])
    assert (
        gmail_messages.skip_reason(message, "owner@example.com", {"include_internal": True}) is None
    )
    assert gmail_messages.strip_quoted_history("New\n> quoted") == "New"
    assert gmail_messages.strip_quoted_history("New\nOn Tue, someone wrote:\nold") == "New"


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


def test_gmail_extraction_rows_include_attachment_text_only_for_gmail():
    message = SimpleNamespace(
        role=MessageRole.CALLER,
        text="I was dismissed.",
        ts=datetime(2026, 10, 9, tzinfo=UTC),
        meta={"attachments_text": {"dismissal-date.txt": "Termination date: 9 October 2026."}},
    )

    gmail_rows = _message_extraction_rows([message], include_attachments=True)
    other_channel_rows = _message_extraction_rows([message])

    assert (
        gmail_rows[0]["text"]
        == "I was dismissed.\n\n[Attachment dismissal-date.txt]\nTermination date: 9 October 2026."
    )
    assert other_channel_rows[0]["text"] == "I was dismissed."


def test_email_draft_prompt_requires_greeting_for_every_reply():
    prompt = _build_email_draft_prompt(
        "Business context",
        "Siobhan McAllister",
        "Demo & Co",
        "Customer: What is the fee?",
    )

    assert (
        "Start every draft, including follow-ups in the same thread, with a greeting line."
        in prompt
    )
    assert 'Use the customer\'s name if known (for example, "Dear Siobhan,")' in prompt
    assert 'otherwise start with "Hello,".' in prompt
    assert "Customer name, if known: Siobhan McAllister." in prompt


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
async def test_push_wakes_queued_reconciliation_job():
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    job = SimpleNamespace(run_after=scheduled_for, payload={"connection_id": "connection"})
    session = SimpleNamespace(scalar=AsyncMock(return_value=job))
    received_at = datetime.now(UTC).isoformat(timespec="microseconds")
    await enqueue_gmail_sync(session, uuid4(), received_at=received_at)
    assert job.run_after < scheduled_for
    assert job.payload["received_at"] == received_at
