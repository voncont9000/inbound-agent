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
    Slot,
    SlotUnavailable,
    alternatives_for,
    booking_window,
    format_slot_speech,
    format_slot_text,
    parse_slot_id,
    slot_offer,
    validate_slot,
)
from inbound_core.config import AppointmentType, AssistantConfig, BookingConfig
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api import graph
from inbound_api.integrations.calendar import CalendarProvider, calendar_provider_for
from inbound_api.integrations.google_auth import action_google_connection_for
from inbound_api.models import (
    Assistant,
    CalendarBooking,
    Commitment,
    CommitmentStatus,
    Conversation,
    Message,
    MessageRole,
    Person,
)
from inbound_api.settings import get_settings

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
    settings = get_settings()
    if settings.calendar_mode == "off":
        return None
    tenant_id = await session.scalar(
        select(Assistant.tenant_id).where(Assistant.id == assistant_id)
    )
    if tenant_id is None:
        return None
    connection = await action_google_connection_for(
        session, assistant_id=assistant_id, tenant_id=tenant_id, feature="calendar"
    )
    if connection is None:
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
    if get_settings().calendar_mode == "off":
        return False
    tenant_id = await session.scalar(
        select(Assistant.tenant_id).where(Assistant.id == assistant_id)
    )
    if tenant_id is None:
        return False
    connection = await action_google_connection_for(
        session, assistant_id=assistant_id, tenant_id=tenant_id, feature="calendar"
    )
    return connection is not None


async def fetch_busy(session: AsyncSession, ctx: BookingContext, assistant_id: UUID, now: datetime):
    window = booking_window(ctx.booking, now)
    started = time.perf_counter()
    busy = await ctx.provider.free_busy(ctx.booking.calendar_id, window.start, window.end)
    provider_ms = round((time.perf_counter() - started) * 1000)
    rows = await session.scalars(
        select(CalendarBooking).where(
            CalendarBooking.assistant_id == assistant_id,
            CalendarBooking.calendar_id == ctx.booking.calendar_id,
            CalendarBooking.status == "booked",
            CalendarBooking.start_at < window.end,
            CalendarBooking.end_at > window.start,
        )
    )
    busy.extend(BusyBlock(start=row.start_at, end=row.end_at) for row in rows)
    return window, busy, provider_ms


def _booking_record(ctx: BookingContext, booking: CalendarBooking, now: datetime) -> BookingRecord:
    appointment = next(
        (item for item in ctx.booking.appointment_types if item.key == booking.appointment_type),
        None,
    )
    if appointment is None:
        duration_minutes = int((booking.end_at - booking.start_at).total_seconds() // 60)
        appointment = AppointmentType(
            key=booking.appointment_type,
            label=booking.appointment_type,
            duration_minutes=max(1, min(480, duration_minutes)),
            when_to_use="",
        )
    slot = Slot(
        appointment_type=appointment,
        start=booking.start_at,
        end=booking.end_at,
    )
    return BookingRecord(
        booking_id=str(booking.id),
        appointment_type=booking.appointment_type,
        label=appointment.label,
        start=booking.start_at,
        end=booking.end_at,
        timezone=ctx.timezone,
        speech=format_slot_speech(slot, ctx.timezone, now),
        text=format_slot_text(slot, ctx.timezone),
        event_id=booking.event_id,
        event_link=booking.event_link,
        calendar_id=booking.calendar_id,
        attendee_name=booking.attendee_name,
        attendee_email=booking.attendee_email,
        provider=booking.provider,
    )


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
        return BookResponse(status="booked", booking=_booking_record(ctx, existing, now))

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{conversation.assistant_id}:{ctx.booking.calendar_id}"},
    )
    try:
        existing = await session.scalar(
            select(CalendarBooking).where(
                CalendarBooking.conversation_id == conversation.id,
                CalendarBooking.slot_id == slot_id,
                CalendarBooking.status == "booked",
            )
        )
        if existing is not None:
            return BookResponse(status="booked", booking=_booking_record(ctx, existing, now))
        _, busy, _ = await fetch_busy(session, ctx, conversation.assistant_id, now)
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
        original_metrics = conversation.metrics
        original_person_id = conversation.person_id
        try:
            async with session.begin_nested():
                booking = CalendarBooking(
                    tenant_id=tenant_id,
                    assistant_id=conversation.assistant_id,
                    conversation_id=conversation.id,
                    person_id=original_person_id,
                    calendar_id=ctx.booking.calendar_id,
                    provider=ctx.provider.name,
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
                person_id = original_person_id
                if person_id is None:
                    person_id = await graph.resolve_person(
                        session, phone=phone, email=email, source=conversation.channel.value
                    )
                if person_id is None:
                    person = Person(
                        tenant_id=tenant_id,
                        display_name=name.strip() or "Unknown caller",
                    )
                    session.add(person)
                    await session.flush()
                    person_id = person.id
                if person_id:
                    conversation.person_id = person_id
                    booking.person_id = person_id
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
                await session.flush()
        except Exception:
            conversation.metrics = original_metrics
            conversation.person_id = original_person_id
            LOGGER.exception("Calendar booking persistence failed")
            try:
                await ctx.provider.cancel_event(ctx.booking.calendar_id, event.event_id)
            except Exception:
                LOGGER.exception("Calendar booking compensation failed")
            return BookResponse(status="error", reason="calendar_error")
        return BookResponse(status="booked", booking=record)
    except Exception:
        LOGGER.exception("Calendar booking failed")
        return BookResponse(status="error", reason="calendar_error")
