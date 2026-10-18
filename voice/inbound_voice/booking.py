from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from inbound_core.booking import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookRequest,
    BusyBlock,
    Slot,
    alternatives_for,
    format_slot_speech,
    generate_slots,
    parse_preference,
    select_offered_slots,
)
from inbound_core.call_report import CallStart
from inbound_core.config import AssistantConfig

from inbound_voice.api_client import VoiceApiClient


class CallBooking:
    def __init__(
        self,
        config: AssistantConfig,
        api: VoiceApiClient,
        call_start: CallStart,
        availability_task: asyncio.Task[AvailabilityResponse] | None,
        conversation_id_getter: Callable[[], Any],
        caller_phone: str | None,
        metrics_observer: Any,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.api = api
        self.call_start = call_start
        self.availability_task = availability_task
        self.conversation_id_getter = conversation_id_getter
        self.caller_phone = caller_phone
        self.metrics_observer = metrics_observer
        self.now_fn = now_fn
        self._availability: AvailabilityResponse | None = None
        self._offered: dict[str, Slot] = {}
        self._busy: list[BusyBlock] = []

    async def check_availability(
        self, appointment_type: str, preference_text: str = ""
    ) -> dict[str, Any]:
        booking = self.config.booking
        if booking is None:
            return {"ok": False, "error": "Appointment booking is not enabled."}
        appointment = next(
            (item for item in booking.appointment_types if item.key == appointment_type), None
        )
        if appointment is None:
            return {"ok": False, "error": "Unknown appointment type."}
        response = await self._get_availability()
        if response is None:
            return {"ok": False, "error": "The diary can't be checked right now."}
        now = self.now_fn()
        self._busy = list(response.busy)
        windows = (
            parse_preference(
                preference_text, now, self.config.office_hours.timezone, booking.horizon_days
            )
            if preference_text.strip()
            else None
        )
        slots = generate_slots(self.config, appointment_type, self._busy, now, windows)
        matched = True
        if windows is not None and not slots:
            slots = alternatives_for(
                self.config,
                appointment_type,
                self._busy,
                now,
                windows[0].start,
                booking.max_slots_offered,
            )
            matched = False
        chosen = select_offered_slots(slots, booking.max_slots_offered)
        self._offered.update({slot.slot_id: slot for slot in chosen})
        return {
            "ok": True,
            "appointment": appointment.label,
            "slots": [
                {
                    "slot_id": slot.slot_id,
                    "when": self._speech(slot, now),
                }
                for slot in chosen
            ],
            "matched_preference": matched,
        }

    async def book_appointment(
        self, slot_id: str, name: str, email: str | None = None
    ) -> dict[str, Any]:
        slot = self._offered.get(slot_id)
        if slot is None:
            return {"ok": False, "error": "Offer a slot from check_availability first."}
        conversation_id = self.conversation_id_getter()
        if asyncio.iscoroutine(conversation_id):
            conversation_id = await conversation_id
        if conversation_id is None:
            return {"ok": False, "error": "The conversation could not be saved."}
        try:
            response = await self.api.book(
                BookRequest(
                    conversation_id=UUID(str(conversation_id)),
                    external_call_id=self.call_start.external_call_id,
                    slot_id=slot_id,
                    name=name,
                    email=email,
                    phone=self.caller_phone,
                )
            )
        except Exception:
            return {"ok": False, "error": "The diary couldn't be updated right now."}
        now = self.now_fn()
        if response.status == "booked":
            self._busy.append(BusyBlock(start=slot.start, end=slot.end))
            return {
                "ok": True,
                "status": "booked",
                "when": self._speech(slot, now),
                "appointment": slot.appointment_type.label,
            }
        if response.status == "unavailable":
            for offer in response.alternatives:
                if offer.slot_id in self._offered:
                    continue
                self._offered[offer.slot_id] = Slot(
                    appointment_type=slot.appointment_type,
                    start=offer.start,
                    end=offer.end,
                )
            return {
                "ok": False,
                "status": "taken",
                "alternatives": [
                    {"slot_id": item.slot_id, "when": item.speech} for item in response.alternatives
                ],
            }
        return {"ok": False, "error": "The diary couldn't be updated right now."}

    async def _get_availability(self) -> AvailabilityResponse | None:
        if self._availability is not None:
            return self._availability
        response = None
        if self.availability_task is not None:
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(self.availability_task), timeout=1.5
                )
            except Exception:
                response = None
        if response is None or not response.available:
            try:
                response = await self.api.availability(self._availability_request())
            except Exception:
                return None
        if not response.available:
            return None
        self._availability = response
        self._busy = list(response.busy)
        return response

    def _availability_request(self) -> AvailabilityRequest:
        if self.call_start.channel == "web" and self.call_start.web_call_token:
            return AvailabilityRequest(channel="web", web_call_token=self.call_start.web_call_token)
        return AvailabilityRequest(channel="phone", to_number=self.call_start.to_number)

    def _speech(self, slot: Slot, now: datetime) -> str:
        return format_slot_speech(slot, self.config.office_hours.timezone, now)
