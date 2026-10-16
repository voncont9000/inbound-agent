from typing import Any

from pydantic import ValidationError

from inbound_voice.intake import (
    PRESETS,
    EmploymentIntake,
    days_until_unfair_dismissal_deadline,
)
from inbound_voice.sinks import CallSink


class CallTools:
    def __init__(self, sink: CallSink) -> None:
        self.sink = sink
        self.intake: dict[str, Any] = {}

    def save_intake(self, practice_area: str, fields: dict[str, Any]) -> dict[str, Any]:
        model = PRESETS.get(practice_area)
        if model is None:
            return {
                "ok": False,
                "errors": [{"field": "practice_area", "message": "Unknown practice area."}],
            }
        previous = self.intake.get(practice_area, {})
        try:
            validated = model.model_validate({**previous, **fields})
        except ValidationError as error:
            return {"ok": False, "errors": error.errors(include_url=False)}

        data = validated.model_dump(mode="json", exclude_unset=False)
        self.intake[practice_area] = data
        output_data = data.copy()
        if isinstance(validated, EmploymentIntake) and validated.termination_date:
            days_left = days_until_unfair_dismissal_deadline(validated.termination_date)
            output_data["unfair_dismissal_days_remaining"] = days_left
            if days_left <= 28:
                self.flag_urgent(
                    f"Employment termination date recorded; unfair dismissal deadline "
                    f"calculated as {days_left} days away."
                )
        self.sink.save_intake(
            {
                "practice_area": practice_area,
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
            return {"ok": False, "error": "Unknown tool."}
        try:
            return handler(**arguments)
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "save_intake",
            "description": (
                "Save validated intake fields. Practice area must be conveyancing or employment. "
                "If validation fails, ask the caller to correct the returned fields."
            ),
            "properties": {
                "practice_area": {
                    "type": "string",
                    "enum": ["conveyancing", "employment"],
                },
                "fields": {"type": "object", "description": "Fields for the chosen intake preset."},
            },
            "required": ["practice_area", "fields"],
        },
        {
            "name": "flag_urgent",
            "description": "Immediately flag danger, distress, or a time-critical issue.",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
        {
            "name": "log_complaint",
            "description": "Log a complaint for the firm's complaints handler.",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        {
            "name": "request_callback",
            "description": "Request a solicitor or staff callback.",
            "properties": {
                "reason": {"type": "string"},
                "preferred_time": {"type": "string"},
            },
            "required": ["reason"],
        },
        {
            "name": "end_call",
            "description": "End the call after the caller's needs have been handled.",
            "properties": {},
            "required": [],
        },
    ]
