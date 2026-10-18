import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from inbound_api.models import Conversation
from inbound_api.services.costs import add_conversation_cost
from inbound_api.services.dashboard import (
    MINUTES_SAVED_PER_CONVERSATION,
    PASSED_TO_STAFF_CREDIT,
    DashboardRangeError,
    resolve_range,
)

RANGE = {"range": "custom", "start": "2026-03-01", "end": "2026-03-07", "timezone": "Europe/London"}


class Seeder:
    def __init__(self, owner_url: str, tenant_id: str) -> None:
        self.owner_url = owner_url
        self.tenant_id = tenant_id

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        with psycopg.connect(self.owner_url) as connection:
            connection.execute(query, params)

    def assistant(self, name: str = "Reception") -> UUID:
        assistant_id = uuid4()
        self.execute(
            "INSERT INTO assistants (id, tenant_id, name, config) VALUES (%s, %s, %s, '{}'::jsonb)",
            (assistant_id, self.tenant_id, name),
        )
        return assistant_id

    def conversation(
        self,
        assistant_id: UUID,
        channel: str,
        started_at: str,
        *,
        metrics: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
    ) -> UUID:
        conversation_id = uuid4()
        self.execute(
            "INSERT INTO conversations (id, tenant_id, assistant_id, channel, external_ref, "
            "status, started_at, metrics, estimated_cost) "
            "VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s::jsonb, %s::jsonb)",
            (
                conversation_id,
                self.tenant_id,
                assistant_id,
                channel,
                str(uuid4()),
                started_at,
                json.dumps(metrics or {}),
                json.dumps(cost) if cost is not None else None,
            ),
        )
        return conversation_id

    def task(self, conversation_id: UUID, kind: str) -> None:
        self.execute(
            "INSERT INTO tasks (tenant_id, conversation_id, kind, source) "
            "VALUES (%s, %s, %s, 'post_call')",
            (self.tenant_id, conversation_id, kind),
        )

    def message(self, conversation_id: UUID, role: str) -> None:
        self.execute(
            "INSERT INTO messages (tenant_id, conversation_id, role, text, ts) "
            "VALUES (%s, %s, %s, 'hello', now())",
            (self.tenant_id, conversation_id, role),
        )

    def booking(self, assistant_id: UUID, conversation_id: UUID, status: str = "booked") -> None:
        self.execute(
            "INSERT INTO calendar_bookings (tenant_id, assistant_id, conversation_id, calendar_id, "
            "provider, event_id, appointment_type, slot_id, start_at, end_at, attendee_name, "
            "status) VALUES (%s, %s, %s, 'primary', 'fake', %s, 'consultation', 'slot', "
            "'2026-03-10T10:00:00Z', '2026-03-10T10:30:00Z', 'Jamie', %s)",
            (self.tenant_id, assistant_id, conversation_id, str(uuid4()), status),
        )


def _latency(p50: float) -> dict[str, Any]:
    return {"turn_latency_summary": {"turn_count": 3, "p50_seconds": p50}}


def _cost(total: float, twilio: float = 0.0) -> dict[str, Any]:
    return {
        "currency": "USD",
        "components": {"stt": total - twilio, "twilio": twilio},
        "total": total,
        "unknown_components": [],
    }


