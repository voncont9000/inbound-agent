from __future__ import annotations

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TwilioStart:
    stream_sid: str
    call_sid: str
    account_sid: str
    to_number: str | None
    from_number: str | None
    custom_parameters: dict[str, str]


def compute_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    payload = url + "".join(key + params[key] for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def validate_signature(
    auth_token: str,
    url: str,
    params: Mapping[str, str],
    signature: str,
) -> bool:
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)


def stream_token(auth_token: str, call_sid: str, to_number: str, from_number: str) -> str:
    message = f"{call_sid}\n{to_number}\n{from_number}"
    return hmac.new(auth_token.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_stream_token(
    auth_token: str,
    call_sid: str,
    to_number: str,
    from_number: str,
    token: str,
) -> bool:
    return hmac.compare_digest(
        stream_token(auth_token, call_sid, to_number, from_number),
        token,
    )


def build_stream_twiml(ws_url: str, parameters: dict[str, str]) -> str:
    response = ET.Element("Response")
    connect = ET.SubElement(response, "Connect")
    stream = ET.SubElement(connect, "Stream", {"url": ws_url})
    for name, value in parameters.items():
        ET.SubElement(stream, "Parameter", {"name": name, "value": value})
    return ET.tostring(response, encoding="unicode")


def parse_start_message(msg: dict) -> TwilioStart:
    start = msg.get("start", {})
    custom = start.get("customParameters", {})
    if not isinstance(custom, dict):
        custom = {}
    to_number = custom.get("To") or custom.get("to_number") or start.get("To")
    from_number = custom.get("From") or custom.get("from_number") or start.get("From")
    return TwilioStart(
        stream_sid=str(msg.get("streamSid") or start.get("streamSid") or ""),
        call_sid=str(start.get("callSid") or msg.get("callSid") or ""),
        account_sid=str(start.get("accountSid") or msg.get("accountSid") or ""),
        to_number=str(to_number) if to_number else None,
        from_number=str(from_number) if from_number else None,
        custom_parameters={str(key): str(value) for key, value in custom.items()},
    )
