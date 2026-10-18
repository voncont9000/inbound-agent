from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from inbound_core.config import AppointmentType, AssistantConfig, BookingConfig


class BusyBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone.")
        return value


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone.")
        return value


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    appointment_type: AppointmentType
    start: datetime
    end: datetime

    @property
    def slot_id(self) -> str:
        key = self.appointment_type.key
        return f"{key}@{self.start.astimezone(UTC):%Y%m%dT%H%MZ}"


class SlotUnavailable(ValueError):
    def __init__(
        self,
        reason: Literal[
            "invalid", "unknown_type", "outside_hours", "too_soon", "beyond_horizon", "busy"
        ],
    ):
        self.reason = reason
        super().__init__(reason)


class SlotOffer(BaseModel):
    slot_id: str
    start: datetime
    end: datetime
    appointment_type: str
    label: str
    speech: str
    text: str


class AvailabilityRequest(BaseModel):
    channel: Literal["phone", "web"]
    to_number: str | None = None
    web_call_token: str | None = None


class AvailabilityResponse(BaseModel):
    available: bool
    reason: str | None = None
    timezone: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    busy: list[BusyBlock] = Field(default_factory=list)
    fetched_at: datetime | None = None
    provider_ms: int | None = None


class BookRequest(BaseModel):
    conversation_id: UUID
    external_call_id: str
    slot_id: str
    name: str
    email: str | None = None
    phone: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()):
            raise ValueError("Invalid email address.")
        return value.strip() if value is not None else None


class BookingRecord(BaseModel):
    booking_id: str
    appointment_type: str
    label: str
    start: datetime
    end: datetime
    timezone: str
    speech: str
    text: str
    event_id: str
    event_link: str | None = None
    calendar_id: str
    attendee_name: str
    attendee_email: str | None = None
    provider: str


class BookResponse(BaseModel):
    status: Literal["booked", "unavailable", "invalid", "error"]
    reason: str | None = None
    booking: BookingRecord | None = None
    alternatives: list[SlotOffer] = Field(default_factory=list)


