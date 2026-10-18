from __future__ import annotations

import pytest

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
