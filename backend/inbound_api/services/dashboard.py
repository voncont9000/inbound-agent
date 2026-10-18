"""Home dashboard analytics (PRD FR-S14).

Definitions (also documented in docs/m1-architecture.md#home-dashboard):

* A conversation is counted in the range when its ``started_at`` falls inside it.
  Practice calls (``metrics.test_call``) are excluded unless ``include_test`` is set.
* **Passed to staff**: the conversation created at least one staff task (any task kind
  except the system kinds in ``SYSTEM_TASK_KINDS``, e.g. ``callback``, ``urgent``,
  ``complaint``, ``email_needs_human``), an operator replied in it, or a call was
  transferred to a person (``metrics.transfer.status`` in ``TRANSFERRED_STATUSES``).
  Every other conversation is **handled by the assistant**.
* **Time saved** (minutes) = for each conversation,
  ``MINUTES_SAVED_PER_CONVERSATION[channel]`` when handled by the assistant, or that value
  times ``PASSED_TO_STAFF_CREDIT`` when passed to staff (the assistant still did the intake).
* **Response latency**: the median, across calls, of each call's median time from the
  caller finishing speaking to the agent's first audio
  (``metrics.turn_latency_summary.p50_seconds``, written by the voice runtime).
* **Cost**: the sum of ``conversations.estimated_cost.total``. Telephony is the ``twilio``
  component; AI is the rest (STT, TTS, LLM, extraction).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RangePreset = Literal["today", "7d", "30d", "custom"]
Bucket = Literal["hour", "day"]
CHANNELS = ("phone", "web", "whatsapp", "gmail")

MINUTES_SAVED_PER_CONVERSATION: dict[str, float] = {
    "phone": 5.0,
    "web": 5.0,
    "whatsapp": 4.0,
    "gmail": 6.0,
}
PASSED_TO_STAFF_CREDIT = 0.5
SYSTEM_TASK_KINDS = ("action_failed", "action_setup_needed")
TRANSFERRED_STATUSES = ("answered", "completed", "simulated")
MAX_CUSTOM_RANGE_DAYS = 366


class DashboardRangeError(ValueError):
    pass


class ChannelCounts(BaseModel):
    phone: int = 0
    web: int = 0
    whatsapp: int = 0
    gmail: int = 0


class DashboardRange(BaseModel):
    preset: RangePreset
    start: datetime
    end: datetime
    timezone: str
    bucket: Bucket


class DashboardLatency(BaseModel):
    median_seconds: float | None
    calls_measured: int


class DashboardCost(BaseModel):
    currency: str
    total: float
    ai: float
    telephony: float
    conversations_costed: int


class DashboardSeriesPoint(ChannelCounts):
    start: datetime
    total: int = 0


class DashboardSummary(BaseModel):
    range: DashboardRange
    assistant_id: UUID | None
    include_test: bool
    conversations: int
    by_channel: ChannelCounts
    handled_by_assistant: int
    passed_to_staff: int
    bookings: int
    callbacks: int
    tasks: int
    time_saved_minutes: float
    minutes_saved_per_conversation: dict[str, float]
    passed_to_staff_credit: float
    response_latency: DashboardLatency
    cost: DashboardCost
    series: list[DashboardSeriesPoint]


def resolve_range(
    preset: RangePreset,
    timezone: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> DashboardRange:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise DashboardRangeError("Unknown timezone") from error
    local_now = (now or datetime.now(UTC)).astimezone(zone)
    today = local_now.date()
    if preset == "custom":
        if start_date is None or end_date is None:
            raise DashboardRangeError("Custom range needs start and end dates")
        if end_date < start_date:
            raise DashboardRangeError("End date is before start date")
        if (end_date - start_date).days + 1 > MAX_CUSTOM_RANGE_DAYS:
            raise DashboardRangeError(f"Custom range is limited to {MAX_CUSTOM_RANGE_DAYS} days")
        first_day, last_day = start_date, end_date
    else:
        days = {"today": 1, "7d": 7, "30d": 30}[preset]
        first_day, last_day = today - timedelta(days=days - 1), today
    start = datetime.combine(first_day, time.min, tzinfo=zone)
    end = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=zone)
    return DashboardRange(
        preset=preset,
        start=start,
        end=end,
        timezone=timezone,
        bucket="hour" if first_day == last_day else "day",
    )


def _bucket_starts(window: DashboardRange) -> list[datetime]:
    zone = ZoneInfo(window.timezone)
    local_start = window.start.astimezone(zone).replace(tzinfo=None)
    local_end = window.end.astimezone(zone).replace(tzinfo=None)
    step = timedelta(hours=1) if window.bucket == "hour" else timedelta(days=1)
    starts: list[datetime] = []
    cursor = local_start
    while cursor < local_end:
        starts.append(cursor)
        cursor += step
    return starts


_SUMMARY_SQL = """
WITH conv AS (
    SELECT c.id, c.channel::text AS channel, c.metrics, c.estimated_cost,
           date_trunc(:bucket, c.started_at AT TIME ZONE :tz) AS bucket
    FROM conversations c
    WHERE c.tenant_id = :tenant_id
      AND c.started_at >= :start AND c.started_at < :end
      {filters}
),
task_agg AS (
    SELECT t.conversation_id,
           count(*) AS tasks,
           count(*) FILTER (WHERE t.kind = 'callback') AS callbacks
    FROM tasks t
    JOIN conv ON conv.id = t.conversation_id
    WHERE t.tenant_id = :tenant_id AND NOT (t.kind = ANY(:system_kinds))
    GROUP BY t.conversation_id
),
booking_agg AS (
    SELECT b.conversation_id, count(*) AS bookings
    FROM calendar_bookings b
    JOIN conv ON conv.id = b.conversation_id
    WHERE b.tenant_id = :tenant_id AND b.status = 'booked'
    GROUP BY b.conversation_id
),
operator_agg AS (
    SELECT DISTINCT m.conversation_id
    FROM messages m
    JOIN conv ON conv.id = m.conversation_id
    WHERE m.tenant_id = :tenant_id AND m.role = 'operator'
),
per_conversation AS (
    SELECT conv.channel, conv.bucket,
           (
               coalesce(task_agg.tasks, 0) > 0
               OR operator_agg.conversation_id IS NOT NULL
               OR coalesce(conv.metrics->'transfer'->>'status', '') = ANY(:transferred)
           ) AS passed,
           coalesce(task_agg.tasks, 0) AS tasks,
           coalesce(task_agg.callbacks, 0) AS callbacks,
           coalesce(booking_agg.bookings, 0) AS bookings,
           CASE WHEN jsonb_typeof(conv.estimated_cost->'total') = 'number'
                THEN (conv.estimated_cost->>'total')::float END AS cost_total,
           CASE WHEN jsonb_typeof(conv.estimated_cost->'components'->'twilio') = 'number'
                THEN (conv.estimated_cost->'components'->>'twilio')::float ELSE 0 END
               AS telephony,
           conv.estimated_cost->>'currency' AS currency,
           CASE WHEN conv.channel IN ('phone', 'web')
                 AND jsonb_typeof(conv.metrics->'turn_latency_summary'->'p50_seconds') = 'number'
                THEN (conv.metrics->'turn_latency_summary'->>'p50_seconds')::float END
               AS latency
    FROM conv
    LEFT JOIN task_agg ON task_agg.conversation_id = conv.id
    LEFT JOIN booking_agg ON booking_agg.conversation_id = conv.id
    LEFT JOIN operator_agg ON operator_agg.conversation_id = conv.id
)
SELECT GROUPING(channel, bucket) AS overall,
       channel, bucket,
       count(*) AS conversations,
       count(*) FILTER (WHERE passed) AS passed,
       coalesce(sum(tasks), 0) AS tasks,
       coalesce(sum(callbacks), 0) AS callbacks,
       coalesce(sum(bookings), 0) AS bookings,
       coalesce(sum(cost_total), 0) AS cost_total,
       coalesce(sum(telephony) FILTER (WHERE cost_total IS NOT NULL), 0) AS telephony,
       count(cost_total) AS costed,
       max(currency) AS currency,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency) AS latency_median,
       count(latency) AS latency_count
