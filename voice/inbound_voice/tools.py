from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from inbound_core.config import TransferTarget
from inbound_core.transfer import available_transfer_targets
from pydantic import ValidationError

from inbound_voice.booking import CallBooking
from inbound_voice.config import (
    AssistantConfig,
    DateDeadlineUrgencyRule,
    KeywordUrgencyRule,
    load_assistant_config,
)
from inbound_voice.intake import build_service_model, days_until_deadline
from inbound_voice.sinks import CallSink
from inbound_voice.transfer import TransferStart

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml"
)


@dataclass(frozen=True)
class TransferRequest:
    target: TransferTarget
    reason: str
    requested_at: float


class CallTools:
    def __init__(
        self,
        sink: CallSink,
        config: AssistantConfig | None = None,
        booking: CallBooking | None = None,
        booking_available: bool = False,
        now: datetime | None = None,
    ) -> None:
        self.sink = sink
        self.config = config or load_assistant_config(DEFAULT_CONFIG_PATH)
        self.booking = booking
        self.booking_available = booking_available
        self.now = now
        self.transfer_targets = available_transfer_targets(self.config, now)
        self.transfer_request: TransferRequest | None = None
        self.transfer_start: TransferStart | None = None
        self.transfer_started_at: float | None = None
        self.transfer_accepted = False
        self.models = {
            name: build_service_model(service) for name, service in self.config.services.items()
        }
        self.live_intake: dict[str, Any] = {"service": None, "fields": {}}
        self._deadline_tasks: set[tuple[int, str]] = set()
        self._keyword_rules_triggered: set[int] = set()
        self.end_call_requested = False

    def confirm_critical_fields(self, service: str, fields: dict[str, Any]) -> dict[str, Any]:
        service_config = self.config.services.get(service)
        model = self.models.get(service)
        if service_config is None or model is None:
            return {"ok": False, "error": "Unknown service."}

        allowed_fields = [field.name for field in service_config.fields if field.critical]
        rejected_fields = sorted(set(fields) - set(allowed_fields))
        if rejected_fields:
            return {"ok": False, "allowed_fields": allowed_fields}
        if not fields:
            return {"ok": False, "allowed_fields": allowed_fields}
        try:
            validated = model.model_validate(fields)
        except ValidationError as error:
            return {"ok": False, "errors": error.errors(include_url=False)}

        values = validated.model_dump(mode="json", exclude_unset=True)
        if self.live_intake.get("service") != service:
            self.live_intake = {"service": service, "fields": {}}
        confirmed_at = datetime.now(UTC).isoformat()
        for name, value in values.items():
            self.live_intake["fields"][name] = {
                "value": value,
                "confirmed": True,
                "confirmed_at": confirmed_at,
            }
        self.sink.save_live_intake(self.live_intake)
        self._check_deadline_rules(service, values)
        return {"ok": True, "intake": self.live_intake}

    def _check_deadline_rules(self, service: str, fields: dict[str, Any]) -> None:
        for index, rule in enumerate(self.config.urgency_rules):
            if not isinstance(rule, DateDeadlineUrgencyRule) or rule.service != service:
                continue
            value = fields.get(rule.field)
            if not value:
                continue
            start = date.fromisoformat(value)
            days_left = days_until_deadline(start, rule.add_months, rule.minus_days)
            key = (index, value)
            if days_left <= rule.urgent_within_days and key not in self._deadline_tasks:
                self._deadline_tasks.add(key)
                self._append_live_task(
                    "urgent",
                    {
                        "reason": f"{rule.reason} Calculated deadline is {days_left} days away.",
                        "rule": index,
                        "value": value,
                    },
                )

    def check_caller_text(self, text: str) -> list[dict[str, str]]:
        normalized_text = text.casefold()
        matches = []
        for index, rule in enumerate(self.config.urgency_rules):
            if not isinstance(rule, KeywordUrgencyRule) or index in self._keyword_rules_triggered:
                continue
            phrase = next(
                (
                    candidate
                    for candidate in rule.phrases
                    if candidate.casefold() in normalized_text
                ),
                None,
            )
            if phrase is None:
                continue
            self._keyword_rules_triggered.add(index)
            reason = rule.reason.replace("{phrase}", phrase)
            self._append_live_task(
                "urgent",
                {"reason": reason, "matched_phrase": phrase, "rule": index},
            )
            matches.append({"phrase": phrase, "reason": reason})
        return matches

    def flag_urgent(self, reason: str) -> dict[str, Any]:
        deadline_matches = self._matching_deadline_mentions(reason)
        if deadline_matches:
            added = False
            for index, value in deadline_matches:
                key = (index, value)
                if key in self._deadline_tasks:
                    continue
                self._deadline_tasks.add(key)
                self._append_live_task(
                    "urgent",
                    {"reason": reason, "rule": index, "value": value},
                )
                added = True
            status = (
                "Urgent deadline follow-up flagged."
                if added
                else "Urgent deadline follow-up already flagged."
            )
            return {"ok": True, "status": status}
        self._append_live_task("urgent", {"reason": reason})
        return {"ok": True, "status": "Urgent follow-up flagged."}

    def _matching_deadline_mentions(self, reason: str) -> list[tuple[int, str]]:
        normalized_reason = reason.casefold()
        values = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", reason))
        matches = []
        for index, rule in enumerate(self.config.urgency_rules):
            if not isinstance(rule, DateDeadlineUrgencyRule):
                continue
            service = self.config.services.get(rule.service)
            field = (
                next(
                    (item for item in service.fields if item.name == rule.field),
                    None,
                )
                if service
                else None
            )
            labels = {rule.field.casefold()}
            if field:
                labels.add(field.label.casefold())
            if not any(label in normalized_reason for label in labels):
                continue
            for value in values:
                try:
                    start = date.fromisoformat(value)
                except ValueError:
                    continue
                days_left = days_until_deadline(start, rule.add_months, rule.minus_days)
                if days_left <= rule.urgent_within_days:
                    matches.append((index, value))
        return matches

    def log_complaint(self, summary: str) -> dict[str, Any]:
        self._append_live_task("complaint", {"summary": summary})
        return {"ok": True, "status": "Complaint logged for the complaints handler."}

    def request_callback(self, reason: str, preferred_time: str | None = None) -> dict[str, Any]:
        self._append_live_task("callback", {"reason": reason, "preferred_time": preferred_time})
        return {"ok": True, "status": "Callback request logged."}

    def end_call(self) -> dict[str, Any]:
        self.end_call_requested = True
        self._append_live_task("end_call", {})
        return {"ok": True, "status": "The call may now end."}

    def transfer_call(self, target_key: str, reason: str) -> dict[str, Any]:
        if self.transfer_request is not None:
            return {"ok": False, "error": "A transfer is already pending."}
        target = next(
            (target for target in self.transfer_targets if target.key == target_key),
            None,
        )
        if target is None:
            return {"ok": False, "error": "Unknown transfer target."}
        self.transfer_request = TransferRequest(target, reason, time.monotonic())
        return {"ok": True, "status": "Transferring the caller."}

    def _append_live_task(self, task_type: str, details: dict[str, Any]) -> None:
        self.sink.append_task(task_type, {**details, "source": "live"})

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.config.live_actions:
            result = {"ok": False, "error": "Action is not enabled."}
        else:
            handler = getattr(self, name, None)
            if handler is None or name.startswith("_"):
                result = {"ok": False, "error": "Unknown tool."}
            else:
                try:
                    result = handler(**arguments)
                except (TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
        self.sink.append_tool_call(name, arguments, result)
        return result

    async def dispatch_async(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in {"check_availability", "book_appointment"}:
            return self.dispatch(name, arguments)
        if name not in self.config.live_actions:
            result = {"ok": False, "error": "Action is not enabled."}
        elif not self.booking_available or self.booking is None:
            result = {"ok": False, "error": "Appointment booking is not available."}
        else:
            try:
                result = await getattr(self.booking, name)(**arguments)
            except (TypeError, ValueError) as error:
                result = {"ok": False, "error": str(error)}
        self.sink.append_tool_call(name, arguments, result)
        return result


def tool_specs(
    config: AssistantConfig | None = None,
    now: datetime | None = None,
    booking_available: bool = False,
) -> list[dict[str, Any]]:
    assistant = config or load_assistant_config(DEFAULT_CONFIG_PATH)
    transfer_targets = available_transfer_targets(assistant, now)
    actions = {
        "confirm_critical_fields": {
            "description": (
                "Persist only critical fields that have been read back and confirmed by the caller."
            ),
            "properties": {
                "service": {"type": "string", "enum": list(assistant.services)},
                "fields": {
                    "type": "object",
                    "description": _critical_fields_description(assistant),
                },
            },
            "required": ["service", "fields"],
        },
        "flag_urgent": {
            "description": "Immediately flag danger, distress, or a time-critical issue.",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
        "log_complaint": {
            "description": "Log a complaint for the business complaints handler.",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        "request_callback": {
            "description": "Request a staff callback.",
            "properties": {
                "reason": {"type": "string"},
                "preferred_time": {"type": "string"},
            },
            "required": ["reason"],
        },
        "end_call": {
            "description": "End the call after the caller's needs have been handled.",
            "properties": {},
            "required": [],
        },
    }
    if transfer_targets:
        actions["transfer_call"] = {
            "description": (
                "Transfer the caller to a person. Call this tool straight away without announcing "
                "the transfer; the system will tell the caller."
            ),
            "properties": {
                "target_key": {
                    "type": "string",
                    "enum": [target.key for target in transfer_targets],
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason the caller wants a person.",
                },
            },
            "required": ["target_key", "reason"],
        }
    result = [{"name": name, **actions[name]} for name in assistant.live_actions if name in actions]
    if booking_available and assistant.booking is not None:
        appointment_types = assistant.booking.appointment_types
        descriptions = "\n".join(
            f"{item.key}: {item.label} — {item.when_to_use}" for item in appointment_types
        )
        booking_tools = {
            "check_availability": {
                "description": (
                    "Call this tool immediately without announcing it; the system will say a short "
                    "holding line. Offer only the returned slots, using their 'when' wording."
                ),
                "properties": {
                    "appointment_type": {
                        "type": "string",
                        "enum": [item.key for item in appointment_types],
                        "description": descriptions,
                    },
                    "preference_text": {
                        "type": "string",
                        "description": (
                            "The caller's preferred day/time in their own words; empty if none."
                        ),
                    },
                },
                "required": ["appointment_type", "preference_text"],
            },
            "book_appointment": {
                "description": (
                    "Book a slot the caller chose. Before calling, read the chosen slot back once "
                    "and get a yes. Then call this tool immediately without announcing it; the "
                    "system will say a short holding line."
                ),
                "properties": {
                    "slot_id": {"type": "string"},
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["slot_id", "name"],
            },
        }
        result.extend(
            {"name": name, **booking_tools[name]}
            for name in ("check_availability", "book_appointment")
            if name in assistant.live_actions
        )
    return result


def _critical_fields_description(config: AssistantConfig) -> str:
    lines = ["Critical fields by service; submit only values confirmed by the caller:"]
    for service_name, service in config.services.items():
        fields = [field for field in service.fields if field.critical]
        lines.append(f"{service_name}:")
        lines.extend(
            f"- {field.name}: {field.type}"
            + (f"; choices: {', '.join(field.choices)}" if field.choices else "")
            for field in fields
        )
    lines.append("Dates must use ISO YYYY-MM-DD format.")
    return "\n".join(lines)
