from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from inbound_core.booking import (
    BusyBlock,
    Slot,
    SlotUnavailable,
    TimeWindow,
    alternatives_for,
    booking_window,
    format_slot_speech,
    format_slot_text,
    generate_slots,
    parse_preference,
    parse_slot_id,
    select_offered_slots,
    validate_slot,
)
from inbound_core.config import AssistantConfig, BookingConfig


def _config(**booking_changes) -> AssistantConfig:
    booking = {
        "appointment_types": [
            {
                "key": "initial_consultation",
                "label": "Initial consultation",
                "duration_minutes": 60,
                "when_to_use": "For new enquiries",
            }
        ],
        "min_notice_hours": 0,
        "horizon_days": 14,
        "slot_step_minutes": 30,
        **booking_changes,
    }
    return AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "office_hours": {
                "days": ["Mon", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "opens": "09:00",
                "closes": "17:00",
                "timezone": "Europe/London",
            },
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {},
            "urgency_rules": [],
            "live_actions": [],
            "booking": booking,
        }
    )


def test_booking_config_validates_unique_keys_and_bounds() -> None:
    with pytest.raises(ValidationError):
        BookingConfig(
            appointment_types=[
                {
                    "key": "initial",
                    "label": "First",
                    "duration_minutes": 30,
                    "when_to_use": "New client",
                },
                {
                    "key": "initial",
                    "label": "Second",
                    "duration_minutes": 30,
                    "when_to_use": "New client",
                },
            ]
        )
    with pytest.raises(ValidationError):
        BookingConfig(
            appointment_types=[
                {
                    "key": "Initial",
                    "label": "First",
                    "duration_minutes": 30,
                    "when_to_use": "New client",
                }
            ]
        )


