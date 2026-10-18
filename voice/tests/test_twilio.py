from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from inbound_core.config import TransferTarget

import phone_server
from bot import resolve_caller_id
from inbound_voice.config import load_assistant_config
from inbound_voice.transfer import FakeTwilioCallControl
from inbound_voice.twilio import (
    STREAM_TOKEN_MAX_AGE_SECONDS,
    build_stream_twiml,
    compute_signature,
    parse_start_message,
    stream_token,
    verify_stream_token,
)
from phone_server import create_app

AUTH_TOKEN = "test-auth-token"
VOICE_URL = "https://example.com/myapp?foo=1"
VOICE_PARAMS = {"CallSid": "CA1234", "Caller": "+12345"}
SDK_SIGNATURE = "mXInA0Z/Xd472znZ/g/WrnB6R14="


@pytest.mark.parametrize(
    ("issued_at_offset", "expected"),
    [
        (30, True),
        (31, False),
        (-STREAM_TOKEN_MAX_AGE_SECONDS, True),
        (-(STREAM_TOKEN_MAX_AGE_SECONDS + 1), False),
    ],
)
def test_verify_stream_token_enforces_exact_age_boundaries(
    issued_at_offset: int,
    expected: bool,
) -> None:
    now = 1_700_000_000
    issued_at = now + issued_at_offset
    token = stream_token(AUTH_TOKEN, "CA123", "+449876543210", "+441234567890", issued_at)

    assert (
        verify_stream_token(
            AUTH_TOKEN,
            "CA123",
            "+449876543210",
            "+441234567890",
            str(issued_at),
            token,
            now=now,
        )
        is expected
    )


def _send_start(websocket, call_sid: str, custom_parameters: dict[str, str]) -> None:
    websocket.send_json({"event": "connected"})
    websocket.send_json(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "accountSid": "AC123",
                "callSid": call_sid,
                "streamSid": "MZ123",
                "customParameters": custom_parameters,
            },
        }
    )


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
    parameters = {
        parameter.get("name", ""): parameter.get("value", "")
        for parameter in ET.fromstring(response.text).findall(".//Parameter")
    }
    issued_at = int(parameters["StreamIssuedAt"])
    expected_stream_token = stream_token(
        AUTH_TOKEN,
        "CA1234",
        "+449876543210",
        "+441234567890",
        issued_at,
    )
    assert parameters["StreamToken"] == expected_stream_token
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


def test_production_twilio_requires_auth_token() -> None:
    with pytest.raises(
        ValueError,
        match="TWILIO_AUTH_TOKEN is required in production when TWILIO_MODE=twilio",
    ):
        create_app({"APP_ENV": "production", "TWILIO_MODE": "twilio"})


def test_twilio_mode_must_be_valid() -> None:
    with pytest.raises(ValueError, match="TWILIO_MODE must be 'twilio' or 'off'"):
        create_app({"TWILIO_MODE": "fake"})


def test_twilio_off_disables_voice_webhook_and_closes_stream() -> None:
    with TestClient(create_app({"APP_ENV": "production", "TWILIO_MODE": "off"})) as client:
        response = client.post("/twilio/voice", data={"To": "+449876543210"})
        with client.websocket_connect("/twilio/ws") as websocket:
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()

    assert response.status_code == 503
    assert response.json() == {"detail": "Twilio is disabled"}
    assert closed.value.code == 1013


def test_phone_server_host_defaults_to_loopback() -> None:
    assert phone_server.build_parser().parse_args([]).host == "127.0.0.1"


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


