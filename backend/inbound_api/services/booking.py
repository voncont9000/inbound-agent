from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from inbound_core.booking import (
    BookingRecord,
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
from inbound_core.config import AssistantConfig, BookingConfig
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api import graph
from inbound_api.integrations.calendar import CalendarProvider
from inbound_api.integrations.google_auth import google_connection_for, has_google_feature
from inbound_api.models import (
    CalendarBooking,
    Commitment,
    CommitmentStatus,
    Conversation,
    Message,
    MessageRole,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class BookingContext:
    config: AssistantConfig
    booking: BookingConfig
    connection: object
    provider: CalendarProvider
    timezone: str


async def booking_context(
    session: AsyncSession, assistant_id: UUID, config: AssistantConfig
) -> BookingContext | None:
    if config.booking is None or not (
        {"check_availability", "book_appointment"} & set(config.live_actions)
    ):
        return None
    from inbound_api.integrations.calendar import calendar_provider_for
    from inbound_api.settings import get_settings

    settings = get_settings()
    if settings.calendar_mode == "off":
        return None
    connection = await google_connection_for(session, assistant_id)
    if connection is None or not has_google_feature(connection, "calendar"):
        return None
    provider = await calendar_provider_for(connection)
    if provider is None:
        return None
    return BookingContext(
        config=config,
        booking=config.booking,
        connection=connection,
        provider=provider,
        timezone=config.office_hours.timezone,
    )


async def booking_available(
    session: AsyncSession, assistant_id: UUID, config: AssistantConfig
) -> bool:
    if config.booking is None or not (
        {"check_availability", "book_appointment"} & set(config.live_actions)
    ):
        return False
    from inbound_api.settings import get_settings

    if get_settings().calendar_mode == "off":
        return False
    connection = await google_connection_for(session, assistant_id)
    return connection is not None and has_google_feature(connection, "calendar")


async def fetch_busy(ctx: BookingContext, now: datetime):
    window = booking_window(ctx.booking, now)
    started = time.perf_counter()
    busy = await ctx.provider.free_busy(ctx.booking.calendar_id, window.start, window.end)
    return window, busy, round((time.perf_counter() - started) * 1000)


async def book_slot(
    session: AsyncSession,
    tenant_id: UUID,
    conversation: Conversation,
    ctx: BookingContext,
    *,
    slot_id: str,
    name: str,
    email: str | None,
    phone: str | None,
    now: datetime | None = None,
) -> BookResponse:
    now = now or datetime.now(UTC)
    round_trip_started = time.perf_counter()
    existing = await session.scalar(
        select(CalendarBooking).where(
            CalendarBooking.conversation_id == conversation.id,
            CalendarBooking.slot_id == slot_id,
            CalendarBooking.status == "booked",
        )
    )
    if existing is not None:
        stored = (conversation.metrics or {}).get("booking")
        if isinstance(stored, dict) and stored.get("booking_id") == str(existing.id):
            return BookResponse(status="booked", booking=BookingRecord.model_validate(stored))

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{tenant_id}:{ctx.booking.calendar_id}"},
    )
    try:
        window = booking_window(ctx.booking, now)
        busy = await ctx.provider.free_busy(ctx.booking.calendar_id, window.start, window.end)
        rows = await session.scalars(
            select(CalendarBooking).where(
                CalendarBooking.calendar_id == ctx.booking.calendar_id,
                CalendarBooking.status == "booked",
                CalendarBooking.start_at < window.end,
                CalendarBooking.end_at > window.start,
            )
        )
        busy.extend(BusyBlock(start=row.start_at, end=row.end_at) for row in rows)
        try:
            slot = validate_slot(ctx.config, slot_id, busy, now)
        except SlotUnavailable as error:
            if error.reason in {"invalid", "unknown_type"}:
                return BookResponse(status="invalid", reason=error.reason)
            try:
                key, start = parse_slot_id(slot_id)
            except ValueError:
                return BookResponse(status="invalid", reason="invalid")
            options = alternatives_for(ctx.config, key, busy, now, start)
            return BookResponse(
                status="unavailable",
                reason=error.reason,
                alternatives=[slot_offer(item, ctx.timezone, now) for item in options],
            )
        label = slot.appointment_type.label
        description = (
            f"Name: {name}\nPhone: {phone or 'Not provided'}\nEmail: {email or 'Not provided'}\n"
            f"Conversation: {conversation.id}\nBooked by the AI assistant"
        )
        event = await ctx.provider.create_event(
            ctx.booking.calendar_id,
            slot.start,
            slot.end,
            f"{label} – {name}",
            description,
            email,
            location=slot.appointment_type.location_or_link,
            send_updates=ctx.booking.send_updates,
        )
        try:
            booking = CalendarBooking(
                tenant_id=tenant_id,
                assistant_id=conversation.assistant_id,
                conversation_id=conversation.id,
                person_id=conversation.person_id,
                calendar_id=ctx.booking.calendar_id,
                provider="google"
                if type(ctx.provider).__name__ == "GoogleCalendarProvider"
                else "fake",
                event_id=event.event_id,
                event_link=event.html_link,
                appointment_type=slot.appointment_type.key,
                slot_id=slot.slot_id,
                start_at=slot.start,
                end_at=slot.end,
                attendee_name=name,
                attendee_email=email,
                attendee_phone=phone,
            )
            session.add(booking)
            await session.flush()
            speech = format_slot_speech(slot, ctx.timezone, now)
            text_value = format_slot_text(slot, ctx.timezone)
            message = Message(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=MessageRole.SYSTEM,
                text=f"Appointment booked: {label}, {text_value} ({ctx.timezone})",
                ts=now,
                meta={"booking_id": str(booking.id)},
            )
            session.add(message)
            await session.flush()
            person_id = conversation.person_id
            if person_id is None:
                try:
                    async with session.begin_nested():
                        person_id = await graph.resolve_person(
                            session, phone=phone, email=email, source=conversation.channel.value
                        )
                except Exception:
                    LOGGER.warning("Could not resolve booking attendee", exc_info=True)
                if person_id:
                    conversation.person_id = person_id
                    booking.person_id = person_id
            if person_id:
                session.add(
                    Commitment(
                        tenant_id=tenant_id,
                        person_id=person_id,
                        kind="appointment",
                        what=f"Appointment: {label}, {text_value}",
                        due_at=slot.start,
                        status=CommitmentStatus.OPEN,
                        source_message_id=message.id,
                    )
                )
            record = BookingRecord(
                booking_id=str(booking.id),
                appointment_type=slot.appointment_type.key,
                label=label,
                start=slot.start,
                end=slot.end,
                timezone=ctx.timezone,
                speech=speech,
                text=text_value,
                event_id=event.event_id,
                event_link=event.html_link,
                calendar_id=ctx.booking.calendar_id,
                attendee_name=name,
                attendee_email=email,
                provider=booking.provider,
            )
            conversation.metrics = {
                **(conversation.metrics or {}),
                "booking": {
                    **record.model_dump(mode="json"),
                    "status": "booked",
                    "booked_at": now.isoformat(),
                    "round_trip_ms": round((time.perf_counter() - round_trip_started) * 1000),
                },
            }
            return BookResponse(status="booked", booking=record)
        except Exception:
            await ctx.provider.cancel_event(ctx.booking.calendar_id, event.event_id)
            raise
    except Exception:
        LOGGER.exception("Calendar booking failed")
        return BookResponse(status="error", reason="calendar_error")
