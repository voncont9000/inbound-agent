import argparse
import asyncio
import base64
import email.utils
import hashlib
import hmac
import json
import mimetypes
import time
from pathlib import Path
from uuid import uuid4

import httpx

from inbound_api.channels.gmail_client import FakeGmailClient
from inbound_api.settings import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate a text-channel webhook")
    parser.add_argument("channel", choices=["whatsapp", "gmail"])
    parser.add_argument("--phone-number-id")
    parser.add_argument("--mailbox")
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--name")
    parser.add_argument("--subject", default="")
    parser.add_argument("--text", required=True)
    parser.add_argument("--thread-id")
    parser.add_argument("--attach", type=Path)
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120)
    return parser


async def _simulate(args: argparse.Namespace) -> None:
    if args.channel == "gmail":
        await _simulate_gmail(args)
        return
    settings = get_settings()
    if not args.phone_number_id or not args.name:
        raise ValueError("WhatsApp simulation requires --phone-number-id and --name")
    if not settings.whatsapp_app_secret:
        raise RuntimeError("WHATSAPP_APP_SECRET is required to sign the simulated webhook")
    if settings.whatsapp_mode != "fake":
        raise RuntimeError("The simulator waits for the fake outbox; set WHATSAPP_MODE=fake")
    outbox = Path(settings.fake_outbox_path)
    offset = outbox.stat().st_size if outbox.exists() else 0
    sent_at = time.monotonic()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "simulator",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": args.phone_number_id,
                            },
                            "contacts": [
                                {"profile": {"name": args.name or ""}, "wa_id": args.sender}
                            ],
                            "messages": [
                                {
                                    "from": args.sender,
                                    "id": f"wamid.simulator.{uuid4()}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": args.text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(
        settings.whatsapp_app_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{args.backend_url.rstrip('/')}/webhooks/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )
        response.raise_for_status()
    deadline = sent_at + args.timeout
    while time.monotonic() < deadline:
        if outbox.exists():
            with outbox.open(encoding="utf-8") as file:
                file.seek(offset)
                for line in file:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("to") == args.sender:
                        print(json.dumps(item, ensure_ascii=False))
                        return
        await asyncio.sleep(0.5)
    raise TimeoutError("No fake WhatsApp reply arrived before the timeout")


async def _simulate_gmail(args: argparse.Namespace) -> None:
    settings = get_settings()
    if settings.gmail_mode != "fake":
        raise RuntimeError("The simulator requires GMAIL_MODE=fake")
    if not settings.gmail_push_token:
        raise RuntimeError("GMAIL_PUSH_TOKEN is required to simulate the Gmail push")
    if not args.mailbox or "@" not in args.mailbox:
        raise ValueError("--mailbox must be a mailbox email address")
    mailbox = args.mailbox.strip().lower()
    client = FakeGmailClient(mailbox, settings.gmail_fake_dir)
    before_drafts = set(client.drafts_dir.glob("*.json"))
    message_id = f"fake-message-{uuid4()}"
    thread_id = args.thread_id or f"fake-thread-{uuid4()}"
    sender_name, sender_email = email.utils.parseaddr(args.sender)
    sender_email = sender_email or args.sender
    headers = [
        {
            "name": "From",
            "value": email.utils.formataddr((sender_name or args.name or "", sender_email)),
        },
        {"name": "To", "value": mailbox},
        {"name": "Subject", "value": args.subject},
        {"name": "Message-ID", "value": f"<{message_id}@simulator.local>"},
        *(
            {"name": key.strip(), "value": value.strip()}
            for item in args.header
            if ":" in item
            for key, value in [item.split(":", 1)]
        ),
    ]
    parts = [
        {
            "mimeType": "text/plain",
            "filename": "",
            "body": {
                "size": len(args.text.encode()),
                "data": base64.urlsafe_b64encode(args.text.encode()).decode().rstrip("="),
            },
        }
    ]
    if args.attach:
        data = args.attach.read_bytes()
        attachment_id = f"fake-attachment-{uuid4()}"
        mime_type = mimetypes.guess_type(args.attach.name)[0] or "application/octet-stream"
        client.attachments_dir.mkdir(parents=True, exist_ok=True)
        (client.attachments_dir / f"{attachment_id}.bin").write_bytes(data)
        parts.append(
            {
                "mimeType": mime_type,
                "filename": args.attach.name,
                "body": {"attachmentId": attachment_id, "size": len(data)},
            }
        )
    message = {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "historyId": "",
        "internalDate": str(int(time.time() * 1000)),
        "payload": {
            "mimeType": "multipart/mixed" if args.attach else "text/plain",
            "headers": headers,
            "body": {},
            "parts": parts,
        },
    }
    client.append_message(message)
    profile = await client.get_profile()
    encoded_data = base64.b64encode(
        json.dumps(
            {"emailAddress": mailbox, "historyId": profile["historyId"]},
            separators=(",", ":"),
        ).encode()
    ).decode()
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.post(
            f"{args.backend_url.rstrip('/')}/webhooks/gmail",
            params={"token": settings.gmail_push_token},
            json={"message": {"data": encoded_data}},
        )
        response.raise_for_status()
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        new_drafts = set(client.drafts_dir.glob("*.json")) - before_drafts
        for draft_path in new_drafts:
            print(draft_path.read_text(encoding="utf-8"))
            return
        stored = await client.get_message(message_id)
        labels = stored.get("labelIds", [])
        label_map = client._read(client.root / "labels.json", {})
        if any(label_map.get(name) in labels for name in ("AI/Skipped", "AI/Needs human")):
            print(json.dumps({"message_id": message_id, "labels": labels}, ensure_ascii=False))
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("No Gmail draft or skip label arrived before the timeout")


def main() -> None:
    asyncio.run(_simulate(_parser().parse_args()))


if __name__ == "__main__":
    main()
