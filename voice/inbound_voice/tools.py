from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from inbound_voice.config import AssistantConfig, DateDeadlineUrgencyRule, load_assistant_config
from inbound_voice.intake import build_service_model, days_until_deadline
from inbound_voice.sinks import CallSink

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml"


class CallTools:
    def __init__(self, sink: CallSink, config: AssistantConfig | None = None) -> None:
        self.sink = sink
        self.config = config or load_assistant_config(DEFAULT_CONFIG_PATH)
        self.models = {
            name: build_service_model(service) for name, service in self.config.services.items()
        }
        self.intake: dict[str, Any] = {}
        self._deadline_tasks: set[tuple[int, str]] = set()

    def save_intake(self, service: str, fields: dict[str, Any]) -> dict[str, Any]:
        model = self.models.get(service)
        if model is None:
            return {
                "ok": False,
                "errors": [{"field": "service", "message": "Unknown service."}],
            }
        previous = self.intake.get(service, {})
        try:
            validated = model.model_validate({**previous, **fields})
        except ValidationError as error:
            return {"ok": False, "errors": error.errors(include_url=False)}

        data = validated.model_dump(mode="json", exclude_unset=False)
        self.intake[service] = data
        output_data = data.copy()
        for index, rule in enumerate(self.config.urgency_rules):
            if (
                isinstance(rule, DateDeadlineUrgencyRule)
                and rule.service == service
                and data.get(rule.field)
                and data[rule.field] != previous.get(rule.field)
            ):
                days_left = days_until_deadline(
                    date.fromisoformat(data[rule.field]),
                    rule.add_months,
                    rule.minus_days,
                )
                if days_left <= rule.urgent_within_days:
                    key = (index, data[rule.field])
                    if key not in self._deadline_tasks:
                        self._deadline_tasks.add(key)
                        self.flag_urgent(f"{rule.reason} Calculated deadline is {days_left} days away.")
        self.sink.save_intake(
            {
                "service": service,
                "fields": output_data,
                "conflict_check": "pending",
            }
        )
        return {"ok": True, "intake": output_data}

    def flag_urgent(self, reason: str) -> dict[str, Any]:
        self.sink.append_task("urgent", {"reason": reason})
        return {"ok": True, "status": "Urgent follow-up flagged."}

    def log_complaint(self, summary: str) -> dict[str, Any]:
        self.sink.append_task("complaint", {"summary": summary})
        return {"ok": True, "status": "Complaint logged for the complaints handler."}

    def request_callback(self, reason: str, preferred_time: str | None = None) -> dict[str, Any]:
        self.sink.append_task("callback", {"reason": reason, "preferred_time": preferred_time})
        return {"ok": True, "status": "Callback request logged."}

    def end_call(self) -> dict[str, Any]:
        self.sink.append_task("end_call", {})
        return {"ok": True, "status": "The call may now end."}

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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


def tool_specs(config: AssistantConfig | None = None) -> list[dict[str, Any]]:
    assistant = config or load_assistant_config(DEFAULT_CONFIG_PATH)
    action_specs = {
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
    return [
        {
            "name": "save_intake",
            "description": "Save validated fields for one configured service.",
            "properties": {
                "service": {"type": "string", "enum": list(assistant.services)},
                "fields": {
                    "type": "object",
                    "description": _intake_fields_description(assistant),
                },
            },
            "required": ["service", "fields"],
        },
        *[
            {"name": name, **action_specs[name]}
            for name in assistant.live_actions
            if name in action_specs
        ],
    ]


def _intake_fields_description(config: AssistantConfig) -> str:
    lines = ["Fields by service:"]
    for service_name, service in config.services.items():
        lines.append(f"{service_name}:")
        lines.extend(
            f"- {field.name}: {field.type}"
            + (f" choices: {', '.join(field.choices)}" if field.choices else "")
            for field in service.fields
        )
    lines.extend(["Dates must use ISO YYYY-MM-DD format.", "Fields merge across calls."])
    return "\n".join(lines)
