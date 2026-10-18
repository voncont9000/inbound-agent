from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport

import web_call_server
from inbound_voice.web_call import PcmWebSocketSerializer


@pytest.mark.asyncio
async def test_pcm_serializer_deserializes_binary_pcm_and_ignores_text() -> None:
    serializer = PcmWebSocketSerializer()
    frame = await serializer.deserialize(b"\x01\x00\xff\xff")

    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == b"\x01\x00\xff\xff"
    assert frame.sample_rate == 16000
    assert frame.num_channels == 1
    assert await serializer.deserialize('{"type":"ignored"}') is None


@pytest.mark.asyncio
async def test_pcm_serializer_serializes_output_and_interruptions_only() -> None:
    serializer = PcmWebSocketSerializer()
    audio = b"\x02\x00\xfc\xff"

    assert (
        await serializer.serialize(
            OutputAudioRawFrame(audio=audio, sample_rate=24000, num_channels=1)
        )
        == audio
    )
    assert await serializer.serialize(InterruptionFrame()) == '{"type":"clear"}'
    assert (
        await serializer.serialize(
            InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
        )
        is None
    )


def test_page_and_health_endpoint() -> None:
    with TestClient(web_call_server.create_app({})) as client:
        page = client.get("/")
        health = client.get("/healthz")

    assert page.status_code == 200
    assert "Use headphones so the agent doesn't hear itself." in page.text
    assert "AudioWorkletNode" in page.text
    assert health.status_code == 200
    assert health.json() == {"ok": True}


def test_websocket_requires_configured_token() -> None:
    with TestClient(web_call_server.create_app({"WEB_CALL_TOKEN": "secret"})) as client:
        with client.websocket_connect("/ws") as websocket:
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()

    assert closed.value.code == 1008


def test_websocket_enforces_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    async def hold_call(*args, **kwargs) -> None:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)

    async def prepare_call(*args, **kwargs):
        return None

    monkeypatch.setattr(web_call_server, "prepare_call", prepare_call)
    monkeypatch.setattr(web_call_server, "run_bot", hold_call)
    with TestClient(web_call_server.create_app({"WEB_CALL_MAX_CONCURRENT": "1"})) as client:
        with client.websocket_connect("/ws") as first:
            assert started.wait(timeout=2)
            with client.websocket_connect("/ws") as second:
                with pytest.raises(WebSocketDisconnect) as closed:
                    second.receive_text()
            assert closed.value.code == 1013
            release.set()
            with pytest.raises(WebSocketDisconnect):
                first.receive_text()


def test_websocket_runs_shared_web_call_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = object()
    prepared_calls = []
    run_calls = []

    async def prepare_call(*args, **kwargs):
        prepared_calls.append((args, kwargs))
        return prepared

    async def run_bot(transport, **kwargs):
        run_calls.append((transport, kwargs))

    monkeypatch.setattr(web_call_server, "prepare_call", prepare_call)
    monkeypatch.setattr(web_call_server, "run_bot", run_bot)
    env = {
        "CALLER_ID_OVERRIDE": "+441234567890",
        "WEB_CALL_TO_NUMBER": "+449876543210",
        "WEB_CALL_TOKEN": "secret",
    }
    with TestClient(web_call_server.create_app(env)) as client:
        with client.websocket_connect("/ws?token=secret") as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

    args, kwargs = prepared_calls[0]
    assert kwargs == {
        "channel": "web",
        "to_number": "+449876543210",
        "from_number": "+441234567890",
        "external_call_id": run_calls[0][1]["call_id"],
    }
    transport, run_kwargs = run_calls[0]
    assert isinstance(transport, FastAPIWebsocketTransport)
    assert run_kwargs["telephony"] is False
    assert run_kwargs["caller_id"] == "+441234567890"
    assert run_kwargs["prepared"] is prepared
    assert args[0] is env
