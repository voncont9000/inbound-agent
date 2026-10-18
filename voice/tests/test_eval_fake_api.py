from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from inbound_core.booking import AvailabilityResponse, generate_slots
from test_api_client import BOOKING_ASSISTANT_PATH, _route_response

from eval.fake_api import create_fake_api
from inbound_voice.config import load_assistant_config

SERVICE_TOKEN = "booking-test-token"
TO_NUMBER = "+441234567890"


def _fake_app():
    route = _route_response().model_copy(
        update={"config": load_assistant_config(BOOKING_ASSISTANT_PATH)}
    )
    return create_fake_api(route, SERVICE_TOKEN, calendar_delay_ms=0)


async def _availability(client: httpx.AsyncClient) -> AvailabilityResponse:
    response = await client.post(
        "/internal/voice/availability",
        json={"channel": "phone", "to_number": TO_NUMBER},
        headers={"X-Service-Token": SERVICE_TOKEN},
    )
    assert response.status_code == 200, response.text
    return AvailabilityResponse.model_validate(response.json())


async def _book(client: httpx.AsyncClient, conversation_id, slot_id: str) -> httpx.Response:
    return await client.post(
        "/internal/voice/book",
        json={
            "conversation_id": str(conversation_id),
            "external_call_id": str(conversation_id),
            "slot_id": slot_id,
            "name": "Alex Example",
        },
        headers={"X-Service-Token": SERVICE_TOKEN},
    )


def _available_slots(availability: AvailabilityResponse):
    assert availability.fetched_at is not None
    return generate_slots(
        load_assistant_config(BOOKING_ASSISTANT_PATH),
        "initial_consultation",
        availability.busy,
        availability.fetched_at,
    )


@pytest.mark.asyncio
async def test_availability_slot_can_be_booked() -> None:
    app = _fake_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        availability = await _availability(client)
        slot = _available_slots(availability)[0]

        response = await _book(client, uuid4(), slot.slot_id)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "booked"


@pytest.mark.asyncio
async def test_taken_slot_returns_alternatives_without_the_taken_slot() -> None:
    app = _fake_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        availability = await _availability(client)
        slot = _available_slots(availability)[0]

        first = await _book(client, uuid4(), slot.slot_id)
        second = await _book(client, uuid4(), slot.slot_id)

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "booked"
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["status"] == "unavailable"
    assert result["alternatives"]
    assert all(item["slot_id"] != slot.slot_id for item in result["alternatives"])


@pytest.mark.asyncio
async def test_prior_booking_is_busy_for_later_availability_and_booking() -> None:
    app = _fake_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        first_availability = await _availability(client)
        taken_slot = _available_slots(first_availability)[0]
        first = await _book(client, uuid4(), taken_slot.slot_id)
        second_availability = await _availability(client)
        second_slots = _available_slots(second_availability)

        assert first.status_code == 200
        assert first.json()["status"] == "booked"
        assert taken_slot.slot_id not in {slot.slot_id for slot in second_slots}

        second = await _book(client, uuid4(), second_slots[0].slot_id)

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "booked"
