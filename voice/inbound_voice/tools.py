import types
from datetime import date
from typing import Any, Union, get_args, get_origin
from typing import Literal as TypingLiteral

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
            if days_left <= 28 and data["termination_date"] != previous.get("termination_date"):
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
    fields_description = _intake_fields_description()
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
                "fields": {"type": "object", "description": fields_description},
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


def _intake_fields_description() -> str:
    lines = ["Fields by practice area:"]
    for practice_area, model in PRESETS.items():
        lines.append(f"{practice_area}:")
        lines.extend(
            f"- {name}: {_format_field_type(field.annotation)}"
            for name, field in model.model_fields.items()
        )
    lines.extend(
        [
            "Dates must use ISO YYYY-MM-DD format.",
            "Booleans must be true or false.",
            "Fields merge across calls; pass only new or corrected fields.",
        ]
    )
    return "\n".join(lines)


def _format_field_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is TypingLiteral:
        choices = ", ".join(repr(choice) for choice in arguments)
        return f"Literal[{choices}]"
    if origin in (Union, types.UnionType):
        non_null_arguments = [argument for argument in arguments if argument is not type(None)]
        if len(non_null_arguments) == 1:
            return _format_field_type(non_null_arguments[0])
        return " | ".join(_format_field_type(argument) for argument in non_null_arguments)
    if origin is list:
        return f"list[{_format_field_type(arguments[0])}]"
    if origin is dict:
        return f"dict[{_format_field_type(arguments[0])}, {_format_field_type(arguments[1])}]"
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is date:
        return "date"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if getattr(annotation, "__name__", None) == "EmailStr":
        return "string (email)"
    return getattr(annotation, "__name__", str(annotation))