def parse_slot_id(slot_id: str) -> tuple[str, datetime]:
    match = re.fullmatch(r"([a-z0-9_]+)@(\d{8}T\d{4}Z)", slot_id)
    if match is None:
        raise ValueError("Invalid slot id.")
    try:
        start = datetime.strptime(match.group(2), "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("Invalid slot id.") from error
    return match.group(1), start


def booking_window(booking: BookingConfig, now: datetime) -> TimeWindow:
    _require_aware(now)
    return TimeWindow(
        start=now + timedelta(hours=booking.min_notice_hours),
        end=now + timedelta(days=booking.horizon_days),
    )


def generate_slots(
    config: AssistantConfig,
    appointment_type_key: str,
    busy: list[BusyBlock],
    now: datetime,
    windows: list[TimeWindow] | None = None,
) -> list[Slot]:
    booking = config.booking
    if booking is None:
        return []
    appointment = next(
        (item for item in booking.appointment_types if item.key == appointment_type_key), None
    )
    if appointment is None:
        return []
    _require_aware(now)
    window = booking_window(booking, now)
    tz = _timezone(config.office_hours.timezone)
    first_date = window.start.astimezone(tz).date()
    last_date = window.end.astimezone(tz).date()
    allowed_days = {day[:3].casefold() for day in config.office_hours.days}
    opens = _parse_clock(config.office_hours.opens)
    closes = _parse_clock(config.office_hours.closes)
    duration = timedelta(minutes=appointment.duration_minutes)
    buffer = timedelta(minutes=booking.buffer_minutes)
    result = []
    current_day = first_date
    while current_day <= last_date:
        if current_day.strftime("%a").casefold() in allowed_days:
            local_open = datetime.combine(current_day, opens, tzinfo=tz)
            local_close = datetime.combine(current_day, closes, tzinfo=tz)
            candidate = local_open
            while candidate + duration <= local_close:
                end = candidate + duration
                if (
                    window.start <= candidate <= window.end
                    and not any(
                        _overlaps(candidate - buffer, end + buffer, block.start, block.end)
                        for block in busy
                    )
                    and (
                        windows is None
                        or any(item.start <= candidate < item.end for item in windows)
                    )
                ):
                    result.append(Slot(appointment_type=appointment, start=candidate, end=end))
                candidate += timedelta(minutes=booking.slot_step_minutes)
        current_day += timedelta(days=1)
    return result


def select_offered_slots(slots: list[Slot], limit: int) -> list[Slot]:
    ordered = sorted(slots, key=lambda slot: slot.start.astimezone(UTC))
    selected = []
    selected_ids = set()
    for slot in ordered:
        if not selected or slot.start.astimezone(UTC) >= selected[-1].start.astimezone(
            UTC
        ) + timedelta(hours=2):
            selected.append(slot)
            selected_ids.add(slot.slot_id)
            if len(selected) >= limit:
                return selected
    if len(selected) < limit:
        for slot in ordered:
            if slot.slot_id not in selected_ids:
                selected.append(slot)
                if len(selected) >= limit:
                    break
    return sorted(selected, key=lambda slot: slot.start.astimezone(UTC))


def validate_slot(
    config: AssistantConfig, slot_id: str, busy: list[BusyBlock], now: datetime
) -> Slot:
    booking = config.booking
    if booking is None:
        raise SlotUnavailable("invalid")
    try:
        type_key, start_utc = parse_slot_id(slot_id)
    except ValueError as error:
        raise SlotUnavailable("invalid") from error
    appointment = next((item for item in booking.appointment_types if item.key == type_key), None)
    if appointment is None:
        raise SlotUnavailable("unknown_type")
    tz = _timezone(config.office_hours.timezone)
    start = start_utc.astimezone(tz)
    duration = timedelta(minutes=appointment.duration_minutes)
    end = start + duration
    window = booking_window(booking, now)
    if start < window.start:
        raise SlotUnavailable("too_soon")
    if start > window.end:
        raise SlotUnavailable("beyond_horizon")
    weekday = start.strftime("%a").casefold()
    if weekday not in {day[:3].casefold() for day in config.office_hours.days}:
        raise SlotUnavailable("outside_hours")
    opens = _parse_clock(config.office_hours.opens)
    closes = _parse_clock(config.office_hours.closes)
    start_clock = start.timetz().replace(tzinfo=None)
    end_clock = end.timetz().replace(tzinfo=None)
    elapsed = start_clock.hour * 60 + start_clock.minute - opens.hour * 60 - opens.minute
    if (
        elapsed < 0
        or elapsed % booking.slot_step_minutes != 0
        or end_clock > closes
        or end.date() != start.date()
    ):
        raise SlotUnavailable("outside_hours")
    buffer = timedelta(minutes=booking.buffer_minutes)
    if any(_overlaps(start - buffer, end + buffer, block.start, block.end) for block in busy):
        raise SlotUnavailable("busy")
    return Slot(appointment_type=appointment, start=start, end=end)


def alternatives_for(
    config: AssistantConfig,
    type_key: str,
    busy: list[BusyBlock],
    now: datetime,
    near: datetime,
) -> list[Slot]:
    booking = config.booking
    if booking is None:
        return []
    tz = _timezone(config.office_hours.timezone)
    same_day = near.astimezone(tz).date()
    day_start = datetime.combine(same_day, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    same_day_windows = [TimeWindow(start=day_start, end=day_end)]
    slots = generate_slots(config, type_key, busy, now, same_day_windows)
    if not slots:
        slots = generate_slots(config, type_key, busy, now)
    return select_offered_slots(slots, booking.max_slots_offered)


def parse_preference(
    text: str, now: datetime, timezone: str, horizon_days: int
) -> list[TimeWindow] | None:
    _require_aware(now)
    tz = _timezone(timezone)
    local_now = now.astimezone(tz)
    phrase = " ".join(text.casefold().split())
    if phrase in {
        "",
        "any time",
        "anytime",
        "whenever",
        "asap",
        "as soon as possible",
        "earliest",
        "soonest",
        "first available",
    }:
        return None

    horizon_end = datetime.combine(
        local_now.date() + timedelta(days=horizon_days), time.max, tzinfo=tz
    )
    dates: list[date] | None = None
    for name, offset in (("day after tomorrow", 2), ("tomorrow", 1), ("today", 0)):
        if re.search(rf"\b{re.escape(name)}\b", phrase):
            dates = [local_now.date() + timedelta(days=offset)]
            break
    if dates is None and "next week" in phrase:
        monday = local_now.date() + timedelta(days=(7 - local_now.weekday()))
        dates = [monday + timedelta(days=index) for index in range(7)]
    elif dates is None and "this week" in phrase:
        dates = [
            local_now.date() + timedelta(days=index) for index in range(7 - local_now.weekday())
        ]
    elif dates is None and "weekend" in phrase:
        start = local_now.date() + timedelta(days=(5 - local_now.weekday()) % 7)
        dates = [start, start + timedelta(days=1)]

    if dates is None:
        for index, full_name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        ):
            names = (full_name.casefold(), full_name[:3].casefold())
            if any(re.search(rf"\b{re.escape(name)}\b", phrase) for name in names):
                strict = bool(
                    re.search(
                        rf"\bnext\s+(?:{full_name.casefold()}|{full_name[:3].casefold()})\b", phrase
                    )
                )
                delta = (index - local_now.weekday()) % 7
                if strict and delta == 0:
                    delta = 7
                dates = [local_now.date() + timedelta(days=delta)]
                break

    if dates is None:
        month_names = {
            name.casefold(): index
            for index, name in enumerate(
                (
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ),
                1,
            )
        }
        date_match = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b|\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            phrase,
        )
        numeric_match = re.search(r"\b(\d{1,2})/(\d{1,2})\b", phrase)
        month_match = (date_match.group(2) or date_match.group(3)) if date_match else None
        has_month = month_match is not None and any(
            name.startswith(month_match) for name in month_names
        )
        try:
            if date_match and has_month:
                day = int(date_match.group(1) or date_match.group(4))
                month_name = month_match
                month = next(
                    (n for name, n in month_names.items() if name.startswith(month_name)), None
                )
                if month:
                    year = local_now.year + (
                        1 if (month, day) < (local_now.month, local_now.day) else 0
                    )
                    dates = [date(year, month, day)]
            elif numeric_match:
                day, month = int(numeric_match.group(1)), int(numeric_match.group(2))
                year = local_now.year + (
                    1 if (month, day) < (local_now.month, local_now.day) else 0
                )
                dates = [date(year, month, day)]
            elif not has_month:
                ordinal_match = re.search(
                    r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b|\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                    phrase,
                )
                if ordinal_match:
                    day = int(ordinal_match.group(1) or ordinal_match.group(2))
                    month = local_now.month + (day < local_now.day)
                    year = local_now.year + (month > 12)
                    dates = [date(year, (month - 1) % 12 + 1, day)]
        except ValueError:
            dates = []

    day_period: tuple[time, time] | None = None
    if any(token in phrase for token in ("late afternoon", "end of the day")):
        day_period = (time(15), time(23, 59, 59))
    elif "morning" in phrase or "first thing" in phrase:
        day_period = (time(0), time(12))
    elif "lunchtime" in phrase:
        day_period = (time(12), time(14))
    elif "afternoon" in phrase:
        day_period = (time(12), time(17))
    elif "evening" in phrase:
        day_period = (time(17), time(23, 59, 59))

    hour_match = re.search(
        r"\b(after|before|at|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
        r"|\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(\d{1,2})\s+o'?clock\b",
        phrase,
    )
    if hour_match:
        if hour_match.group(1):
            mode, raw_hour, raw_min, suffix = (
                hour_match.group(1),
                hour_match.group(2),
                hour_match.group(3),
                hour_match.group(4),
            )
        elif hour_match.group(5):
            mode, raw_hour, raw_min, suffix = (
                "at",
                hour_match.group(5),
                hour_match.group(6),
                hour_match.group(7),
            )
        else:
            mode, raw_hour, raw_min, suffix = "at", hour_match.group(8), None, None
        hour = int(raw_hour)
        minute = int(raw_min or 0)
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        elif suffix is None and 1 <= hour <= 7:
            hour += 12
        if mode == "after":
            day_period = (time(hour, minute), time(23, 59, 59))
        elif mode == "before":
            day_period = (time(0), time(hour, minute))
        else:
            center = hour * 60 + minute
            day_period = (
                time(max(0, center - 30) // 60, max(0, center - 30) % 60),
                time(min(1439, center + 60) // 60, min(1439, center + 60) % 60),
            )

    if dates is None:
        dates = [local_now.date() + timedelta(days=index) for index in range(horizon_days + 1)]
    start_time, end_time = day_period or (time.min, time.max)
    windows = []
    for day in dates:
        start = datetime.combine(day, start_time, tzinfo=tz)
        end = datetime.combine(day, end_time, tzinfo=tz)
        if day_period and end.time() != time.max:
            end += timedelta(minutes=1)
        if start <= horizon_end and end > local_now:
            windows.append(TimeWindow(start=max(start, local_now), end=min(end, horizon_end)))
    return windows


def format_slot_speech(slot: Slot, timezone: str, now: datetime) -> str:
    local = slot.start.astimezone(_timezone(timezone))
    weekday = local.strftime("%A")
    month = (
        f" of {local.strftime('%B')}" if local.month != now.astimezone(local.tzinfo).month else ""
    )
    hour = local.hour % 12 or 12
    clock = (
        "at midday"
        if local.hour == 12 and local.minute == 0
        else (
            f"at {hour}{f':{local.minute:02d}' if local.minute else ''} "
            f"{'am' if local.hour < 12 else 'pm'}"
        )
    )
    return f"{weekday} the {local.day}{_ordinal_suffix(local.day)}{month} {clock}"


def format_slot_text(slot: Slot, timezone: str) -> str:
    local = slot.start.astimezone(_timezone(timezone))
    clock = local.strftime("%I:%M").lstrip("0")
    meridiem = "am" if local.hour < 12 else "pm"
    return f"{local.strftime('%a')} {local.day:02d} {local.strftime('%b')}, {clock}{meridiem}"


def slot_offer(slot: Slot, timezone: str, now: datetime) -> SlotOffer:
    return SlotOffer(
        slot_id=slot.slot_id,
        start=slot.start,
        end=slot.end,
        appointment_type=slot.appointment_type.key,
        label=slot.appointment_type.label,
        speech=format_slot_speech(slot, timezone, now),
        text=format_slot_text(slot, timezone),
    )


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {value}") from error


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include a timezone.")


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end


def _ordinal_suffix(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
