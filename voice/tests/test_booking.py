import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from inbound_core.booking import (
    AvailabilityResponse,
    BookResponse,
    BusyBlock,
    SlotOffer,
)
from inbound_core.call_report import CallStart
from inbound_core.config import AppointmentType, BookingConfig

from inbound_voice.booking import CallBooking
from inbound_voice.config import load_assistant_config

ASSISTANT_PATH = "config/assistants/demo_firm.yaml"
NOW = datetime(2026, 10, 19, 8, tzinfo=UTC)
CONVERSATION_ID = UUID("10000000-0000-0000-0000-000000000001")


class FakeApi:
    def __init__(self, availability=None, book=None):
        self.availability_responses = list(availability or [AvailabilityResponse(available=True)])
        self.book_response = book or BookResponse(status="error")
        self.availability_calls = 0
        self.book_calls = []

    async def availability(self, _request):
        self.availability_calls += 1
        if self.availability_responses:
            return self.availability_responses.pop(0)
        return AvailabilityResponse(available=False)

    async def book(self, request):
        self.book_calls.append(request)
        return self.book_response


def _booking(api, task=None):
    config = load_assistant_config(ASSISTANT_PATH).model_copy(
        update={
            "booking": BookingConfig(
                appointment_types=[
                    AppointmentType(
                        key="consultation",
                        label="Initial consultation",
                        duration_minutes=30,
                        when_to_use="For new callers",
                    )
                ],
                min_notice_hours=1,
                horizon_days=14,
                max_slots_offered=3,
            ),
            "live_actions": ["check_availability", "book_appointment"],
        }
    )
    call_start = CallStart(
        to_number="+441234567890",
        from_number="+447700900001",
        external_call_id="call-1",
        channel="phone",
        started_at=NOW,
    )
    return CallBooking(
        config,
        api,
        call_start,
        task,
        lambda: CONVERSATION_ID,
        "+447700900001",
        None,
        now_fn=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_prefetched_availability_is_used_without_a_live_request() -> None:
    async def result():
        return AvailabilityResponse(available=True)

    api = FakeApi()
    booking = _booking(api, asyncio.create_task(result()))

    offered = await booking.check_availability("consultation")

    assert offered["ok"] is True
    assert offered["slots"]
    assert api.availability_calls == 0


@pytest.mark.asyncio
async def test_unavailable_prefetch_falls_back_to_one_live_request() -> None:
    async def result():
        return AvailabilityResponse(available=False, reason="calendar_error")

    api = FakeApi([AvailabilityResponse(available=True)])
    booking = _booking(api, asyncio.create_task(result()))

    offered = await booking.check_availability("consultation")

    assert offered["ok"] is True
    assert api.availability_calls == 1


@pytest.mark.asyncio
async def test_slot_from_earlier_availability_check_remains_bookable() -> None:
    api = FakeApi(book=BookResponse(status="booked"))
    booking = _booking(api)

    earlier_offer = await booking.check_availability("consultation", "Tuesday")
    earlier_slot_id = earlier_offer["slots"][0]["slot_id"]
    later_offer = await booking.check_availability("consultation", "Wednesday")

    assert earlier_slot_id not in {slot["slot_id"] for slot in later_offer["slots"]}
    assert earlier_slot_id in booking._offered

    result = await booking.book_appointment(earlier_slot_id, "Alex")

    assert result["ok"] is True
    assert result["status"] == "booked"
    assert api.book_calls[0].slot_id == earlier_slot_id


@pytest.mark.asyncio
async def test_availability_falls_back_when_preference_has_no_slots() -> None:
    api = FakeApi(
        [
            AvailabilityResponse(
                available=True,
                busy=[
                    BusyBlock(
                        start=datetime(2026, 10, 20, tzinfo=UTC),
                        end=datetime(2026, 10, 21, tzinfo=UTC),
                    )
                ],
            )
        ]
    )
    booking = _booking(api)

    result = await booking.check_availability("consultation", "Tuesday")

    assert result["ok"] is True
    assert result["matched_preference"] is False
    assert result["slots"]


@pytest.mark.asyncio
async def test_booking_refuses_slots_not_offered_in_this_call() -> None:
    api = FakeApi()
    booking = _booking(api)

    result = await booking.book_appointment("consultation@20261020T0900Z", "Alex")

    assert result == {
        "ok": False,
        "error": "Offer a slot from check_availability first.",
    }
    assert api.book_calls == []


@pytest.mark.asyncio
async def test_taken_slot_offers_alternatives_for_the_caller_to_choose() -> None:
    alternative = SlotOffer(
        slot_id="consultation@20261020T1000Z",
        start=datetime(2026, 10, 20, 10, tzinfo=UTC),
        end=datetime(2026, 10, 20, 10, 30, tzinfo=UTC),
        appointment_type="consultation",
        label="Initial consultation",
        speech="Tuesday the 20th of October at 11 am",
        text="Tue 20 Oct, 11:00am",
    )
    api = FakeApi(book=BookResponse(status="unavailable", alternatives=[alternative]))
    booking = _booking(api)
    offered = await booking.check_availability("consultation")
    slot_id = offered["slots"][0]["slot_id"]

    result = await booking.book_appointment(slot_id, "Alex")

    assert result["status"] == "taken"
    assert result["alternatives"][0]["slot_id"] == alternative.slot_id
    assert alternative.slot_id in booking._offered
