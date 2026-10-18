from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from inbound_core.config import AssistantConfig, OfficeHours, TransferTarget


def is_within_office_hours(hours: OfficeHours, now: datetime) -> bool:
    local_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    local_now = local_now.astimezone(ZoneInfo(hours.timezone))
    days = {day.casefold() for day in hours.days}
    if (
        local_now.strftime("%A").casefold() not in days
        and local_now.strftime("%a").casefold() not in days
    ):
        return False
    opens = time.fromisoformat(hours.opens)
    closes = time.fromisoformat(hours.closes)
    return opens <= local_now.timetz().replace(tzinfo=None) < closes


def available_transfer_targets(
    config: AssistantConfig, now: datetime | None = None
) -> list[TransferTarget]:
    if "transfer_call" not in config.live_actions:
        return []
    current_time = now or datetime.now(UTC)
    return [
        target
        for target in config.transfer_targets
        if not target.office_hours_only or is_within_office_hours(config.office_hours, current_time)
    ]
