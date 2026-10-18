from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx


class WhatsAppSender(Protocol):
    async def send_text(self, phone_number_id: str, to: str, body: str) -> str: ...


class GraphWhatsAppSender:
    def __init__(self, *, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    async def send_text(self, phone_number_id: str, to: str, body: str) -> str:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": body},
                },
            )
            response.raise_for_status()
        return response.json()["messages"][0]["id"]


class FakeWhatsAppSender:
    def __init__(self, outbox_path: str | Path):
        self.outbox_path = Path(outbox_path)

    async def send_text(self, phone_number_id: str, to: str, body: str) -> str:
        message_id = f"fakewamid.{uuid4()}"
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox_path.open("a", encoding="utf-8") as outbox:
            outbox.write(
                json.dumps(
                    {
                        "id": message_id,
                        "phone_number_id": phone_number_id,
                        "to": to,
                        "body": body,
                        "sent_at": datetime.now(UTC).isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return message_id


def sender_from_settings(settings):
    if settings.whatsapp_mode == "graph":
        return GraphWhatsAppSender(
            base_url=settings.whatsapp_graph_base_url,
            access_token=settings.whatsapp_access_token or "",
        )
    return FakeWhatsAppSender(settings.fake_outbox_path)
