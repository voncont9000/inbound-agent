from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from uuid import UUID

from inbound_api.integrations.google_auth import google_scopes_for_features
from inbound_api.settings import get_settings


def issue_state(
    tenant_id: UUID,
    assistant_id: UUID,
    user_id: UUID,
    ttl: int = 600,
    *,
    features: list[str] | None = None,
) -> str:
    requested_features = list(dict.fromkeys(features or []))
    google_scopes_for_features(requested_features)
    payload = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "assistant_id": str(assistant_id),
            "user_id": str(user_id),
            "features": requested_features,
            "nonce": secrets.token_urlsafe(18),
            "exp": int(time.time()) + ttl,
        },
        separators=(",", ":"),
    ).encode()
    encoded = _encode(payload)
    signature = hmac.new(
        get_settings().voice_service_token.encode(),
        b"gmail-oauth:" + encoded.encode(),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_encode(signature)}"


def verify_state(token: str) -> tuple[UUID, UUID, UUID]:
    tenant_id, assistant_id, user_id, _features = verify_state_with_features(token)
    return tenant_id, assistant_id, user_id


def verify_state_with_features(token: str) -> tuple[UUID, UUID, UUID, list[str]]:
    try:
        encoded, signed = token.split(".")
        signature = _decode(signed)
        expected = hmac.new(
            get_settings().voice_service_token.encode(),
            b"gmail-oauth:" + encoded.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid OAuth state signature")
        payload = json.loads(_decode(encoded))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("nonce"), str)
            or not payload["nonce"]
            or isinstance(payload.get("exp"), bool)
            or not isinstance(payload.get("exp"), int)
            or payload["exp"] <= int(time.time())
        ):
            raise ValueError("Expired or malformed OAuth state")
        features = payload.get("features", [])
        if not isinstance(features, list) or any(not isinstance(item, str) for item in features):
            raise ValueError("Malformed OAuth state features")
        google_scopes_for_features(features)
        return (
            UUID(payload["tenant_id"]),
            UUID(payload["assistant_id"]),
            UUID(payload["user_id"]),
            features,
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("Invalid Gmail OAuth state") from error


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