def test_slot_id_roundtrip_and_rejects_invalid_values() -> None:
    config = _config()
    slot = Slot(
        appointment_type=config.booking.appointment_types[0],
        start=datetime(2026, 10, 25, 9, tzinfo=UTC),
        end=datetime(2026, 10, 25, 10, tzinfo=UTC),
    )
    assert slot.slot_id == "initial_consultation@20261025T0900Z"
    assert parse_slot_id(slot.slot_id) == (
        "initial_consultation",
        datetime(2026, 10, 25, 9, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        parse_slot_id("not-a-slot")


@pytest.mark.parametrize(
    ("local_day", "expected_utc_hour"),
    [(date(2026, 3, 30), 8), (date(2026, 10, 26), 9)],
)
def test_slot_local_times_follow_daylight_saving(local_day: date, expected_utc_hour: int) -> None:
    config = _config()
    now = datetime.combine(local_day, time(0), tzinfo=UTC)
    slots = generate_slots(config, "initial_consultation", [], now)
    slot = next(item for item in slots if item.start.date() == local_day)
    assert slot.start.hour == 9
    assert slot.start.astimezone(UTC).hour == expected_utc_hour


def test_slots_obey_notice_horizon_busy_buffers_and_time_windows() -> None:
    config = _config(min_notice_hours=2, horizon_days=2, buffer_minutes=15)
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    window = booking_window(config.booking, now)
    assert window.start == datetime(2026, 10, 19, 10, tzinfo=UTC)
    assert window.end == datetime(2026, 10, 21, 8, tzinfo=UTC)
    busy = [
        BusyBlock(
            start=datetime(2026, 10, 19, 9, 45, tzinfo=UTC),
            end=datetime(2026, 10, 19, 10, tzinfo=UTC),
        )
    ]
    slots = generate_slots(config, "initial_consultation", busy, now)
    assert all(slot.start.astimezone(UTC) >= window.start for slot in slots)
    assert all(
        slot.end.astimezone(UTC) + timedelta(minutes=15) <= busy[0].start
        or slot.start.astimezone(UTC) - timedelta(minutes=15) >= busy[0].end
        for slot in slots
        if slot.start.date() == date(2026, 10, 19)
    )
    morning = TimeWindow(
        start=datetime(2026, 10, 20, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 12, tzinfo=UTC),
    )
    assert all(
        slot.start.astimezone(UTC).date() == date(2026, 10, 20)
        for slot in generate_slots(config, "initial_consultation", [], now, [morning])
    )


def test_half_open_busy_intervals_allow_touching_edges() -> None:
    config = _config()
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    busy = [
        BusyBlock(
            start=datetime(2026, 10, 19, 10, tzinfo=UTC), end=datetime(2026, 10, 19, 11, tzinfo=UTC)
        )
    ]
    slots = generate_slots(config, "initial_consultation", busy, now)
    assert any(
        slot.start.astimezone(UTC) == datetime(2026, 10, 19, 9, tzinfo=UTC) for slot in slots
    )
    assert any(
        slot.start.astimezone(UTC) == datetime(2026, 10, 19, 11, tzinfo=UTC) for slot in slots
    )
    assert not any(
        slot.start.astimezone(UTC) == datetime(2026, 10, 19, 10, tzinfo=UTC) for slot in slots
    )


def test_offer_selection_spreads_slots_then_fills_earliest() -> None:
    config = _config()
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    slots = generate_slots(config, "initial_consultation", [], now)
    selected = select_offered_slots(slots, 3)
    assert [slot.start.astimezone(UTC).hour for slot in selected] == [8, 10, 12]
    selected = select_offered_slots(slots[:3], 3)
    assert [slot.start.astimezone(UTC).hour for slot in selected] == [8, 8, 9]


@pytest.mark.parametrize(
    ("slot_id", "reason"),
    [
        ("broken", "invalid"),
        ("unknown@20261019T0900Z", "unknown_type"),
        ("initial_consultation@20261019T1700Z", "outside_hours"),
        ("initial_consultation@20261019T0900Z", "too_soon"),
    ],
)
def test_slot_validation_reasons(slot_id: str, reason: str) -> None:
    config = _config(min_notice_hours=3)
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    with pytest.raises(SlotUnavailable) as error:
        validate_slot(config, slot_id, [], now)
    assert error.value.reason == reason


def test_slot_validation_checks_horizon_and_busy_blocks() -> None:
    config = _config(horizon_days=1)
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    with pytest.raises(SlotUnavailable, match="beyond_horizon"):
        validate_slot(config, "initial_consultation@20261021T0900Z", [], now)
    with pytest.raises(SlotUnavailable, match="busy"):
        validate_slot(
            config,
            "initial_consultation@20261019T0900Z",
            [
                BusyBlock(
                    start=datetime(2026, 10, 19, 9, tzinfo=UTC),
                    end=datetime(2026, 10, 19, 10, tzinfo=UTC),
                )
            ],
            now,
        )
    assert alternatives_for(config, "initial_consultation", [], now, now)


@pytest.mark.parametrize(
    ("phrase", "expected_day", "expected_start_hour"),
    [
        ("tomorrow morning", date(2026, 10, 20), 0),
        ("next Monday after 3", date(2026, 10, 26), 15),
        ("the 20th", date(2026, 10, 20), 0),
        ("20 October", date(2026, 10, 20), 0),
        ("October 20th", date(2026, 10, 20), 0),
        ("20/10", date(2026, 10, 20), 0),
        ("at 2", date(2026, 10, 19), 13),
        ("before 11am", date(2026, 10, 19), 9),
    ],
)
def test_preference_parser(phrase: str, expected_day: date, expected_start_hour: int) -> None:
    now = datetime(2026, 10, 19, 8, tzinfo=UTC)
    windows = parse_preference(phrase, now, "Europe/London", 14)
    assert windows
    assert windows[0].start.date() == expected_day
    assert windows[0].start.hour == expected_start_hour


@pytest.mark.parametrize(
    "phrase",
    [
        "",
        "any time",
        "anytime",
        "whenever",
        "asap",
        "as soon as possible",
        "earliest",
        "soonest",
        "first available",
    ],
)
def test_preference_parser_unconstrained_phrases(phrase: str) -> None:
    assert (
        parse_preference(phrase, datetime(2026, 10, 19, 8, tzinfo=UTC), "Europe/London", 14) is None
    )


def test_slot_formatting_is_localized_and_speech_friendly() -> None:
    config = _config()
    slot = Slot(
        appointment_type=config.booking.appointment_types[0],
        start=datetime(2026, 11, 20, 14, 30, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 30, tzinfo=UTC),
    )
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert (
        format_slot_speech(slot, "Europe/London", now) == "Friday the 20th of November at 2:30 pm"
    )
    assert format_slot_text(slot, "Europe/London") == "Fri 20 Nov, 2:30pm"


def test_generated_business_context_includes_appointments_only_when_enabled() -> None:
    from inbound_core.prompts import render_business_context

    config = _config()
    assert "APPOINTMENTS" not in render_business_context(config)
    enabled = render_business_context(config, booking_enabled=True)
    assert "Only offer these appointment types" in enabled
    assert "Initial consultation" in enabled
