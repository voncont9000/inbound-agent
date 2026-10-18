from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from redis import asyncio as redis_asyncio

import inbound_voice.replay as replay
import phone_server
from inbound_voice.replay import (
    InMemoryReplayGuard,
    RedisReplayGuard,
    replay_guard_from_env,
)
from phone_server import create_app


class RedisStub:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.calls: list[tuple[str, str, bool, int]] = []

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool | None:
        self.calls.append((key, value, nx, ex))
        if nx and key in self.claimed:
            return None
        self.claimed.add(key)
        return True


@pytest.mark.asyncio
async def test_in_memory_replay_guard_claim_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(replay.time, "time", lambda: now)
    guard = InMemoryReplayGuard()

    assert await guard.claim("twilio:CA123", 10)
    assert not await guard.claim("twilio:CA123", 10)

    now += 10.1
    assert await guard.claim("twilio:CA123", 10)


@pytest.mark.asyncio
async def test_redis_replay_guard_uses_nx_prefix_and_ttl() -> None:
    client = RedisStub()
    guard = RedisReplayGuard(client, prefix="test:replay:")

    assert await guard.claim("twilio:CA123", 150)
    assert not await guard.claim("twilio:CA123", 150)
    assert client.calls == [
        ("test:replay:twilio:CA123", "1", True, 150),
        ("test:replay:twilio:CA123", "1", True, 150),
    ]


@pytest.mark.asyncio
async def test_redis_replay_guard_falls_back_to_memory_on_errors() -> None:
    class FailingRedis:
        async def set(self, *args, **kwargs):
            raise ConnectionError("Redis unavailable")

    guard = RedisReplayGuard(FailingRedis())

    assert await guard.claim("twilio:CA123", 150)
    assert not await guard.claim("twilio:CA123", 150)


def test_replay_guard_from_env_uses_redis_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    urls = []
    monkeypatch.setattr(replay.redis_asyncio, "from_url", lambda url: urls.append(url) or client)

    assert isinstance(replay_guard_from_env({}), InMemoryReplayGuard)
    guard = replay_guard_from_env({"REDIS_URL": " redis://localhost:6379/0 "})

    assert isinstance(guard, RedisReplayGuard)
    assert guard.client is client
    assert urls == ["redis://localhost:6379/0"]


def test_phone_app_closes_only_redis_clients_it_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosableRedis(RedisStub):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    created_client = ClosableRedis()
    injected_client = ClosableRedis()
    monkeypatch.setattr(replay.redis_asyncio, "from_url", lambda _: created_client)

    with TestClient(create_app({"REDIS_URL": "redis://localhost:6379/0"})):
        pass
    with TestClient(create_app({}, replay_guard=RedisReplayGuard(injected_client))):
        pass

    assert created_client.closed
    assert not injected_client.closed


def test_two_phone_apps_share_replay_guard_and_reject_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = InMemoryReplayGuard()
    prepared_calls = []
    run_calls = []

    async def prepare_call(*args, **kwargs):
        prepared_calls.append(kwargs)
        return object()

    async def run_bot(*args, **kwargs):
        run_calls.append(kwargs)

    monkeypatch.setattr(phone_server, "prepare_call", prepare_call)
    monkeypatch.setattr(phone_server, "run_bot", run_bot)
    app_a = create_app({}, replay_guard=guard)
    app_b = create_app({}, replay_guard=guard)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        with client_a.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, "CA123")
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()

        with client_b.websocket_connect("/twilio/ws") as websocket:
            _send_start(websocket, "CA123")
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 1008
    assert len(prepared_calls) == 1
    assert len(run_calls) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="REDIS_URL is not configured")
async def test_real_redis_replay_guard() -> None:
    client = redis_asyncio.from_url(os.environ["REDIS_URL"])
    prefix = f"inbound:test-replay:{uuid4()}:"
    guard = RedisReplayGuard(client, prefix=prefix)
    key = "twilio:CA123"
    try:
        assert await guard.claim(key, 30)
        assert not await guard.claim(key, 30)
    finally:
        await client.delete(prefix + key)
        await client.aclose()


def _send_start(websocket, call_sid: str) -> None:
    websocket.send_json({"event": "connected"})
    websocket.send_json(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "accountSid": "AC123",
                "callSid": call_sid,
                "streamSid": "MZ123",
                "customParameters": {
                    "To": "+449876543210",
                    "From": "+441234567890",
                },
            },
        }
    )
