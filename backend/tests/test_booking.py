import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from inbound_core.booking import generate_slots
from inbound_core.config import AssistantConfig
from sqlalchemy import func, select

from inbound_api.db import tenant_session
from inbound_api.integrations.calendar import FakeCalendarProvider
from inbound_api.models import (
    CalendarBooking,
    Commitment,
    Conversation,
    Message,
    MessageRole,
)
from inbound_api.services import booking as booking_service
from inbound_api.services.assistant_context import load_assistant_route
from inbound_api.services.booking import BookingContext, book_slot

BOOKING_NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def _config(*, booking: bool = True, actions: bool = True) -> AssistantConfig:
    data = {
        "business_name": "Booking Test",
        "greeting": "Hello",
        "office_hours": {
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "09:00",
            "closes": "17:00",
            "timezone": "Europe/London",
        },
        "price_list": [],
        "policies": [],
        "contacts": {},
        "services": {"general": {"label": "General", "fields": []}},
        "urgency_rules": [],
        "live_actions": ["check_availability", "book_appointment"] if actions else [],
    }
    if booking:
        data["booking"] = {
            "calendar_id": "primary",
            "appointment_types": [
                {
                    "key": "consultation",
                    "label": "Consultation",
                    "duration_minutes": 30,
                    "when_to_use": "Initial appointment",
                    "location_or_link": "Online",
                }
            ],
            "min_notice_hours": 0,
            "horizon_days": 14,
            "buffer_minutes": 0,
            "max_slots_offered": 3,
            "slot_step_minutes": 30,
        }
    return AssistantConfig.model_validate(data)


def _save_config(graph_env, config: AssistantConfig, assistant_id=None) -> None:
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config.model_dump(mode="json")), assistant_id or graph_env["assistant_id"]),
    )


def _insert_connection(
    graph_env, kind: str, external_id: str, settings: dict | None = None, assistant_id=None
):
    connection_id = uuid4()
    graph_env["execute"](
        "INSERT INTO channel_connections "
        "(id, tenant_id, assistant_id, kind, external_id, status, settings) "
        "VALUES (%s, %s, %s, %s, %s, 'active', %s::jsonb)",
        (
            connection_id,
            graph_env["tenant_id"],
            assistant_id or graph_env["assistant_id"],
            kind,
            external_id,
            json.dumps(settings or {}),
        ),
    )
    return connection_id


def _insert_conversation(graph_env, *, assistant_id=None, channel="phone", metrics=None):
    conversation_id = uuid4()
    graph_env["execute"](
        "INSERT INTO conversations "
        "(id, tenant_id, assistant_id, channel, external_ref, status, started_at, metrics) "
        "VALUES (%s, %s, %s, %s, %s, 'active', %s, %s::jsonb)",
        (
            conversation_id,
            graph_env["tenant_id"],
            assistant_id or graph_env["assistant_id"],
            channel,
            str(uuid4()),
            BOOKING_NOW,
            json.dumps(metrics or {}),
        ),
    )
    return conversation_id


def _context(config: AssistantConfig, provider) -> BookingContext:
    assert config.booking is not None
    return BookingContext(
        config=config,
        booking=config.booking,
        connection=None,
        provider=provider,
        timezone=config.office_hours.timezone,
    )


def _slot(config: AssistantConfig):
    slots = generate_slots(config, "consultation", [], BOOKING_NOW)
    assert slots
    return slots[0]


def _enable_internal_fake_calendar(graph_env, monkeypatch, tmp_path, config):
    from inbound_api import settings as settings_module
    from inbound_api.integrations import google_auth

    calendar_path = tmp_path / "calendar.json"
    _save_config(graph_env, config)
    _insert_connection(
        graph_env, "gmail", "booking@example.test", {"google_features": ["calendar"]}
    )
    _insert_connection(graph_env, "phone", "+442079460000")
    monkeypatch.setattr(
        booking_service, "get_settings", lambda: SimpleNamespace(calendar_mode="fake")
    )
    monkeypatch.setattr(google_auth, "get_settings", lambda: SimpleNamespace(gmail_mode="fake"))
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            calendar_mode="fake",
            calendar_fake_path=calendar_path,
            calendar_fake_delay_ms=0,
        ),
    )
    return calendar_path


