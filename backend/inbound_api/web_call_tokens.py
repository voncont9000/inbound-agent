from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from uuid import UUID

from inbound_api.settings import get_settings


def issue(tenant_id: UUID | str, assistant_id: UUID | str, ttl: int = 600) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl
    payload = json.dumps(
        {
            "t": str(tenant_id),
            "a": str(assistant_id),
            "exp": expires_at,
            "n": secrets.token_urlsafe(18),
        },
        separators=(",", ":"),
    ).encode()
    payload_b64 = _encode(payload)
    signature = hmac.new(
        get_settings().voice_service_token.encode(),
        b"web-call:" + payload_b64.encode(),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_encode(signature)}", expires_at


def verify(token: str) -> tuple[UUID, UUID]:
    try:
        payload_b64, signature_b64 = token.split(".")
        payload_bytes = _decode(payload_b64)
        signature = _decode(signature_b64)
        expected_signature = hmac.new(
            get_settings().voice_service_token.encode(),
            b"web-call:" + payload_b64.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Invalid web call token signature")
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise ValueError("Invalid web call token payload")
        tenant_id = payload.get("t")
        assistant_id = payload.get("a")
        if not isinstance(tenant_id, str) or not isinstance(assistant_id, str):
            raise ValueError("Invalid web call token tenant or assistant")
        expires_at = payload.get("exp")
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= int(time.time())
        ):
            raise ValueError("Expired web call token")
        if not isinstance(payload.get("n"), str) or not payload["n"]:
            raise ValueError("Invalid web call token nonce")
        return UUID(tenant_id), UUID(assistant_id)
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Invalid web call token") from error


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    if not value:
        raise ValueError("Invalid web call token encoding")
    return base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
