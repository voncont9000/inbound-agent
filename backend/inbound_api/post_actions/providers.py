from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request

import httpx
from inbound_core.knowledge import pinned_opener

from inbound_api.integrations.google_auth import (
    action_google_connection_for,
    google_access_token,
)
from inbound_api.jobs import PermanentJobError
from inbound_api.models import ChannelConnection
from inbound_api.services.url_safety import is_public_address, is_public_http_url
from inbound_api.settings import Settings, get_settings

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


class ActionSkipped(Exception):
    def __init__(self, reason: str, setup_task: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.setup_task = setup_task


class PermanentActionError(PermanentJobError):
    pass


class EmailSender(Protocol):
    async def send(
        self,
        *,
        assistant_id,
        tenant_id,
        to: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]: ...


class SheetsWriter(Protocol):
    async def append_row(
        self,
        *,
        connection: ChannelConnection,
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str],
        row: list[str],
    ) -> dict[str, Any]: ...


class WebhookSender(Protocol):
    async def post(self, *, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]: ...


class GmailEmailSender:
    def __init__(
        self,
        session,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.settings = settings or get_settings()

    async def send(
        self,
        *,
        assistant_id,
        tenant_id,
        to: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        connection = await action_google_connection_for(
            self.session,
            assistant_id=assistant_id,
            tenant_id=tenant_id,
            feature="gmail",
        )
        if connection is None:
            raise ActionSkipped(
                "No Gmail account with email-send access is connected in this workspace",
                {"message": "Connect a Gmail account to send follow-up emails"},
            )
        token = await google_access_token(connection)
        message = EmailMessage()
        message["To"] = ", ".join(to)
        message["From"] = connection.external_id
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20, follow_redirects=False
        ) as client:
            response = await client.post(
                f"{GMAIL_API}/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
        _raise_http_response(response, "Gmail send")
        return {"message_id": response.json().get("id"), "from": connection.external_id}


class ResendEmailSender:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.transport = transport
        self.settings = settings or get_settings()

    async def send(
        self,
        *,
        assistant_id,
        tenant_id=None,
        to: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        if not self.settings.resend_api_key or not self.settings.email_from:
            raise PermanentActionError("Resend email settings are incomplete")
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20, follow_redirects=False
        ) as client:
            response = await client.post(
                f"{self.settings.resend_base_url.rstrip('/')}/emails",
                headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                json={
                    "from": self.settings.email_from,
                    "to": to,
                    "subject": subject,
                    "text": body,
                },
            )
        _raise_http_response(response, "Resend send")
        return {"message_id": response.json().get("id")}


class FakeEmailSender:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "emails.jsonl"

    async def send(
        self,
        *,
        assistant_id,
        tenant_id=None,
        to: list[str],
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        message_id = f"fake-{datetime.now(UTC).timestamp()}"
        _append_jsonl(
            self.path,
            {
                "ts": datetime.now(UTC).isoformat(),
                "assistant_id": str(assistant_id),
                "to": to,
                "subject": subject,
                "body": body,
                "message_id": message_id,
            },
        )
        return {"message_id": message_id}


class OffEmailSender:
    async def send(self, **kwargs) -> dict[str, Any]:
        raise ActionSkipped("Email sending is off")


class FakeSheetsWriter:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "sheets.jsonl"

    async def append_row(
        self,
        *,
        connection: ChannelConnection,
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str],
        row: list[str],
    ) -> dict[str, Any]:
        _append_jsonl(
            self.path,
            {
                "ts": datetime.now(UTC).isoformat(),
                "assistant_id": str(connection.assistant_id),
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "headers": headers,
                "row": row,
            },
        )
        result = {"updated_range": f"'{sheet_name}'!A1"}
        account_email = getattr(connection, "external_id", None)
        if account_email:
            result["account_email"] = account_email
        return result


class GoogleSheetsWriter:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.transport = transport

    async def append_row(
        self,
        *,
        connection: ChannelConnection,
        spreadsheet_id: str,
        sheet_name: str,
        headers: list[str],
        row: list[str],
    ) -> dict[str, Any]:
        token = await google_access_token(connection)
        sheet_range = f"'{sheet_name.replace(chr(39), chr(39) * 2)}'"
        encoded_range = quote(sheet_range, safe="")
        base_url = f"{SHEETS_API}/{quote(spreadsheet_id, safe='')}/values"
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20, follow_redirects=False
        ) as client:
            header_response = await client.get(
                f"{base_url}/{encoded_range}!1:1",
                headers={"Authorization": f"Bearer {token}"},
            )
            _raise_http_response(header_response, "Google Sheets header lookup")
            if not header_response.json().get("values"):
                write_response = await client.put(
                    f"{base_url}/{encoded_range}!A1",
                    params={"valueInputOption": "USER_ENTERED"},
                    headers={"Authorization": f"Bearer {token}"},
                    json={"values": [headers]},
                )
                _raise_http_response(write_response, "Google Sheets header write")
            append_response = await client.post(
                f"{base_url}/{encoded_range}!A1:append",
                params={
                    "valueInputOption": "USER_ENTERED",
                    "insertDataOption": "INSERT_ROWS",
                },
                headers={"Authorization": f"Bearer {token}"},
                json={"values": [row]},
            )
        _raise_http_response(append_response, "Google Sheets append")
        return {
            "updated_range": append_response.json().get("updates", {}).get("updatedRange"),
            "account_email": connection.external_id,
        }


class OffSheetsWriter:
    async def append_row(self, **kwargs) -> dict[str, Any]:
        raise ActionSkipped("Google Sheets writing is off")


class FakeWebhookSender:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "webhooks.jsonl"

    async def post(self, *, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        _append_jsonl(
            self.path,
            {
                "ts": datetime.now(UTC).isoformat(),
                "url": url,
                "headers": headers,
                "body": json.loads(body),
            },
        )
        return {"status_code": 200, "event_id": headers.get("X-Inbound-Event-Id")}


class HttpWebhookSender:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.transport = transport
        self.settings = settings or get_settings()

    async def post(self, *, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        if (
            self.settings.app_env not in {"development", "test"}
            and urlsplit(url).scheme.casefold() != "https"
        ):
            raise PermanentActionError("Webhook URLs must use HTTPS")
        allow_private = self.settings.post_action_webhook_allow_private_urls
        if not allow_private and not is_public_http_url(url):
            raise PermanentActionError("Webhook URL is blocked by the network safety policy")
        if self.transport is not None:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                response = await client.post(url, content=body, headers=headers)
            _raise_http_response(response, "Webhook delivery", redirect_message=True)
            return {
                "status_code": response.status_code,
                "event_id": headers.get("X-Inbound-Event-Id"),
            }

        allow_address = (lambda address: True) if allow_private else is_public_address
        try:
            status, response_headers = await asyncio.to_thread(
                _send_pinned_webhook,
                url,
                body,
                headers,
                allow_address,
            )
        except HTTPError as error:
            if 300 <= error.code < 400:
                raise PermanentActionError("Webhook redirects are not followed") from error
            if error.code == 429 or error.code >= 500:
                raise RuntimeError(f"Webhook receiver returned {error.code}") from error
            raise PermanentActionError(
                f"Webhook receiver rejected the event ({error.code})"
            ) from error
        except URLError as error:
            reason = error.reason
            if isinstance(reason, OSError) and "blocked by address safety policy" in str(reason):
                raise PermanentActionError(
                    "Webhook URL is blocked by the network safety policy"
                ) from error
            raise RuntimeError(f"Webhook delivery failed: {reason}") from error
        except TimeoutError as error:
            raise RuntimeError("Webhook delivery timed out") from error
        except OSError as error:
            if "blocked by address safety policy" in str(error):
                raise PermanentActionError(
                    "Webhook URL is blocked by the network safety policy"
                ) from error
            raise RuntimeError(f"Webhook delivery failed: {error}") from error
        return {
            "status_code": status,
            "event_id": headers.get("X-Inbound-Event-Id"),
        }


class OffWebhookSender:
    async def post(self, **kwargs) -> dict[str, Any]:
        raise ActionSkipped("Webhook delivery is off")


def email_sender_from_settings(
    session,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmailSender:
    settings = settings or get_settings()
    if settings.email_sender == "off":
        return OffEmailSender()
    if settings.email_sender == "fake":
        return FakeEmailSender(settings.post_actions_fake_dir)
    if settings.email_sender == "gmail":
        return GmailEmailSender(session, transport=transport, settings=settings)
    return ResendEmailSender(transport=transport, settings=settings)


async def sheets_writer_for(
    session,
    assistant_id,
    *,
    tenant_id,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[ChannelConnection, SheetsWriter]:
    settings = settings or get_settings()
    connection = await action_google_connection_for(
        session,
        assistant_id=assistant_id,
        tenant_id=tenant_id,
        feature="sheets",
    )
    if connection is None:
        raise ActionSkipped(
            "No Google account with Sheets access is connected in this workspace",
            {"message": "Connect Google Sheets to log conversations"},
        )
    if settings.sheets_writer == "off":
        return connection, OffSheetsWriter()
    if settings.sheets_writer == "fake":
        return connection, FakeSheetsWriter(settings.post_actions_fake_dir)
    return connection, GoogleSheetsWriter(transport=transport)


def webhook_sender_from_settings(
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebhookSender:
    settings = settings or get_settings()
    if settings.webhook_sender == "off":
        return OffWebhookSender()
    if settings.webhook_sender == "fake":
        return FakeWebhookSender(settings.post_actions_fake_dir)
    return HttpWebhookSender(transport=transport, settings=settings)


def sign_webhook(secret: str, body: bytes) -> str:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={signature}"


def _raise_http_response(
    response: httpx.Response,
    operation: str,
    *,
    redirect_message: bool = False,
) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if redirect_message and 300 <= status < 400:
        raise PermanentActionError("Webhook redirects are not followed")
    if status == 429 or status >= 500:
        raise httpx.HTTPStatusError(
            f"{operation} temporarily failed ({status})",
            request=response.request,
            response=response,
        )
    raise PermanentActionError(f"{operation} rejected the request ({status})")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _send_pinned_webhook(url, body, headers, allow_address) -> tuple[int, Any]:
    request = Request(url, data=body, headers=headers, method="POST")
    with pinned_opener(allow_address).open(request, timeout=10) as response:
        status = response.getcode()
        response.read()
        return status, response.headers