@pytest.mark.asyncio
async def test_internal_availability_and_book_require_service_token(api_client):
    requests = [
        (
            "/internal/voice/availability",
            {"channel": "phone", "to_number": "+442079460000"},
        ),
        (
            "/internal/voice/book",
            {
                "conversation_id": str(uuid4()),
                "external_call_id": "booking-auth",
                "slot_id": "consultation@20261019T0800Z",
                "name": "Alex",
            },
        ),
    ]
    for path, payload in requests:
        missing = await api_client.post(path, json=payload)
        assert missing.status_code in {401, 422}
        wrong = await api_client.post(
            path,
            json=payload,
            headers={"X-Service-Token": "wrong-token"},
        )
        assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_booking_availability_gates_and_route_flag(graph_env, monkeypatch, tmp_path):
    from inbound_core.booking import AvailabilityRequest

    from inbound_api import settings as settings_module
    from inbound_api.integrations import google_auth
    from inbound_api.routers.internal_voice import availability

    config = _config()
    _save_config(graph_env, config)
    _insert_connection(
        graph_env, "gmail", "booking@example.test", {"google_features": ["calendar"]}
    )
    _insert_connection(graph_env, "phone", "+442079460000")
    monkeypatch.setattr(
        booking_service,
        "get_settings",
        lambda: SimpleNamespace(calendar_mode="fake"),
    )
    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            calendar_mode="fake",
            calendar_fake_path=tmp_path / "calendar.json",
            calendar_fake_delay_ms=0,
        ),
    )

    async def assert_unavailable(test_config, *, feature=True, mode="fake"):
        _save_config(graph_env, test_config)
        graph_env["execute"](
            "UPDATE channel_connections SET settings = %s::jsonb WHERE assistant_id = %s "
            "AND kind = 'gmail'",
            (
                json.dumps({"google_features": ["calendar"]} if feature else {}),
                graph_env["assistant_id"],
            ),
        )
        monkeypatch.setattr(
            booking_service,
            "get_settings",
            lambda: SimpleNamespace(calendar_mode=mode),
        )
        route = await load_assistant_route(
            graph_env["tenant_id"],
            graph_env["assistant_id"],
            caller_phone=None,
            include_context_card=False,
        )
        response = await availability(
            AvailabilityRequest(channel="phone", to_number="+442079460000")
        )
        assert route.booking_available is False
        assert response.available is False

    await assert_unavailable(_config(booking=False))
    await assert_unavailable(_config(actions=False))
    await assert_unavailable(_config(), feature=False)
    await assert_unavailable(_config(), mode="off")

    _save_config(graph_env, config)
    graph_env["execute"](
        "UPDATE channel_connections SET settings = %s::jsonb WHERE assistant_id = %s "
        "AND kind = 'gmail'",
        (json.dumps({"google_features": ["calendar"]}), graph_env["assistant_id"]),
    )
    monkeypatch.setattr(
        booking_service,
        "get_settings",
        lambda: SimpleNamespace(calendar_mode="fake"),
    )
    route = await load_assistant_route(
        graph_env["tenant_id"],
        graph_env["assistant_id"],
        caller_phone=None,
        include_context_card=False,
    )
    response = await availability(AvailabilityRequest(channel="phone", to_number="+442079460000"))
    assert route.booking_available is True
    assert response.available is True


@pytest.mark.asyncio
async def test_internal_availability_slot_can_be_booked_when_unchanged(
    graph_env, monkeypatch, tmp_path
):
    from inbound_core.booking import AvailabilityRequest

    from inbound_api.routers.internal_voice import availability

    config = _config()
    calendar_path = _enable_internal_fake_calendar(graph_env, monkeypatch, tmp_path, config)
    response = await availability(AvailabilityRequest(channel="phone", to_number="+442079460000"))
    assert response.available is True
    assert response.fetched_at is not None
    slots = generate_slots(config, "consultation", response.busy, response.fetched_at)
    assert slots
    conversation_id = _insert_conversation(graph_env)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, conversation_id)
        result = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, FakeCalendarProvider(calendar_path)),
            slot_id=slots[0].slot_id,
            name="Alex",
            email=None,
            phone=None,
            now=response.fetched_at,
        )

    assert result.status == "booked"


@pytest.mark.asyncio
async def test_cancelled_calendar_event_remains_busy_from_booking_row(
    graph_env, monkeypatch, tmp_path
):
    from inbound_core.booking import AvailabilityRequest

    from inbound_api.routers.internal_voice import availability

    config = _config()
    calendar_path = _enable_internal_fake_calendar(graph_env, monkeypatch, tmp_path, config)
    provider = FakeCalendarProvider(calendar_path)
    first_availability = await availability(
        AvailabilityRequest(channel="phone", to_number="+442079460000")
    )
    assert first_availability.fetched_at is not None
    first_slots = generate_slots(
        config, "consultation", first_availability.busy, first_availability.fetched_at
    )
    assert first_slots
    taken_slot = first_slots[0]
    first_conversation_id = _insert_conversation(graph_env)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, first_conversation_id)
        first_booking = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=taken_slot.slot_id,
            name="Alex",
            email=None,
            phone=None,
            now=first_availability.fetched_at,
        )
    assert first_booking.status == "booked"
    assert first_booking.booking is not None
    await provider.cancel_event("primary", first_booking.booking.event_id)

    second_availability = await availability(
        AvailabilityRequest(channel="phone", to_number="+442079460000")
    )
    assert second_availability.available is True
    assert second_availability.fetched_at is not None
    assert any(
        block.start < taken_slot.end and taken_slot.start < block.end
        for block in second_availability.busy
    )
    second_slots = generate_slots(
        config, "consultation", second_availability.busy, second_availability.fetched_at
    )
    assert taken_slot.slot_id not in {slot.slot_id for slot in second_slots}
    assert second_slots
    second_conversation_id = _insert_conversation(graph_env)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, second_conversation_id)
        second_booking = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=second_slots[0].slot_id,
            name="Jordan",
            email=None,
            phone=None,
            now=second_availability.fetched_at,
        )

    assert second_booking.status == "booked"


