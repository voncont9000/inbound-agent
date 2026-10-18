from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import phone_server
from bot import resolve_caller_id
from inbound_voice.twilio import (
    build_stream_twiml,
    compute_signature,
    parse_start_message,
)
from phone_server import create_app

AUTH_TOKEN = "test-auth-token"
VOICE_URL = "https://example.com/myapp?foo=1"
VOICE_PARAMS = {"CallSid": "CA1234", "Caller": "+12345"}
SDK_SIGNATURE = "mXInA0Z/Xd472znZ/g/WrnB6R14="


def test_twilio_signature_matches_sdk_checked_vector() -> None:
    assert compute_signature(AUTH_TOKEN, VOICE_URL, VOICE_PARAMS) == SDK_SIGNATURE


def test_twiml_signature_validation_and_public_websocket_url() -> None:
    env = {
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "PUBLIC_BASE_URL": "https://voice.example.com",
    }
    form = {
        "CallSid": "CA1234",
        "AccountSid": "AC1234",
        "From": "+441234567890",
        "To": "+449876543210",
        "CallStatus": "ringing",
    }
    signature = compute_signature(AUTH_TOKEN, "https://voice.example.com/twilio/voice", form)

    with TestClient(create_app(env)) as client:
        response = client.post(
            "/twilio/voice",
            data=form,
            headers={"X-Twilio-Signature": signature},
        )
        invalid_signature = client.post(
            "/twilio/voice",
            data=form,
            headers={"X-Twilio-Signature": "invalid"},
        )
        missing_signature = client.post("/twilio/voice", data=form)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert 'url="wss://voice.example.com/twilio/ws"' in response.text
    assert 'name="To" value="+449876543210"' in response.text
    assert 'name="From" value="+441234567890"' in response.text
    assert invalid_signature.status_code == 403
    assert missing_signature.status_code == 403


def test_public_base_url_signature_includes_request_query_string() -> None:
    token = "query-string-token"
    public_base_url = "https://voice.example.com"
    query_string = "source=twilio&attempt=2"
    form = {"To": "+449876543210", "From": "+441234567890"}
    signature = compute_signature(
        token,
        f"{public_base_url}/twilio/voice?{query_string}",
        form,
    )

    with TestClient(
        create_app(
            {
                "TWILIO_AUTH_TOKEN": token,
                "PUBLIC_BASE_URL": public_base_url,
            }
        )
    ) as client:
        response = client.post(
            f"/twilio/voice?{query_string}",
            data=form,
            headers={"X-Twilio-Signature": signature},
        )
        without_query_signature = client.post(
            f"/twilio/voice?{query_string}",
            data=form,
            headers={
                "X-Twilio-Signature": compute_signature(
                    token,
                    f"{public_base_url}/twilio/voice",
                    form,
                )
            },
        )

    assert response.status_code == 200
    assert without_query_signature.status_code == 403


def test_webhook_skips_signature_when_token_is_not_configured() -> None:
    with TestClient(create_app({})) as client:
        response = client.post(
            "/twilio/voice",
            data={"To": "+449876543210", "From": "+441234567890"},
        )

    assert response.status_code == 200
    assert 'url="ws://testserver/twilio/ws"' in response.text


def test_webhook_uses_forwarded_scheme_and_host_for_signature() -> None:
    token = "forwarded-token"
    form = {"To": "+449876543210", "From": "+441234567890"}
    forwarded_url = "https://public.example.com/twilio/voice"
    signature = compute_signature(token, forwarded_url, form)
    with TestClient(create_app({"TWILIO_AUTH_TOKEN": token})) as client:
        response = client.post(
            "/twilio/voice",
            data=form,
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "public.example.com",
                "X-Twilio-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert 'url="wss://public.example.com/twilio/ws"' in response.text


def test_parse_start_message_and_resolve_caller_id() -> None:
    start_payload = {
        "accountSid": "AC123",
        "callSid": "CA123",
        "streamSid": "MZ123",
        "customParameters": {
            "To": "+449876543210",
            "From": "+441234567890",
        },
    }
    start = parse_start_message({"event": "start", "streamSid": "MZ123", "start": start_payload})

    assert start.stream_sid == "MZ123"
    assert start.call_sid == "CA123"
    assert start.account_sid == "AC123"
    assert start.to_number == "+449876543210"
    assert start.from_number == "+441234567890"
    assert resolve_caller_id({"start": start_payload}, {}, telephony=True) == "+441234567890"


def test_twiml_escapes_stream_url_and_parameters() -> None:
    twiml = build_stream_twiml(
        "wss://voice.example.com/twilio/ws?a=1&b=2",
        {"To": "A&B", "From": "<caller>"},
    )
    assert "a=1&amp;b=2" in twiml
    assert "A&amp;B" in twiml
    assert "&lt;caller&gt;" in twiml


def test_websocket_closes_when_pipeline_returns_after_end_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = []

    async def pipeline_finished(*args, **kwargs) -> None:
        completed.append(kwargs["telephony"])

    monkeypatch.setattr(phone_server, "run_bot", pipeline_finished)
    with TestClient(create_app({})) as client:
        with client.websocket_connect("/twilio/ws") as websocket:
            websocket.send_json({"event": "connected"})
            websocket.send_json(
                {
                    "event": "start",
                    "streamSid": "MZ123",
                    "start": {
                        "accountSid": "AC123",
                        "callSid": "CA123",
                        "streamSid": "MZ123",
                        "customParameters": {
                            "To": "+449876543210",
                            "From": "+441234567890",
                        },
                    },
                }
            )
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()

    assert completed == [True]
