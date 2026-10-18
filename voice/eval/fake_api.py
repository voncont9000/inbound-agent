from __future__ import annotations

import argparse
import asyncio
import hmac
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from inbound_core.booking import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookingRecord,
    BookRequest,
    BookResponse,
    BusyBlock,
    SlotUnavailable,
    alternatives_for,
    booking_window,
    format_slot_speech,
    format_slot_text,
    parse_slot_id,
    slot_offer,
    validate_slot,
)
from inbound_core.call_report import CallReport, CallStart, RouteResponse

from inbound_voice.config import load_assistant_config


def create_fake_api(
    route_response: RouteResponse,
    token: str,
    *,
    fail_reports: int = 0,
    to_number: str | None = None,
    calendar_delay_ms: int = 300,
    busy_blocks: list[dict] | None = None,
) -> FastAPI:
    app = FastAPI()
    route_response = route_response.model_copy(
        update={
            "booking_available": route_response.config.booking is not None
            and {"check_availability", "book_appointment"}
            <= set(route_response.config.live_actions)
        }
    )
    app.state.fake = {
        "token": token,
        "route_response": route_response,
        "to_number": to_number,
        "fail_reports": fail_reports,
        "calls": [],
        "reports": [],
        "bookings": [],
        "busy": [BusyBlock.model_validate(item) for item in (busy_blocks or [])],
        "calendar_delay_ms": calendar_delay_ms,
    }

    async def require_token(x_service_token: str | None) -> None:
        if not x_service_token or not hmac.compare_digest(x_service_token, app.state.fake["token"]):
            raise HTTPException(status_code=401, detail="Invalid service token")

    def busy_for_window(start: datetime, end: datetime) -> list[BusyBlock]:
        busy = list(app.state.fake["busy"])
        busy.extend(
            BusyBlock(start=item["booking"]["start"], end=item["booking"]["end"])
            for item in app.state.fake["bookings"]
        )
        return [block for block in busy if block.start < end and start < block.end]

    @app.post("/internal/voice/availability", response_model=AvailabilityResponse)
    async def availability(
        payload: AvailabilityRequest,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> AvailabilityResponse:
        await require_token(x_service_token)
        if payload.channel == "phone":
            expected = app.state.fake["to_number"]
            if expected is not None and payload.to_number != expected:
                raise HTTPException(status_code=404, detail="No configured destination")
        config = app.state.fake["route_response"].config
        if not app.state.fake["route_response"].booking_available or config.booking is None:
            return AvailabilityResponse(available=False, reason="booking_disabled")
        delay = app.state.fake["calendar_delay_ms"]
        if delay:
            await asyncio.sleep(delay / 1000)
        now = datetime.now(UTC)
        window = booking_window(config.booking, now)
        busy = busy_for_window(window.start, window.end)
        return AvailabilityResponse(
            available=True,
            timezone=config.office_hours.timezone,
            window_start=window.start,
            window_end=window.end,
            busy=busy,
            fetched_at=now,
            provider_ms=delay,
        )

    @app.post("/internal/voice/book", response_model=BookResponse)
    async def book(
        payload: BookRequest,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> BookResponse:
        await require_token(x_service_token)
        route = app.state.fake["route_response"]
        config = route.config
        if not route.booking_available or config.booking is None:
            return BookResponse(status="invalid", reason="booking_disabled")
        delay = app.state.fake["calendar_delay_ms"]
        if delay:
            await asyncio.sleep(delay / 1000)
        existing = next(
            (
                item
                for item in app.state.fake["bookings"]
                if item["conversation_id"] == str(payload.conversation_id)
                and item["slot_id"] == payload.slot_id
            ),
            None,
        )
        if existing is not None:
            return BookResponse(
                status="booked",
                booking=BookingRecord.model_validate(existing["booking"]),
            )
        now = datetime.now(UTC)
        window = booking_window(config.booking, now)
        busy = busy_for_window(window.start, window.end)
        try:
            slot = validate_slot(config, payload.slot_id, busy, now)
        except SlotUnavailable as error:
            if error.reason in {"invalid", "unknown_type"}:
                return BookResponse(status="invalid", reason=error.reason)
            try:
                appointment_type, requested_start = parse_slot_id(payload.slot_id)
            except ValueError:
                return BookResponse(status="invalid", reason="invalid")
            options = alternatives_for(
                config,
                appointment_type,
                busy,
                now,
                requested_start,
            )
            return BookResponse(
                status="unavailable",
                reason=error.reason,
                alternatives=[
                    slot_offer(item, config.office_hours.timezone, now) for item in options
                ],
            )
        event_id = f"fake-{uuid4()}"
        label = slot.appointment_type.label
        record = BookingRecord(
            booking_id=str(uuid4()),
            appointment_type=slot.appointment_type.key,
            label=label,
            start=slot.start,
            end=slot.end,
            timezone=config.office_hours.timezone,
            speech=format_slot_speech(slot, config.office_hours.timezone, now),
            text=format_slot_text(slot, config.office_hours.timezone),
            event_id=event_id,
            event_link=f"https://calendar.google.com/calendar/event?eid={event_id}",
            calendar_id=config.booking.calendar_id,
            attendee_name=payload.name,
            attendee_email=payload.email,
            provider="fake",
        )
        app.state.fake["bookings"].append(
            {
                "conversation_id": str(payload.conversation_id),
                "slot_id": payload.slot_id,
                "appointment_type": record.appointment_type,
                "booking": record.model_dump(mode="json"),
            }
        )
        return BookResponse(status="booked", booking=record)

    @app.get("/internal/voice/route", response_model=RouteResponse, dependencies=[])
    async def route_call(
        to: str = Query(...),
        from_number: str | None = Query(default=None, alias="from"),
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> RouteResponse:
        await require_token(x_service_token)
        expected = app.state.fake["to_number"]
        if expected is not None and to != expected:
            raise HTTPException(status_code=404, detail="No configured destination")
        return app.state.fake["route_response"]

    @app.post(
        "/internal/voice/calls",
        status_code=201,
        dependencies=[],
    )
    async def start_call(
        payload: CallStart,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict[str, UUID]:
        await require_token(x_service_token)
        conversation_id = uuid4()
        app.state.fake["calls"].append(
            {"conversation_id": str(conversation_id), **payload.model_dump(mode="json")}
        )
        return {"conversation_id": conversation_id}

    @app.post(
        "/internal/voice/calls/{conversation_id}/report",
        status_code=202,
        dependencies=[],
    )
    async def post_report(
        conversation_id: UUID,
        payload: CallReport,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict[str, bool]:
        await require_token(x_service_token)
        if app.state.fake["fail_reports"] > 0:
            app.state.fake["fail_reports"] -= 1
            raise HTTPException(status_code=503, detail="Configured fake report failure")
        app.state.fake["reports"].append(
            {
                "conversation_id": str(conversation_id),
                **payload.model_dump(mode="json"),
            }
        )
        return {"accepted": True}

    @app.get("/_fake/state")
    async def state(
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict:
        await require_token(x_service_token)
        return {
            "calls": app.state.fake["calls"],
            "reports": app.state.fake["reports"],
            "bookings": app.state.fake["bookings"],
        }

    @app.post("/_fake/assistant")
    async def change_assistant(
        payload: dict,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict[str, str]:
        await require_token(x_service_token)
        assistant_path = Path(payload.get("assistant", ""))
        root = Path(__file__).resolve().parents[1]
        if not assistant_path.is_absolute():
            assistant_path = root / assistant_path
        assistant_path = assistant_path.resolve()
        if root.resolve() not in assistant_path.parents:
            raise HTTPException(
                status_code=400, detail="Assistant path must be within the voice repo"
            )
        assistant = load_assistant_config(assistant_path)
        app.state.fake["route_response"] = app.state.fake["route_response"].model_copy(
            update={
                "config": assistant,
                "digest": assistant.knowledge.digest_content,
                "booking_available": assistant.booking is not None
                and {"check_availability", "book_appointment"} <= set(assistant.live_actions),
            }
        )
        return {"assistant": str(assistant_path)}

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an in-memory fake inbound voice API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--token", default="dev-token")
    parser.add_argument("--to", default="+441234567890")
    parser.add_argument(
        "--assistant",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml",
    )
    parser.add_argument("--context-card")
    parser.add_argument("--calendar-delay-ms", type=int, default=300)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    assistant = load_assistant_config(args.assistant)
    route_response = RouteResponse(
        tenant_id=UUID("10000000-0000-0000-0000-000000000001"),
        assistant_id=UUID("20000000-0000-0000-0000-000000000002"),
        config=assistant,
        digest=assistant.knowledge.digest_content,
        context_card=args.context_card,
        booking_available=assistant.booking is not None
        and {"check_availability", "book_appointment"} <= set(assistant.live_actions),
    )
    app = create_fake_api(
        route_response,
        args.token,
        to_number=args.to,
        calendar_delay_ms=args.calendar_delay_ms,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