FROM per_conversation
GROUP BY GROUPING SETS ((channel, bucket), ())
"""


async def dashboard_summary(
    session: AsyncSession,
    tenant_id: UUID,
    window: DashboardRange,
    *,
    assistant_id: UUID | None = None,
    include_test: bool = False,
) -> DashboardSummary:
    filters = []
    params: dict[str, object] = {
        "tenant_id": tenant_id,
        "start": window.start,
        "end": window.end,
        "bucket": window.bucket,
        "tz": window.timezone,
        "system_kinds": list(SYSTEM_TASK_KINDS),
        "transferred": list(TRANSFERRED_STATUSES),
    }
    if assistant_id is not None:
        filters.append("AND c.assistant_id = :assistant_id")
        params["assistant_id"] = assistant_id
    if not include_test:
        filters.append("AND coalesce(c.metrics->>'test_call', 'false') <> 'true'")
    rows = (
        await session.execute(text(_SUMMARY_SQL.format(filters="\n      ".join(filters))), params)
    ).mappings()

    by_channel = ChannelCounts()
    series = {start: DashboardSeriesPoint(start=start) for start in _bucket_starts(window)}
    time_saved = 0.0
    overall = None
    for row in rows:
        if row["overall"]:
            overall = row
            continue
        channel = row["channel"]
        count = int(row["conversations"])
        passed = int(row["passed"])
        setattr(by_channel, channel, getattr(by_channel, channel) + count)
        minutes = MINUTES_SAVED_PER_CONVERSATION.get(channel, 0.0)
        time_saved += minutes * (count - passed) + minutes * PASSED_TO_STAFF_CREDIT * passed
        point = series.get(row["bucket"])
        if point is not None:
            setattr(point, channel, getattr(point, channel) + count)
            point.total += count

    zone = ZoneInfo(window.timezone)
    total = int(overall["conversations"]) if overall else 0
    passed_total = int(overall["passed"]) if overall else 0
    cost_total = float(overall["cost_total"]) if overall else 0.0
    telephony = float(overall["telephony"]) if overall else 0.0
    latency = overall["latency_median"] if overall else None
    return DashboardSummary(
        range=window,
        assistant_id=assistant_id,
        include_test=include_test,
        conversations=total,
        by_channel=by_channel,
        handled_by_assistant=total - passed_total,
        passed_to_staff=passed_total,
        bookings=int(overall["bookings"]) if overall else 0,
        callbacks=int(overall["callbacks"]) if overall else 0,
        tasks=int(overall["tasks"]) if overall else 0,
        time_saved_minutes=round(time_saved, 1),
        minutes_saved_per_conversation=dict(MINUTES_SAVED_PER_CONVERSATION),
        passed_to_staff_credit=PASSED_TO_STAFF_CREDIT,
        response_latency=DashboardLatency(
            median_seconds=round(float(latency), 3) if latency is not None else None,
            calls_measured=int(overall["latency_count"]) if overall else 0,
        ),
        cost=DashboardCost(
            currency=(overall["currency"] if overall else None) or "USD",
            total=round(cost_total, 4),
            ai=round(max(0.0, cost_total - telephony), 4),
            telephony=round(telephony, 4),
            conversations_costed=int(overall["costed"]) if overall else 0,
        ),
        series=[
            point.model_copy(update={"start": start.replace(tzinfo=zone)})
            for start, point in series.items()
        ],
    )