@pytest.fixture
def seeded(tenant_pair, owner_database_url) -> dict[str, Any]:
    first = Seeder(owner_database_url, tenant_pair["first"]["tenant_id"])
    second = Seeder(owner_database_url, tenant_pair["second"]["tenant_id"])
    reception = first.assistant()
    sales = first.assistant("Sales")

    callback_call = first.conversation(
        reception, "phone", "2026-03-02T09:15:00Z", metrics=_latency(1.2), cost=_cost(0.05, 0.02)
    )
    first.task(callback_call, "callback")
    booked_call = first.conversation(
        reception, "phone", "2026-03-02T14:00:00Z", metrics=_latency(1.8), cost=_cost(0.03, 0.01)
    )
    first.booking(reception, booked_call)
    cancelled_booking = first.conversation(reception, "phone", "2026-03-03T10:00:00Z")
    first.booking(reception, cancelled_booking, status="cancelled")
    first.conversation(
        reception,
        "phone",
        "2026-03-03T11:00:00Z",
        metrics={"transfer": {"target_key": "office", "status": "answered"}},
    )
    first.conversation(reception, "whatsapp", "2026-03-04T08:00:00Z", cost=_cost(0.001))
    operator_email = first.conversation(reception, "gmail", "2026-03-05T12:00:00Z")
    first.message(operator_email, "operator")
    system_task_email = first.conversation(reception, "gmail", "2026-03-06T12:00:00Z")
    first.task(system_task_email, "action_failed")
    urgent_whatsapp = first.conversation(sales, "whatsapp", "2026-03-07T23:30:00Z")
    first.task(urgent_whatsapp, "urgent")
    first.task(urgent_whatsapp, "callback")
    first.conversation(
        reception,
        "web",
        "2026-03-02T10:00:00Z",
        metrics={"test_call": True, **_latency(0.4)},
        cost=_cost(0.02),
    )
    first.conversation(reception, "phone", "2026-02-20T10:00:00Z", cost=_cost(1.0))
    first.conversation(reception, "phone", "2026-03-08T00:30:00Z")

    other = second.assistant("Other tenant")
    for index in range(3):
        leaked = second.conversation(
            other, "phone", f"2026-03-0{index + 2}T10:00:00Z", metrics=_latency(9.0), cost=_cost(5)
        )
        second.task(leaked, "callback")
        second.booking(other, leaked)
    return {
        "first": tenant_pair["first"]["client"],
        "second": tenant_pair["second"]["client"],
        "reception": reception,
        "sales": sales,
        "other": other,
    }


async def test_dashboard_summary_aggregates_seeded_tenant(seeded) -> None:
    response = await seeded["first"].get("/dashboard/summary", params=RANGE)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["conversations"] == 8
    assert body["by_channel"] == {"phone": 4, "web": 0, "whatsapp": 2, "gmail": 2}
    assert body["passed_to_staff"] == 4
    assert body["handled_by_assistant"] == 4
    assert body["bookings"] == 1
    assert body["callbacks"] == 2
    assert body["tasks"] == 3
    minutes = MINUTES_SAVED_PER_CONVERSATION
    expected_saved = (
        minutes["phone"] * (2 + 2 * PASSED_TO_STAFF_CREDIT)
        + minutes["whatsapp"] * (1 + PASSED_TO_STAFF_CREDIT)
        + minutes["gmail"] * (1 + PASSED_TO_STAFF_CREDIT)
    )
    assert body["time_saved_minutes"] == pytest.approx(expected_saved)
    assert body["response_latency"] == {"median_seconds": 1.5, "calls_measured": 2}
    assert body["cost"] == {
        "currency": "USD",
        "total": pytest.approx(0.081),
        "ai": pytest.approx(0.051),
        "telephony": pytest.approx(0.03),
        "conversations_costed": 3,
    }
    assert body["range"]["bucket"] == "day"
    assert body["range"]["start"].startswith("2026-03-01T00:00:00")
    series = body["series"]
    assert [point["start"][:10] for point in series] == [f"2026-03-0{day}" for day in range(1, 8)]
    assert sum(point["total"] for point in series) == 8
    assert series[1] == {**series[1], "phone": 2, "total": 2}
    assert series[6]["whatsapp"] == 1


async def test_dashboard_filters_by_assistant_and_includes_practice_calls(seeded) -> None:
    sales = await seeded["first"].get(
        "/dashboard/summary", params={**RANGE, "assistant_id": str(seeded["sales"])}
    )
    assert sales.status_code == 200, sales.text
    assert sales.json()["conversations"] == 1
    assert sales.json()["by_channel"]["whatsapp"] == 1

    with_tests = await seeded["first"].get(
        "/dashboard/summary", params={**RANGE, "include_test": "true"}
    )
    body = with_tests.json()
    assert body["conversations"] == 9
    assert body["by_channel"]["web"] == 1
    assert body["response_latency"] == {"median_seconds": 1.2, "calls_measured": 3}


async def test_dashboard_never_leaks_across_tenants(seeded) -> None:
    second = await seeded["second"].get("/dashboard/summary", params=RANGE)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["conversations"] == 3
    assert body["bookings"] == 3
    assert body["cost"]["total"] == pytest.approx(15)
    assert body["response_latency"]["median_seconds"] == pytest.approx(9.0)

    probing = await seeded["second"].get(
        "/dashboard/summary", params={**RANGE, "assistant_id": str(seeded["reception"])}
    )
    assert probing.status_code == 200
    assert probing.json()["conversations"] == 0
    probing_first = await seeded["first"].get(
        "/dashboard/summary", params={**RANGE, "assistant_id": str(seeded["other"])}
    )
    assert probing_first.json()["conversations"] == 0