@pytest.mark.parametrize("status", ["completed", "answered"])
def test_transfer_status_hangs_up_after_completed_dial(status: str) -> None:
    env = {
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "PUBLIC_BASE_URL": "https://voice.example.com",
    }
    form = {"CallSid": "CA123", "DialCallStatus": status}
    signature = compute_signature(
        AUTH_TOKEN,
        "https://voice.example.com/twilio/transfer-status",
        form,
    )

    with TestClient(create_app(env)) as client:
        response = client.post(
            "/twilio/transfer-status",
            data=form,
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert ET.tostring(ET.fromstring(response.text), encoding="unicode") == (
        "<Response><Hangup /></Response>"
    )


@pytest.mark.parametrize("status", ["no-answer", "busy", "failed"])
def test_transfer_status_speaks_default_message_when_dial_fails(status: str) -> None:
    env = {
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "PUBLIC_BASE_URL": "https://voice.example.com",
    }
    form = {"CallSid": "CA123", "DialCallStatus": status}
    signature = compute_signature(
        AUTH_TOKEN,
        "https://voice.example.com/twilio/transfer-status",
        form,
    )

    with TestClient(create_app(env)) as client:
        response = client.post(
            "/twilio/transfer-status",
            data=form,
            headers={"X-Twilio-Signature": signature},
        )

    root = ET.fromstring(response.text)
    say = root.find("Say")
    assert say is not None
    assert say.text == (
        "Sorry, nobody is available to take your call right now. Someone will call you back "
        "as soon as possible. Goodbye."
    )
    assert root.find("Hangup") is not None


def test_transfer_answered_callback_resolves_parent_call_and_returns_empty_response() -> None:
    env = {
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "PUBLIC_BASE_URL": "https://voice.example.com",
    }
    form = {"ParentCallSid": "CA123", "CallSid": "CA456"}
    signature = compute_signature(
        AUTH_TOKEN,
        "https://voice.example.com/twilio/transfer-status?event=answered",
        form,
    )

    with TestClient(create_app(env)) as client:
        client.app.state.transfer_registry.register(
            "CA123",
            TransferTarget(
                key="duty_solicitor",
                label="Duty solicitor",
                phone="+447700900123",
                when_to_use="The caller explicitly asks to speak to a person now.",
                office_hours_only=False,
            ),
            "Nobody can answer.",
        )
        response = client.post(
            "/twilio/transfer-status?event=answered",
            data=form,
            headers={"X-Twilio-Signature": signature},
        )
        entry = client.app.state.transfer_registry.entry("CA123")

    assert response.status_code == 200
    assert response.text == "<Response/>"
    assert entry is not None
    assert entry.status == "answered"


def test_transfer_status_rejects_bad_signature_and_includes_query_in_public_url() -> None:
    env = {
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "PUBLIC_BASE_URL": "https://voice.example.com",
    }
    form = {"CallSid": "CA123", "DialCallStatus": "no-answer"}
    query = "event=answered"
    good_signature = compute_signature(
        AUTH_TOKEN,
        f"https://voice.example.com/twilio/transfer-status?{query}",
        form,
    )

    with TestClient(create_app(env)) as client:
        good = client.post(
            f"/twilio/transfer-status?{query}",
            data=form,
            headers={"X-Twilio-Signature": good_signature},
        )
        missing_query = client.post(
            f"/twilio/transfer-status?{query}",
            data=form,
            headers={
                "X-Twilio-Signature": compute_signature(
                    AUTH_TOKEN,
                    "https://voice.example.com/twilio/transfer-status",
                    form,
                )
            },
        )
        invalid = client.post(
            "/twilio/transfer-status",
            data=form,
            headers={"X-Twilio-Signature": "invalid"},
        )

    assert good.status_code == 200
    assert missing_query.status_code == 403
    assert invalid.status_code == 403
    assert invalid.json()["detail"] == "Invalid Twilio signature"


def test_transfer_status_uses_registered_failed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    target = TransferTarget(
        key="duty_solicitor",
        label="Duty solicitor",
        phone="+447700900123",
        when_to_use="The caller explicitly asks to speak to a person now.",
        office_hours_only=False,
    )
    assistant = load_assistant_config(
        Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml"
    ).model_copy(update={"transfer_failed_message": "Nobody & <no one> can answer."})

    async def prepare_call(*args, **kwargs):
        return type("Prepared", (), {"assistant": assistant})()

    async def run_bot(*args, **kwargs):
        await kwargs["call_transfer"].start(target, "Caller requests a person.")

    monkeypatch.setattr(phone_server, "prepare_call", prepare_call)
    monkeypatch.setattr(phone_server, "run_bot", run_bot)
    control = FakeTwilioCallControl()

    with TestClient(create_app({}, call_control=control)) as client:
        with client.websocket_connect("/twilio/ws") as websocket:
            _send_start(
                websocket,
                "CA123",
                {"To": "+449876543210", "From": "+441234567890"},
            )
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()
        response = client.post(
            "/twilio/transfer-status",
            data={"CallSid": "CA123", "DialCallStatus": "no-answer"},
        )

    say = ET.fromstring(response.text).find("Say")
    assert say is not None
    assert say.text == "Nobody & <no one> can answer."
    assert control.updates[0][0] == "CA123"


def test_websocket_callback_base_url_uses_public_and_forwarded_headers() -> None:
    class Websocket:
        headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "voice.example.com",
            "host": "internal:7861",
        }
        url = type("URL", (), {"scheme": "ws", "netloc": "internal:7861"})()

    assert phone_server._websocket_callback_base_url(Websocket(), {}) == "https://voice.example.com"
    assert (
        phone_server._websocket_callback_base_url(
            Websocket(),
            {"PUBLIC_BASE_URL": "https://configured.example.com/base/"},
        )
        == "https://configured.example.com/base"
    )


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


