import json

import httpx
import pytest

from inbound_api.post_actions.providers import (
    FakeEmailSender,
    FakeSheetsWriter,
    FakeWebhookSender,
    HttpWebhookSender,
    PermanentActionError,
    ResendEmailSender,
    sign_webhook,
)
from inbound_api.settings import Settings


@pytest.mark.asyncio
async def test_fake_providers_append_rendered_records(tmp_path):
    email = FakeEmailSender(tmp_path)
    sheets = FakeSheetsWriter(tmp_path)
    webhook = FakeWebhookSender(tmp_path)

    await email.send(
        assistant_id="assistant-1",
        to=["caller@example.test"],
        subject="Thanks",
        body="Hello",
    )
    await sheets.append_row(
        connection=type("Connection", (), {"assistant_id": "assistant-1"})(),
        spreadsheet_id="sheet_1",
        sheet_name="Enquiries",
        headers=["Name"],
        row=["Example"],
    )
    await webhook.post(
        url="https://example.test/hook",
        body=b'{"event_id":"run:1"}',
        headers={"X-Inbound-Event-Id": "run:1"},
    )

    assert json.loads((tmp_path / "emails.jsonl").read_text())["to"] == ["caller@example.test"]
    assert json.loads((tmp_path / "sheets.jsonl").read_text())["row"] == ["Example"]
    assert json.loads((tmp_path / "webhooks.jsonl").read_text())["body"]["event_id"] == "run:1"


def test_webhook_signature_is_hmac_sha256():
    body = b'{"action_key":"hook","event_id":"run:1"}'
    signature = sign_webhook("shared-secret", body)
    assert signature.startswith("sha256=")
    assert len(signature) == 71


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, httpx.HTTPStatusError), (503, httpx.HTTPStatusError), (422, PermanentActionError)],
)
async def test_resend_classifies_provider_status(status, error_type):
    async def respond(_request):
        return httpx.Response(status, json={"message": "provider error"})

    sender = ResendEmailSender(
        settings=Settings(
            email_sender="resend",
            resend_api_key="test-key",
            email_from="Inbound <inbound@example.test>",
        ),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(error_type):
        await sender.send(
            assistant_id="assistant-1",
            to=["caller@example.test"],
            subject="Hello",
            body="Body",
        )


@pytest.mark.asyncio
async def test_webhook_rejects_blocked_destination(monkeypatch):
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(providers, "is_public_http_url", lambda _: False)
    sender = HttpWebhookSender(settings=Settings(webhook_sender="http"))

    with pytest.raises(PermanentActionError, match="network safety"):
        await sender.post(url="http://127.0.0.1/hook", body=b"{}", headers={})
