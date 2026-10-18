from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from httpx import ASGITransport, AsyncClient
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("GMAIL_MODE", "fake")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def summary(values: list[float]) -> tuple[float, float, float]:
    return (
        statistics.median(values),
        percentile(values, 0.95),
        max(values),
    )


async def benchmark() -> None:
    from inbound_core.booking import generate_slots
    from inbound_core.call_report import RouteResponse
    from inbound_core.config import load_assistant_config

    from inbound_api.integrations import calendar
    from inbound_api.integrations.calendar import CalendarEvent
    from inbound_api.main import app
    from inbound_api.routers import internal_voice
    from inbound_api.settings import get_settings

    settings = get_settings()
    settings.calendar_fake_delay_ms = 300
    settings.gmail_mode = "fake"
    settings.voice_service_token = "booking-latency-token"
    config = load_assistant_config(ROOT / "voice/config/assistants/booking_firm.yaml")
    config.booking.horizon_days = 60
    buffer = timedelta(minutes=config.booking.buffer_minutes)
    now = datetime.now(UTC)
    candidates = generate_slots(config, "initial_consultation", [], now)
    slots = []
    for candidate in candidates:
        if all(
            candidate.end + buffer <= selected.start or candidate.start >= selected.end + buffer
            for selected in slots
        ):
            slots.append(candidate)
            if len(slots) == 60:
                break
    if len(slots) < 60:
        raise RuntimeError("Booking config did not produce 60 non-overlapping benchmark slots.")

    tenant_id, assistant_id = uuid4(), uuid4()
    phone = "+15555550123"
    owner_url = settings.migration_database_url.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(owner_url) as connection:
            connection.execute(
                "INSERT INTO tenants (id, name) VALUES (%s, %s)",
                (tenant_id, "Booking benchmark"),
            )
            connection.execute(
                "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, %s)",
                (
                    assistant_id,
                    tenant_id,
                    "Booking benchmark",
                    Jsonb(config.model_dump(mode="json")),
                ),
            )
            connection.execute(
                "INSERT INTO channel_connections "
                "(tenant_id, assistant_id, kind, external_id, status, settings) "
                "VALUES (%s, %s, 'gmail', %s, 'active', %s)",
                (
                    tenant_id,
                    assistant_id,
                    "booking-benchmark@example.test",
                    Jsonb({"google_features": ["calendar"]}),
                ),
            )
            conversations = []
            for index in range(60):
                conversation_id, external_ref = uuid4(), f"booking-benchmark-{index}"
                connection.execute(
                    "INSERT INTO conversations "
                    "(id, tenant_id, assistant_id, channel, external_ref, status, started_at) "
                    "VALUES (%s, %s, %s, 'phone', %s, 'active', %s)",
                    (conversation_id, tenant_id, assistant_id, external_ref, now),
                )
                conversations.append((conversation_id, external_ref))

        async def route_call(_to_number: str, _from_number: str | None = None):
            return RouteResponse(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                config=config,
                booking_available=True,
            )

        original_route = internal_voice.route_call
        original_provider_factory = calendar.calendar_provider_for
        internal_voice.route_call = route_call
        provider_results: dict[str, dict[str, list[float]]] = {}

        class GoogleCalendarProvider:
            def __init__(self) -> None:
                self.events: list[tuple[datetime, datetime]] = []

            async def free_busy(self, _calendar_id, _start, _end):
                await asyncio.sleep(0.3)
                return []

            async def create_event(self, _calendar_id, start, end, *_args, **_kwargs):
                await asyncio.sleep(0.3)
                self.events.append((start, end))
                return CalendarEvent(f"mock-google-{len(self.events)}")

            async def cancel_event(self, _calendar_id, _event_id):
                await asyncio.sleep(0.3)

        google_provider = GoogleCalendarProvider()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://booking-benchmark"
        ) as client:
            for provider_name in ("fake", "google"):
                settings.calendar_mode = provider_name
                if provider_name == "google":

                    async def use_google_provider(_connection):
                        return google_provider

                    calendar.calendar_provider_for = use_google_provider
                else:
                    with tempfile.TemporaryDirectory(prefix="booking-calendar-") as directory:
                        settings.calendar_fake_path = Path(directory) / "calendar.json"
                        calendar.calendar_provider_for = original_provider_factory
                        provider_results[provider_name] = await measure_provider(
                            client,
                            settings.voice_service_token,
                            phone,
                            slots[:30],
                            conversations[:30],
                        )
                        continue
                provider_results[provider_name] = await measure_provider(
                    client,
                    settings.voice_service_token,
                    phone,
                    slots[30:],
                    conversations[30:],
                )
        internal_voice.route_call = original_route
        calendar.calendar_provider_for = original_provider_factory

        local_result = subprocess.run(
            [
                "uv",
                "run",
                "--directory",
                str(ROOT / "voice"),
                "python",
                "-c",
                _local_booking_benchmark_source(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        local_values = json.loads(local_result.stdout.strip())

        print("| Provider | Endpoint | p50 ms | p95 ms | max ms |")
        print("|---|---|---:|---:|---:|")
        for provider_name, endpoints in provider_results.items():
            for endpoint, values in endpoints.items():
                p50, p95, maximum = summary(values)
                print(f"| {provider_name} | {endpoint} | {p50:.1f} | {p95:.1f} | {maximum:.1f} |")
        local_p50, local_p95, _ = summary(local_values)
        print(f"\nLocal cached CallBooking: p50 {local_p50:.2f} ms; p95 {local_p95:.2f} ms.")
    finally:
        with psycopg.connect(owner_url) as connection:
            connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


async def measure_provider(client, token, phone, slots, conversations):
    timings = {"availability": [], "book": []}
    headers = {"X-Service-Token": token}
    for index in range(30):
        started = time.perf_counter()
        response = await client.post(
            "/internal/voice/availability",
            headers=headers,
            json={"channel": "phone", "to_number": phone},
        )
        response.raise_for_status()
        timings["availability"].append((time.perf_counter() - started) * 1000)

        conversation_id, external_call_id = conversations[index]
        started = time.perf_counter()
        response = await client.post(
            "/internal/voice/book",
            headers=headers,
            json={
                "conversation_id": str(conversation_id),
                "external_call_id": external_call_id,
                "slot_id": slots[index].slot_id,
                "name": "Booking Benchmark",
            },
        )
        response.raise_for_status()
        if response.json()["status"] != "booked":
            raise RuntimeError(f"Booking benchmark failed: {response.text}")
        timings["book"].append((time.perf_counter() - started) * 1000)
    return timings


def _local_booking_benchmark_source() -> str:
    return f"""
import asyncio, json, statistics, time
from datetime import UTC, datetime
from inbound_core.booking import AvailabilityResponse
from inbound_core.call_report import CallStart
from inbound_core.config import load_assistant_config
from inbound_voice.booking import CallBooking

config = load_assistant_config({str(ROOT / "voice/config/assistants/booking_firm.yaml")!r})
now = datetime.now(UTC)
availability = AvailabilityResponse(
    available=True,
    timezone=config.office_hours.timezone,
    busy=[],
    fetched_at=now,
)
class CachedApi:
    async def availability(self, _request, timeout=4.0):
        return availability
call_start = CallStart(
    to_number="+15555550123",
    external_call_id="local-cache",
    channel="phone",
    started_at=now,
)
booking = CallBooking(config, CachedApi(), call_start, None, lambda: None, None, None)
async def run():
    values = []
    for _ in range(30):
        started = time.perf_counter()
        await booking.check_availability("initial_consultation")
        values.append((time.perf_counter() - started) * 1000)
    print(json.dumps(values))
asyncio.run(run())
"""


if __name__ == "__main__":
    asyncio.run(benchmark())
