from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from inbound_core.booking import BusyBlock


class CalendarError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    html_link: str | None = None


class CalendarProvider(Protocol):
    async def free_busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[BusyBlock]: ...

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str,
        attendee_email: str | None = None,
        *,
        location: str | None = None,
        send_updates: str = "all",
    ) -> CalendarEvent: ...

    async def cancel_event(self, calendar_id: str, event_id: str) -> None: ...


_FAKE_CALENDAR_LOCK = asyncio.Lock()


class FakeCalendarProvider:
    def __init__(self, path: str | Path, delay_ms: int = 0) -> None:
        self.path = Path(path)
        self.delay_ms = delay_ms

    async def free_busy(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyBlock]:
        await self._delay()
        async with _FAKE_CALENDAR_LOCK:
            data = self._read()
        calendar = data.get("calendars", {}).get(calendar_id, {})
        blocks = [BusyBlock.model_validate(item) for item in calendar.get("busy", [])]
        blocks.extend(
            BusyBlock(start=item["start"], end=item["end"])
            for item in calendar.get("events", [])
            if item.get("status", "confirmed") == "confirmed"
            and _overlaps(start, end, _parse_datetime(item["start"]), _parse_datetime(item["end"]))
        )
        return [block for block in blocks if _overlaps(start, end, block.start, block.end)]

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str,
        attendee_email: str | None = None,
        *,
        location: str | None = None,
        send_updates: str = "all",
    ) -> CalendarEvent:
        await self._delay()
        async with _FAKE_CALENDAR_LOCK:
            data = self._read()
            calendars = data.setdefault("calendars", {})
            calendar = calendars.setdefault(calendar_id, {"busy": [], "events": []})
            event = {
                "id": f"fake-{os.urandom(8).hex()}",
                "start": _iso(start),
                "end": _iso(end),
                "summary": summary,
                "description": description,
                "attendee_email": attendee_email,
                "location": location,
                "status": "confirmed",
            }
            calendar.setdefault("events", []).append(event)
            self._write(data)
        return CalendarEvent(
            event["id"], f"https://calendar.google.com/calendar/event?eid={event['id']}"
        )

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        await self._delay()
        async with _FAKE_CALENDAR_LOCK:
            data = self._read()
            events = data.get("calendars", {}).get(calendar_id, {}).get("events", [])
            for event in events:
                if event.get("id") == event_id:
                    event["status"] = "cancelled"
            self._write(data)

    async def _delay(self) -> None:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"calendars": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class GoogleCalendarProvider:
    def __init__(
        self,
        access_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://www.googleapis.com/calendar/v3",
        timeout: float = 5.0,
    ) -> None:
        self.access_token = access_token
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def free_busy(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyBlock]:
        async with self._client() as client:
            response = await client.post(
                "/freeBusy",
                json={
                    "timeMin": _iso(start),
                    "timeMax": _iso(end),
                    "timeZone": "UTC",
                    "items": [{"id": calendar_id}],
                },
            )
        data = self._json(response)
        calendar = data.get("calendars", {}).get(calendar_id, {})
        if calendar.get("errors"):
            raise CalendarError("Google Calendar free/busy returned calendar errors.")
        return [
            BusyBlock(start=item["start"], end=item["end"]) for item in calendar.get("busy", [])
        ]

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str,
        attendee_email: str | None = None,
        *,
        location: str | None = None,
        send_updates: str = "all",
    ) -> CalendarEvent:
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": _iso(start)},
            "end": {"dateTime": _iso(end)},
        }
        if location:
            body["location"] = location
        if attendee_email:
            body["attendees"] = [{"email": attendee_email}]
        async with self._client() as client:
            response = await client.post(
                f"/calendars/{quote(calendar_id, safe='')}/events",
                params={"sendUpdates": send_updates},
                json=body,
            )
        data = self._json(response)
        return CalendarEvent(data["id"], data.get("htmlLink"))

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        async with self._client() as client:
            response = await client.delete(
                f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
                params={"sendUpdates": "none"},
            )
        self._raise(response)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.timeout,
            transport=self.transport,
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise CalendarError(
                f"Google Calendar request failed ({response.status_code})."
            ) from error

    def _json(self, response: httpx.Response) -> dict:
        self._raise(response)
        try:
            return response.json()
        except ValueError as error:
            raise CalendarError("Google Calendar returned invalid JSON.") from error


async def calendar_provider_for(connection) -> CalendarProvider | None:
    from inbound_api.settings import get_settings

    settings = get_settings()
    if settings.calendar_mode == "off":
        return None
    if settings.calendar_mode == "fake":
        return FakeCalendarProvider(settings.calendar_fake_path, settings.calendar_fake_delay_ms)
    if connection is None:
        return None
    from inbound_api.integrations.google_auth import google_access_token

    return GoogleCalendarProvider(await google_access_token(connection))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str | datetime) -> datetime:
    return (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end