async def test_dashboard_empty_state(tenant_pair) -> None:
    response = await tenant_pair["first"]["client"].get(
        "/dashboard/summary", params={"range": "today"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversations"] == 0
    assert body["handled_by_assistant"] == 0
    assert body["time_saved_minutes"] == 0
    assert body["response_latency"] == {"median_seconds": None, "calls_measured": 0}
    assert body["cost"]["total"] == 0
    assert body["range"]["bucket"] == "hour"
    assert len(body["series"]) == 24
    assert all(point["total"] == 0 for point in body["series"])


async def test_dashboard_requires_login_and_valid_range(api_client, tenant_pair) -> None:
    assert (await api_client.get("/dashboard/summary")).status_code == 401
    client = tenant_pair["first"]["client"]
    for params in (
        {"range": "custom"},
        {"range": "custom", "start": "2026-03-07", "end": "2026-03-01"},
        {"range": "custom", "start": "2025-01-01", "end": "2026-03-01"},
        {"range": "7d", "timezone": "Mars/Olympus"},
        {"range": "year"},
    ):
        assert (await client.get("/dashboard/summary", params=params)).status_code == 422


def test_resolve_range_presets_follow_local_days() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    today = resolve_range("today", "Europe/London", now=now)
    assert today.bucket == "hour"
    assert today.start.isoformat() == "2026-03-29T00:00:00+00:00"
    assert today.end.isoformat() == "2026-03-30T00:00:00+01:00"
    week = resolve_range("7d", "Europe/London", now=now)
    assert week.bucket == "day"
    assert week.start.date() == date(2026, 3, 23)
    month = resolve_range("30d", "UTC", now=now)
    assert month.start.date() == date(2026, 2, 28)
    with pytest.raises(DashboardRangeError):
        resolve_range("custom", "UTC", start_date=date(2026, 1, 1))


def test_add_conversation_cost_accumulates_text_replies() -> None:
    conversation = Conversation(estimated_cost=None)
    reply = {
        "currency": "USD",
        "components": {"llm_input": 0.001, "llm_output": 0.002, "stt": None},
        "total": 0.003,
        "unknown_components": ["stt"],
    }
    add_conversation_cost(conversation, reply)
    add_conversation_cost(conversation, reply)
    add_conversation_cost(conversation, None)
    assert conversation.estimated_cost == {
        "currency": "USD",
        "components": {"llm_input": pytest.approx(0.002), "llm_output": pytest.approx(0.004)},
        "total": pytest.approx(0.006),
        "unknown_components": ["stt"],
    }


def test_migration_backfills_text_conversation_costs(tenant_pair, owner_database_url) -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "alembic/versions/0172_dashboard_analytics.py"
    spec = importlib.util.spec_from_file_location("migration_0172", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    seeder = Seeder(owner_database_url, tenant_pair["first"]["tenant_id"])
    assistant = seeder.assistant()
    email = seeder.conversation(assistant, "gmail", "2026-03-02T09:00:00Z")
    priced_call = seeder.conversation(assistant, "phone", "2026-03-02T09:00:00Z")
    reply = {
        "currency": "USD",
        "components": {"llm_input": 0.001, "llm_output": 0.002},
        "total": 0.003,
    }
    for conversation_id in (email, email, priced_call):
        seeder.execute(
            "INSERT INTO messages (tenant_id, conversation_id, role, text, ts, meta) "
            "VALUES (%s, %s, 'assistant', 'reply', now(), %s::jsonb)",
            (seeder.tenant_id, conversation_id, json.dumps({"cost": reply})),
        )
    seeder.message(email, "caller")
    with psycopg.connect(owner_database_url) as connection:
        connection.execute(migration.BACKFILL_TEXT_COSTS_SQL)
        rows = dict(
            connection.execute(
                "SELECT id, estimated_cost FROM conversations WHERE id = ANY(%s)",
                ([email, priced_call],),
            ).fetchall()
        )
    assert rows[priced_call] is None
    assert rows[email]["currency"] == "USD"
    assert float(rows[email]["total"]) == pytest.approx(0.006)
    assert float(rows[email]["components"]["llm_output"]) == pytest.approx(0.004)
