import argparse
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from inbound_api.settings import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate a text-channel webhook")
    parser.add_argument("channel", choices=["whatsapp"])
    parser.add_argument("--phone-number-id", required=True)
    parser.add_argument("--from", dest="from_number", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120)
    return parser


async def _simulate(args: argparse.Namespace) -> None:
    settings = get_settings()
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
                                {"profile": {"name": args.name}, "wa_id": args.from_number}
                            ],
                            "messages": [
                                {
                                    "from": args.from_number,
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
                    if item.get("to") == args.from_number:
                        print(json.dumps(item, ensure_ascii=False))
                        return
        await asyncio.sleep(0.5)
    raise TimeoutError("No fake WhatsApp reply arrived before the timeout")


def main() -> None:
    asyncio.run(_simulate(_parser().parse_args()))


if __name__ == "__main__":
    main()