@pytest.mark.asyncio
async def test_book_slot_persists_booking_message_commitment_and_metrics(graph_env, tmp_path):
    config = _config()
    _save_config(graph_env, config)
    conversation_id = _insert_conversation(graph_env)
    provider = FakeCalendarProvider(tmp_path / "calendar.json")
    slot = _slot(config)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, conversation_id)
        response = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=slot.slot_id,
            name="Alex Example",
            email="alex@example.test",
            phone="+447700900123",
            now=BOOKING_NOW,
        )
        assert response.status == "booked"
        assert response.booking is not None
        booking = await session.scalar(
            select(CalendarBooking).where(CalendarBooking.conversation_id == conversation_id)
        )
        message = await session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.SYSTEM,
            )
        )
        assert message is not None
        commitment = await session.scalar(
            select(Commitment).where(Commitment.source_message_id == message.id)
        )
        assert booking.provider == "fake"
        assert booking.event_id == response.booking.event_id
        assert message.text.startswith("Appointment booked: Consultation,")
        assert commitment.kind == "appointment"
        assert commitment.due_at == slot.start
        assert commitment.source_message_id == message.id
        assert conversation.metrics["booking"]["booking_id"] == str(booking.id)
        events = json.loads((tmp_path / "calendar.json").read_text())["calendars"]["primary"][
            "events"
        ]
        assert len(events) == 1

        from inbound_api.services.conversation_results import persist_voice_report

        await persist_voice_report(
            session,
            graph_env["tenant_id"],
            conversation,
            SimpleNamespace(
                transcript=[],
                live_intake={"fields": {}},
                merged_intake={"fields": {}},
                post_call_intake=None,
                tasks=[],
                knowledge_gaps=[],
                ended_at=BOOKING_NOW + timedelta(minutes=10),
                end_reason="caller_finished",
                summary="Booked an appointment.",
                metrics={"call": "complete"},
                estimated_cost=None,
            ),
        )
        assert conversation.metrics["booking"]["booking_id"] == str(booking.id)
        assert conversation.metrics["call"] == "complete"


@pytest.mark.asyncio
async def test_book_slot_idempotent_retry_uses_booking_row_without_metrics(graph_env, tmp_path):
    config = _config()
    _save_config(graph_env, config)
    conversation_id = _insert_conversation(graph_env)
    provider = FakeCalendarProvider(tmp_path / "calendar.json")
    slot = _slot(config)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, conversation_id)
        first = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=slot.slot_id,
            name="Alex",
            email="alex@example.test",
            phone=None,
            now=BOOKING_NOW,
        )
        conversation.metrics = {}
        second = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=slot.slot_id,
            name="Changed name",
            email=None,
            phone=None,
            now=BOOKING_NOW,
        )
        assert first.status == second.status == "booked"
        assert first.booking is not None and second.booking is not None
        assert second.booking.booking_id == first.booking.booking_id
        assert second.booking.label == "Consultation"
        assert second.booking.text
        count = await session.scalar(
            select(func.count())
            .select_from(CalendarBooking)
            .where(CalendarBooking.conversation_id == conversation_id)
        )
        assert count == 1

    events = json.loads((tmp_path / "calendar.json").read_text())["calendars"]["primary"]["events"]
    assert len(events) == 1


@pytest.mark.asyncio
async def test_busy_slot_returns_alternatives_without_creating_event(graph_env, tmp_path):
    config = _config()
    _save_config(graph_env, config)
    conversation_id = _insert_conversation(graph_env)
    slot = _slot(config)
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "calendars": {
                    "primary": {
                        "busy": [{"start": slot.start.isoformat(), "end": slot.end.isoformat()}],
                        "events": [],
                    }
                }
            }
        )
    )

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, conversation_id)
        response = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, FakeCalendarProvider(path)),
            slot_id=slot.slot_id,
            name="Alex",
            email=None,
            phone=None,
            now=BOOKING_NOW,
        )

    assert response.status == "unavailable"
    assert response.alternatives
    assert json.loads(path.read_text())["calendars"]["primary"]["events"] == []


