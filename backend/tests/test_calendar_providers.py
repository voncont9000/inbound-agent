import json
from datetime import UTC, datetime

import httpx
import pytest
from inbound_core.booking import BusyBlock
from pydantic import ValidationError

from inbound_api.integrations.calendar import (
    CalendarError,
    FakeCalendarProvider,
    GoogleCalendarProvider,
)
from inbound_api.settings import Settings


@pytest.mark.asyncio
async def test_fake_calendar_provider_busy_event_and_cancel_round_trip(tmp_path) -> None:
    provider = FakeCalendarProvider(tmp_path / "calendar.json")
    start = datetime(2026, 10, 20, 10, tzinfo=UTC)
    end = datetime(2026, 10, 20, 10, 30, tzinfo=UTC)

    event = await provider.create_event(
        "primary",
        start,
        end,
        "Consultation",
        "Appointment details",
        "alex@example.com",
        location="Online",
    )
    busy = await provider.free_busy("primary", start, end)
    await provider.cancel_event("primary", event.event_id)
    cancelled = await provider.free_busy("primary", start, end)

    assert provider.name == "fake"
    assert busy == [BusyBlock(start=start, end=end)]
    assert cancelled == []


@pytest.mark.asyncio
async def test_google_calendar_provider_requests_and_response_parsing() -> None:
    calendar_id = "team@group.calendar.google.com"
    start = datetime(2026, 10, 20, 10, tzinfo=UTC)
    end = datetime(2026, 10, 20, 11, tzinfo=UTC)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/freeBusy"):
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        calendar_id: {
                            "busy": [
                                {
                                    "start": "2026-10-20T10:00:00Z",
                                    "end": "2026-10-20T10:30:00Z",
                                }
                            ]
                        }
                    }
                },
            )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "event-1", "htmlLink": "https://calendar.test/event-1"},
            )
        return httpx.Response(204)

    provider = GoogleCalendarProvider(
        "calendar-access-token",
        transport=httpx.MockTransport(handle),
    )
    busy = await provider.free_busy(calendar_id, start, end)
    event = await provider.create_event(
        calendar_id,
        start,
        end,
        "Consultation",
        "Appointment details",
        "alex@example.com",
        location="Online",
        send_updates="externalOnly",
    )
    await provider.cancel_event(calendar_id, event.event_id)

    free_busy_request, create_request, cancel_request = requests
    assert provider.name == "google"
    assert busy == [
        BusyBlock(
            start=datetime(2026, 10, 20, 10, tzinfo=UTC),
            end=datetime(2026, 10, 20, 10, 30, tzinfo=UTC),
        )
    ]
    assert free_busy_request.url.path.endswith("/freeBusy")
    assert json.loads(free_busy_request.content) == {
        "timeMin": "2026-10-20T10:00:00Z",
        "timeMax": "2026-10-20T11:00:00Z",
        "timeZone": "UTC",
        "items": [{"id": calendar_id}],
    }
    assert "/calendars/team%40group.calendar.google.com/events" in str(create_request.url)
    assert create_request.url.params["sendUpdates"] == "externalOnly"
    assert create_request.headers["Authorization"] == "Bearer calendar-access-token"
    assert json.loads(create_request.content) == {
        "summary": "Consultation",
        "description": "Appointment details",
        "start": {"dateTime": "2026-10-20T10:00:00Z"},
        "end": {"dateTime": "2026-10-20T11:00:00Z"},
        "location": "Online",
        "attendees": [{"email": "alex@example.com"}],
    }
    assert event.event_id == "event-1"
    assert cancel_request.method == "DELETE"
    assert cancel_request.url.params["sendUpdates"] == "none"
    assert "/events/event-1" in str(cancel_request.url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (
            200,
            {"calendars": {"primary": {"errors": [{"reason": "notFound"}], "busy": []}}},
        ),
        (503, {"error": {"message": "Unavailable"}}),
    ],
)
async def test_google_calendar_provider_errors_become_calendar_errors(status, body) -> None:
    provider = GoogleCalendarProvider(
        "calendar-access-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, json=body)),
    )

    with pytest.raises(CalendarError):
        await provider.free_busy(
            "primary",
            datetime(2026, 10, 20, 10, tzinfo=UTC),
            datetime(2026, 10, 20, 11, tzinfo=UTC),
        )


def _production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "voice_service_token": "configured-service-token",
        "whatsapp_mode": "off",
        "gmail_mode": "off",
        "calendar_mode": "off",
        "email_sender": "off",
        "sheets_writer": "off",
        "webhook_sender": "off",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_refuses_fake_calendar_mode() -> None:
    with pytest.raises(ValidationError, match="Calendar production"):
        _production_settings(calendar_mode="fake")


def test_google_calendar_mode_requires_google_gmail_mode() -> None:
    with pytest.raises(ValidationError, match="GMAIL_MODE=google"):
        Settings(calendar_mode="google", gmail_mode="fake")
    with pytest.raises(ValidationError, match="GMAIL_MODE=google"):
        _production_settings(calendar_mode="google")
