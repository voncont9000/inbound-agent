from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx

from inbound_api.channels.credentials import decrypt_credential
from inbound_api.jobs import PermanentJobError

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class HistoryExpired(Exception):
    pass


class GmailClient(Protocol):
    async def get_profile(self) -> dict[str, Any]: ...

    async def watch(self, topic: str) -> str: ...

    async def list_history(self, start_history_id: str) -> tuple[list[str], str]: ...

    async def list_recent_inbox(self, newer_than_days: int) -> list[str]: ...

    async def get_message(self, message_id: str) -> dict[str, Any]: ...

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...

    async def create_draft(self, thread_id: str, raw: bytes) -> str: ...

    async def delete_draft(self, draft_id: str) -> None: ...

    async def ensure_label(self, name: str) -> str: ...

    async def add_labels(self, message_id: str, label_ids: list[str]) -> None: ...


class GoogleGmailClient:
    def __init__(self, connection, settings):
        self.connection = connection
        self.settings = settings
        if not connection.credentials_ciphertext:
            raise ValueError("Gmail refresh token is missing")
        self.refresh_token = decrypt_credential(connection.credentials_ciphertext)
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    async def _token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at - 30:
            return self._access_token
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.settings.google_oauth_client_id,
                    "client_secret": self.settings.google_oauth_client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        _raise_response(response)
        data = response.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
        return self._access_token

    async def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs):
        token = await self._token()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{GMAIL_API}/users/me/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        _raise_response(response, allow_404=allow_404)
        return response

    async def get_profile(self) -> dict[str, Any]:
        return (await self._request("GET", "profile")).json()

    async def watch(self, topic: str) -> str:
        return (await self._request("POST", "watch", json={"topicName": topic})).json()["historyId"]

    async def list_history(self, start_history_id: str) -> tuple[list[str], str]:
        try:
            response = await self._request(
                "GET",
                "history",
                allow_404=True,
                params={"startHistoryId": start_history_id, "historyTypes": "messageAdded"},
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise HistoryExpired from error
            raise
        data = response.json()
        ids = {
            item["message"]["id"]
            for event in data.get("history", [])
            for item in event.get("messagesAdded", [])
            if isinstance(item.get("message"), dict) and item["message"].get("id")
        }
        return list(ids), str(data.get("historyId", start_history_id))

    async def list_recent_inbox(self, newer_than_days: int) -> list[str]:
        response = await self._request(
            "GET",
            "messages",
            params={"q": f"in:inbox newer_than:{newer_than_days}d"},
        )
        return [item["id"] for item in response.json().get("messages", []) if item.get("id")]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return (
            await self._request(
                "GET", f"messages/{quote(message_id, safe='')}", params={"format": "full"}
            )
        ).json()

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        attachment_path = (
            f"messages/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}"
        )
        result = (
            await self._request(
                "GET",
                attachment_path,
            )
        ).json()
        return _b64decode(result.get("data", ""))

    async def create_draft(self, thread_id: str, raw: bytes) -> str:
        result = (
            await self._request(
                "POST",
                "drafts",
                json={
                    "message": {
                        "threadId": thread_id,
                        "raw": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                    }
                },
            )
        ).json()
        return result["id"]

    async def delete_draft(self, draft_id: str) -> None:
        try:
            await self._request("DELETE", f"drafts/{quote(draft_id, safe='')}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise

    async def ensure_label(self, name: str) -> str:
        response = await self._request("GET", "labels")
        for label in response.json().get("labels", []):
            if label.get("name") == name:
                return label["id"]
        result = (await self._request("POST", "labels", json={"name": name})).json()
        return result["id"]

    async def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        await self._request(
            "POST",
            f"messages/{quote(message_id, safe='')}/modify",
            json={"addLabelIds": label_ids},
        )


class FakeGmailClient:
    def __init__(self, mailbox: str, root: str | Path):
        self.mailbox = mailbox.strip().lower()
        self.root = Path(root) / quote(self.mailbox, safe="")
        self.messages_dir = self.root / "messages"
        self.attachments_dir = self.root / "attachments"
        self.drafts_dir = self.root / "drafts"

    def _read(self, path: Path, fallback):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return fallback

    def _write(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    async def get_profile(self) -> dict[str, Any]:
        profile = self._read(self.root / "profile.json", {})
        return {
            "emailAddress": self.mailbox,
            "historyId": str(profile.get("historyId", "1")),
            **profile,
        }

    async def watch(self, topic: str) -> str:
        profile = await self.get_profile()
        profile["watchTopic"] = topic
        self._write(self.root / "profile.json", profile)
        return str(profile["historyId"])

    async def list_history(self, start_history_id: str) -> tuple[list[str], str]:
        history = self._read(self.root / "history.json", [])
        ids = [
            item["messageAdded"]["message"]["id"]
            for item in history
            if int(item.get("id", 0)) > int(start_history_id)
            and isinstance(item.get("messageAdded"), dict)
        ]
        profile = await self.get_profile()
        return list(dict.fromkeys(ids)), str(profile["historyId"])

    async def list_recent_inbox(self, newer_than_days: int) -> list[str]:
        return [
            path.stem
            for path in self.messages_dir.glob("*.json")
            if "INBOX" in self._read(path, {}).get("labelIds", [])
        ]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return self._read(self.messages_dir / f"{message_id}.json", {})

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        path = self.attachments_dir / f"{attachment_id}.bin"
        if path.exists():
            return path.read_bytes()
        message = await self.get_message(message_id)
        encoded = _find_attachment_data(message.get("payload", {}), attachment_id)
        return _b64decode(encoded)

    async def create_draft(self, thread_id: str, raw: bytes) -> str:
        draft_id = f"fake-draft-{uuid4()}"
        self._write(
            self.drafts_dir / f"{draft_id}.json",
            {"id": draft_id, "threadId": thread_id, "raw": raw.decode("utf-8")},
        )
        return draft_id

    async def delete_draft(self, draft_id: str) -> None:
        (self.drafts_dir / f"{draft_id}.json").unlink(missing_ok=True)

    async def ensure_label(self, name: str) -> str:
        labels = self._read(self.root / "labels.json", {})
        if name not in labels:
            labels[name] = f"Label_{len(labels) + 1}"
            self._write(self.root / "labels.json", labels)
        return labels[name]

    async def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        path = self.messages_dir / f"{message_id}.json"
        message = self._read(path, {})
        labels = list(message.get("labelIds", []))
        message["labelIds"] = list(dict.fromkeys(labels + label_ids))
        self._write(path, message)

    def append_message(self, message: dict[str, Any]) -> None:
        message_id = message["id"]
        self._write(self.messages_dir / f"{message_id}.json", message)
        profile = self._read(self.root / "profile.json", {})
        history_id = int(profile.get("historyId", 1)) + 1
        profile["historyId"] = str(history_id)
        self._write(self.root / "profile.json", profile)
        message["historyId"] = str(history_id)
        self._write(self.messages_dir / f"{message_id}.json", message)
        history = self._read(self.root / "history.json", [])
        history.append(
            {
                "id": str(history_id),
                "messageAdded": {"message": {"id": message_id}},
            }
        )
        self._write(self.root / "history.json", history)


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _raise_response(response: httpx.Response, *, allow_404: bool = False) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = response.status_code
        if allow_404 and status == 404:
            raise
        if status != 429 and status < 500:
            raise PermanentJobError(f"Gmail API rejected the request ({status})") from error
        raise


def _find_attachment_data(part: dict[str, Any], attachment_id: str) -> str:
    body = part.get("body", {})
    if body.get("attachmentId") == attachment_id:
        return body.get("data", "")
    return next(
        (
            result
            for child in part.get("parts", [])
            if (result := _find_attachment_data(child, attachment_id))
        ),
        "",
    )


def gmail_client_for(connection, settings):
    if settings.gmail_mode == "google":
        return GoogleGmailClient(connection, settings)
    return FakeGmailClient(connection.external_id, settings.gmail_fake_dir)