@pytest.mark.asyncio
async def test_double_book_race_creates_exactly_one_confirmed_event(graph_env, tmp_path):
    config = _config()
    _save_config(graph_env, config)
    conversation_ids = [_insert_conversation(graph_env) for _ in range(2)]
    provider = FakeCalendarProvider(tmp_path / "calendar.json", delay_ms=300)
    slot = _slot(config)

    async def book(conversation_id):
        async with tenant_session(graph_env["tenant_id"]) as session:
            conversation = await session.get(Conversation, conversation_id)
            return await book_slot(
                session,
                graph_env["tenant_id"],
                conversation,
                _context(config, provider),
                slot_id=slot.slot_id,
                name="Alex",
                email=None,
                phone=None,
                now=BOOKING_NOW,
            )

    responses = await asyncio.gather(*(book(item) for item in conversation_ids))
    assert sorted(response.status for response in responses) == ["booked", "unavailable"]
    events = json.loads((tmp_path / "calendar.json").read_text())["calendars"]["primary"]["events"]
    assert [event["status"] for event in events] == ["confirmed"]


@pytest.mark.asyncio
async def test_booking_persistence_failure_rolls_back_and_cancels_event(
    graph_env, monkeypatch, tmp_path
):
    from inbound_api.models import Commitment as CommitmentModel

    config = _config()
    _save_config(graph_env, config)
    conversation_id = _insert_conversation(graph_env)
    path = tmp_path / "calendar.json"
    provider = FakeCalendarProvider(path)
    slot = _slot(config)

    async with tenant_session(graph_env["tenant_id"]) as session:
        conversation = await session.get(Conversation, conversation_id)
        original_add = session.add

        def fail_commitment(instance, **kwargs):
            if isinstance(instance, CommitmentModel):
                raise RuntimeError("forced commitment failure")
            return original_add(instance, **kwargs)

        monkeypatch.setattr(session, "add", fail_commitment)
        response = await book_slot(
            session,
            graph_env["tenant_id"],
            conversation,
            _context(config, provider),
            slot_id=slot.slot_id,
            name="Alex",
            email="alex@example.test",
            phone=None,
            now=BOOKING_NOW,
        )
        assert response.status == "error"
        assert conversation.metrics == {}
        assert conversation.person_id is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CalendarBooking)
                .where(CalendarBooking.conversation_id == conversation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == MessageRole.SYSTEM,
                )
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(Commitment)) == 0

    events = json.loads(path.read_text())["calendars"]["primary"]["events"]
    assert len(events) == 1
    assert events[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_assistant_scoped_locks_allow_same_primary_slot_for_two_assistants(
    graph_env, tmp_path
):
    config = _config()
    _save_config(graph_env, config)
    assistant_ids = [graph_env["assistant_id"], uuid4()]
    graph_env["execute"](
        "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, %s::jsonb)",
        (
            assistant_ids[1],
            graph_env["tenant_id"],
            "Second booking assistant",
            json.dumps(config.model_dump(mode="json")),
        ),
    )
    conversation_ids = [
        _insert_conversation(graph_env, assistant_id=assistant_id) for assistant_id in assistant_ids
    ]
    slot = _slot(config)
    both_checking = asyncio.Event()
    entry_count = 0

    class BarrierProvider(FakeCalendarProvider):
        def __init__(self, path):
            super().__init__(path)

        async def free_busy(self, calendar_id, start, end):
            nonlocal entry_count
            entry_count += 1
            if entry_count == 2:
                both_checking.set()
            await asyncio.wait_for(both_checking.wait(), timeout=2)
            return await super().free_busy(calendar_id, start, end)

    providers = [
        BarrierProvider(tmp_path / "assistant-one.json"),
        BarrierProvider(tmp_path / "assistant-two.json"),
    ]

    async def book(assistant_id, conversation_id, provider):
        async with tenant_session(graph_env["tenant_id"]) as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation.assistant_id == assistant_id
            return await book_slot(
                session,
                graph_env["tenant_id"],
                conversation,
                _context(config, provider),
                slot_id=slot.slot_id,
                name="Alex",
                email=None,
                phone=None,
                now=BOOKING_NOW,
            )

    responses = await asyncio.wait_for(
        asyncio.gather(
            *(
                book(assistant_id, conversation_id, provider)
                for assistant_id, conversation_id, provider in zip(
                    assistant_ids, conversation_ids, providers, strict=True
                )
            )
        ),
        timeout=5,
    )
    assert [response.status for response in responses] == ["booked", "booked"]