def test_websocket_accepts_signed_stream_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "stream-auth-token"
    form = {
        "CallSid": "CA123",
        "AccountSid": "AC123",
        "From": "+441234567890",
        "To": "+449876543210",
    }
    prepared_call = SimpleNamespace(
        assistant=SimpleNamespace(transfer_failed_message="Nobody is available.")
    )
    prepared_calls = []
    run_calls = []

    async def prepare_call(*args, **kwargs):
        prepared_calls.append(kwargs)
        return prepared_call

    async def run_bot(*args, **kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(phone_server, "prepare_call", prepare_call)
    monkeypatch.setattr(phone_server, "run_bot", run_bot)
    voice_url = "https://voice.example.com/twilio/voice"

    with TestClient(
        create_app(
            {
                "TWILIO_AUTH_TOKEN": token,
                "PUBLIC_BASE_URL": "https://voice.example.com",
            }
        )
    ) as client:
        response = client.post(
            "/twilio/voice",
            data=form,
            headers={"X-Twilio-Signature": compute_signature(token, voice_url, form)},
        )
        params = {
            parameter.get("name", ""): parameter.get("value", "")
            for parameter in ET.fromstring(response.text).findall(".//Parameter")
        }
        assert params["StreamToken"] == stream_token(
            token,
            form["CallSid"],
            form["To"],
            form["From"],
            int(params["StreamIssuedAt"]),
        )

        with client.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, form["CallSid"], params)
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()

    assert len(prepared_calls) == 1
    assert prepared_calls[0]["external_call_id"] == form["CallSid"]
    assert len(run_calls) == 1
    assert run_calls[0]["prepared"] is prepared_call


@pytest.mark.parametrize(
    "invalid_token",
    ["missing", "forged", "tampered_from", "expired", "future", "invalid_issued_at"],
)
def test_websocket_rejects_invalid_stream_token_before_preparing_call(
    monkeypatch: pytest.MonkeyPatch,
    invalid_token: str,
) -> None:
    token = "stream-auth-token"
    call_sid = "CA123"
    to_number = "+449876543210"
    from_number = "+441234567890"
    issued_at = int(time.time())
    custom_parameters = {
        "To": to_number,
        "From": from_number,
        "StreamIssuedAt": str(issued_at),
        "StreamToken": stream_token(token, call_sid, to_number, from_number, issued_at),
    }
    if invalid_token == "missing":
        del custom_parameters["StreamToken"]
    elif invalid_token == "forged":
        custom_parameters["StreamToken"] = "forged-token"
    elif invalid_token == "tampered_from":
        custom_parameters["From"] = "+441111111111"
    elif invalid_token in {"expired", "future"}:
        issued_at += -(STREAM_TOKEN_MAX_AGE_SECONDS + 60) if invalid_token == "expired" else 30 + 60
        custom_parameters["StreamIssuedAt"] = str(issued_at)
        custom_parameters["StreamToken"] = stream_token(
            token,
            call_sid,
            to_number,
            from_number,
            issued_at,
        )
    else:
        custom_parameters["StreamIssuedAt"] = "not-an-integer"

    prepared_calls = []
    run_calls = []

    async def prepare_call(*args, **kwargs):
        prepared_calls.append(kwargs)
        return SimpleNamespace(
            assistant=SimpleNamespace(transfer_failed_message="Nobody is available.")
        )

    async def run_bot(*args, **kwargs):
        run_calls.append(kwargs)

    original_verify_stream_token = phone_server.verify_stream_token

    def verify_stream_token_with_clock_shift(*args, **kwargs):
        kwargs["now"] = time.time() + 0.999
        return original_verify_stream_token(*args, **kwargs)

    monkeypatch.setattr(phone_server, "verify_stream_token", verify_stream_token_with_clock_shift)
    monkeypatch.setattr(phone_server, "prepare_call", prepare_call)
    monkeypatch.setattr(phone_server, "run_bot", run_bot)
    with TestClient(create_app({"TWILIO_AUTH_TOKEN": token})) as client:
        with client.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, call_sid, custom_parameters)
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 1008
    assert prepared_calls == []
    assert run_calls == []


def test_websocket_rejects_reused_call_sid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "stream-auth-token"
    call_sid = "CA123"
    to_number = "+449876543210"
    from_number = "+441234567890"
    issued_at = int(time.time())
    custom_parameters = {
        "To": to_number,
        "From": from_number,
        "StreamIssuedAt": str(issued_at),
        "StreamToken": stream_token(
            token,
            call_sid,
            to_number,
            from_number,
            issued_at,
        ),
    }
    prepared_calls = []
    run_calls = []

    async def prepare_call(*args, **kwargs):
        prepared_calls.append(kwargs)
        return SimpleNamespace(
            assistant=SimpleNamespace(transfer_failed_message="Nobody is available.")
        )

    async def run_bot(*args, **kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(phone_server, "prepare_call", prepare_call)
    monkeypatch.setattr(phone_server, "run_bot", run_bot)
    with TestClient(create_app({"TWILIO_AUTH_TOKEN": token})) as client:
        with client.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, call_sid, custom_parameters)
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()

        with client.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, call_sid, custom_parameters)
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 1008
    assert len(prepared_calls) == 1
    assert len(run_calls) == 1
