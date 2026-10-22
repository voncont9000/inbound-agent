import base64
import hashlib
import hmac
import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from inbound_api.post_actions.email_html import render_email_html
from inbound_api.post_actions.providers import (
    FakeEmailSender,
    FakeSheetsWriter,
    FakeWebhookSender,
    GmailEmailSender,
    GoogleSheetsWriter,
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
        html="<p>Hello</p>",
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
    assert json.loads((tmp_path / "emails.jsonl").read_text())["html"] == "<p>Hello</p>"
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
async def test_resend_sends_html_alongside_text():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "message-1"})

    sender = ResendEmailSender(
        settings=Settings(
            email_sender="resend",
            resend_api_key="test-key",
            email_from="Inbound <inbound@example.test>",
        ),
        transport=httpx.MockTransport(respond),
    )
    await sender.send(
        assistant_id="assistant-1",
        to=["caller@example.test"],
        subject="Hello",
        body="Plain text",
        html="<p>HTML</p>",
    )

    assert json.loads(requests[0].content)["text"] == "Plain text"
    assert json.loads(requests[0].content)["html"] == "<p>HTML</p>"


@pytest.mark.asyncio
async def test_webhook_rejects_blocked_destination(monkeypatch):
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(providers, "is_public_http_url", lambda _: False)
    sender = HttpWebhookSender(settings=Settings(webhook_sender="http"))

    with pytest.raises(PermanentActionError, match="network safety"):
        await sender.post(url="http://127.0.0.1/hook", body=b"{}", headers={})


@pytest.mark.asyncio
async def test_gmail_sender_posts_base64url_mime_and_classifies_status(monkeypatch):
    import inbound_api.post_actions.providers as providers

    connection = SimpleNamespace(external_id="firm@example.com")
    monkeypatch.setattr(
        providers,
        "action_google_connection_for",
        lambda *_args, **_kwargs: _resolved(connection),
    )
    monkeypatch.setattr(providers, "google_access_token", lambda *_: _resolved("access-token"))
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "message-1"})

    sender = GmailEmailSender(None, transport=httpx.MockTransport(respond))
    output = await sender.send(
        assistant_id="assistant-1",
        tenant_id="tenant-1",
        to=["caller@example.com"],
        subject="Your appointment",
        body="Hello from the firm",
        html="<p>Hello from the firm</p>",
    )
    assert output["from"] == "firm@example.com"
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/users/me/messages/send")
    assert request.headers["Authorization"] == "Bearer access-token"
    raw = request.content and json.loads(request.content)["raw"]
    mime = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    assert mime["From"] == "firm@example.com"
    assert mime["To"] == "caller@example.com"
    assert mime["Subject"] == "Your appointment"
    assert mime.get_content_type() == "multipart/alternative"
    parts = list(mime.iter_parts())
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert parts[0].get_content().strip() == "Hello from the firm"
    assert parts[1].get_content().strip() == "<p>Hello from the firm</p>"

    for status, error_type in ((403, PermanentActionError), (503, httpx.HTTPStatusError)):
        failing = GmailEmailSender(
            None,
            transport=httpx.MockTransport(
                lambda _request, status=status: httpx.Response(status, json={"error": "failure"})
            ),
        )
        with pytest.raises(error_type):
            await failing.send(
                assistant_id="assistant-1",
                tenant_id="tenant-1",
                to=["caller@example.com"],
                subject="Subject",
                body="Body",
            )


def test_email_html_escapes_content_and_formats_details_and_links():
    rendered = render_email_html(
        body_text=(
            "<script>alert('x')</script>\nSecond line\n\n"
            "Name: Alex Morgan\nPhone: +44 20 7946 0000\n\n"
            "https://app.example.test/conversations/123"
        ),
        business_name="A & B <Services>",
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in rendered
    assert "<br>Second line" in rendered
    assert '<table role="presentation"' in rendered
    assert "<th" in rendered and "Name" in rendered
    assert 'href="https://app.example.test/conversations/123"' in rendered
    assert "Open conversation" in rendered
    assert "A &amp; B &lt;Services&gt;" in rendered
    assert "Sent by A &amp; B &lt;Services&gt;" in rendered
    assert rendered.count('<th width="35%"') == 2
    assert rendered.count('<td valign="top"') == 2
    assert rendered.count("vertical-align:top") == 4


def test_email_html_renders_single_detail_line_as_bold_label_paragraph():
    rendered = render_email_html(
        body_text="Next step: Call back",
        business_name="Example",
    )

    assert "<p " in rendered
    assert "<strong>Next step:</strong> Call back</p>" in rendered
    assert "<th" not in rendered
    assert "<tbody>" not in rendered


@pytest.mark.asyncio
async def test_sheets_writer_headers_append_and_quotes_sheet_name(monkeypatch):
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(providers, "google_access_token", lambda *_: _resolved("sheets-token"))
    requests = []
    has_header = False

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"values": [["Name", "Email"]] if has_header else []})
        return httpx.Response(200, json={"updates": {"updatedRange": "Sheet!A2:B2"}})

    writer = GoogleSheetsWriter(transport=httpx.MockTransport(respond))
    connection = SimpleNamespace(
        assistant_id="assistant-1",
        external_id="account@example.test",
    )
    result = await writer.append_row(
        connection=connection,
        spreadsheet_id="sheet-id",
        sheet_name="Owner's Data",
        headers=["Name", "Email"],
        row=["Alex", "alex@example.com"],
    )
    assert [request.method for request in requests] == ["GET", "PUT", "POST"]
    assert all(b"Owner%27%27s%20Data" in request.url.raw_path for request in requests)
    header_request, append_request = requests[1:]
    assert header_request.url.params["valueInputOption"] == "USER_ENTERED"
    assert json.loads(header_request.content)["values"] == [["Name", "Email"]]
    assert append_request.url.params["valueInputOption"] == "USER_ENTERED"
    assert append_request.url.params["insertDataOption"] == "INSERT_ROWS"
    assert result["account_email"] == "account@example.test"
    assert json.loads(append_request.content)["values"] == [["Alex", "alex@example.com"]]

    requests.clear()
    has_header = True
    await writer.append_row(
        connection=connection,
        spreadsheet_id="sheet-id",
        sheet_name="Owner's Data",
        headers=["Name", "Email"],
        row=["Alex", "alex@example.com"],
    )
    assert [request.method for request in requests] == ["GET", "POST"]


def _resolved(value):
    async def result(*_args, **_kwargs):
        return value

    return result()


@pytest.mark.asyncio
async def test_http_webhook_pinned_local_receiver_signs_exact_body_and_rejects_redirect():
    received = []
    response_status = [200]

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((body, self.headers.get("X-Inbound-Signature")))
            self.send_response(response_status[0])
            self.send_header("Location", "/redirect-target")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        sender = HttpWebhookSender(
            settings=Settings(
                app_env="test",
                webhook_sender="http",
                post_action_webhook_allow_private_urls=True,
            )
        )
        secret = "stored-test-secret"
        body = json.dumps({"schema_version": "1", "event_id": "run:1"}).encode()
        signature = sign_webhook(secret, body)
        await sender.post(url=url, body=body, headers={"X-Inbound-Signature": signature})
        captured_body, captured_signature = received[-1]
        expected = "sha256=" + hmac.new(secret.encode(), captured_body, hashlib.sha256).hexdigest()
        assert captured_signature == expected
        assert json.loads(captured_body)["schema_version"] == "1"

        response_status[0] = 302
        with pytest.raises(PermanentActionError, match="redirects are not followed"):
            await sender.post(url=url, body=body, headers={"X-Inbound-Signature": signature})
        assert len(received) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
