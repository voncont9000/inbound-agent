from __future__ import annotations

from uuid import UUID

import pytest
from inbound_core.call_report import RouteResponse

from inbound_voice.api_client import VoiceApiClient
from inbound_voice.call_setup import prepare_call
from inbound_voice.config import load_assistant_config

ASSISTANT_PATH = "config/assistants/demo_firm.yaml"


@pytest.mark.asyncio
async def test_route_failure_falls_back_but_keeps_api_and_call_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_route(self, to_number: str, from_number: str | None):
        raise RuntimeError("route unavailable")

    monkeypatch.setattr(VoiceApiClient, "route", failed_route)
    env = {
        "API_BASE_URL": "https://api.example.com",
        "VOICE_SERVICE_TOKEN": "test-token",
        "ASSISTANT_CONFIG": ASSISTANT_PATH,
    }

    prepared = await prepare_call(
        env,
        channel="phone",
        to_number="+441234567890",
        from_number="+447700900001",
        external_call_id="CA123",
    )

    assert prepared.assistant.business_name == load_assistant_config(ASSISTANT_PATH).business_name
    assert prepared.context_card is None
    assert prepared.route is None
    assert prepared.api is not None
    assert prepared.call_start is not None
    assert prepared.call_start.external_call_id == "CA123"


@pytest.mark.asyncio
async def test_tokenized_web_call_uses_backend_route_and_call_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routed_tokens: list[str] = []

    async def route_web_call(self, token: str) -> RouteResponse:
        routed_tokens.append(token)
        return RouteResponse(
            tenant_id=UUID("10000000-0000-0000-0000-000000000001"),
            assistant_id=UUID("20000000-0000-0000-0000-000000000002"),
            config=load_assistant_config(ASSISTANT_PATH),
            digest="Practice-only knowledge.",
        )

    monkeypatch.setattr(VoiceApiClient, "route_web_call", route_web_call)
    token = "signed-test-call-token"
    prepared = await prepare_call(
        {
            "API_BASE_URL": "https://api.example.com",
            "VOICE_SERVICE_TOKEN": "test-token",
            "ASSISTANT_CONFIG": ASSISTANT_PATH,
        },
        channel="web",
        to_number=None,
        from_number=None,
        external_call_id="random-call-id",
        web_call_token=token,
    )

    assert routed_tokens == [token]
    assert prepared.route is not None
    assert prepared.route.digest == "Practice-only knowledge."
    assert prepared.assistant.knowledge.digest_content == "Practice-only knowledge."
    assert prepared.call_start is not None
    assert prepared.call_start.web_call_token == token
    assert prepared.call_start.external_call_id == "random-call-id"


@pytest.mark.asyncio
async def test_tokenized_web_call_fails_closed_without_api_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_route(self, token: str) -> RouteResponse:
        raise AssertionError("route lookup should not be attempted")

    monkeypatch.setattr(VoiceApiClient, "route_web_call", unexpected_route)
    with pytest.raises(RuntimeError, match="API_BASE_URL"):
        await prepare_call(
            {"ASSISTANT_CONFIG": ASSISTANT_PATH},
            channel="web",
            to_number=None,
            from_number=None,
            external_call_id="call-id",
            web_call_token="signed-token",
        )


@pytest.mark.asyncio
async def test_tokenized_web_call_fails_closed_when_route_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_route(self, token: str) -> RouteResponse:
        raise RuntimeError("route unavailable")

    monkeypatch.setattr(VoiceApiClient, "route_web_call", failed_route)
    with pytest.raises(RuntimeError, match="Could not route tokenized web call"):
        await prepare_call(
            {
                "API_BASE_URL": "https://api.example.com",
                "VOICE_SERVICE_TOKEN": "test-token",
                "ASSISTANT_CONFIG": ASSISTANT_PATH,
            },
            channel="web",
            to_number=None,
            from_number=None,
            external_call_id="call-id",
            web_call_token="signed-token",
        )
