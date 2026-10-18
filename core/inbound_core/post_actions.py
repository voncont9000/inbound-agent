from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from inbound_core.config import PostAction

CONVERSATION_EVENTS = (
    "conversation_completed",
    "urgent",
    "callback_requested",
    "needs_human",
    "complaint",
)
EVENT_TASK_KINDS = {
    "urgent": {"urgent"},
    "callback_requested": {"callback"},
    "complaint": {"complaint"},
    "needs_human": {"needs_human", "email_needs_human"},
}
TEMPLATE_VARIABLES: list[tuple[str, str]] = [
    ("business_name", "The configured business name."),
    ("contact.name", "The customer's name, when known."),
    ("contact.email", "The customer's email address, when known."),
    ("contact.phone", "The customer's phone number, when known."),
    ("channel", "The conversation channel."),
    ("started_at", "The conversation start time in the assistant's timezone."),
    ("service", "The selected service label."),
    ("service_key", "The selected service key."),
    ("intake.<field_name>", "An individual merged intake field value."),
    ("intake_table", "Merged intake fields formatted as Label: value lines."),
    ("summary", "The conversation summary."),
    ("urgency", "Whether the conversation is urgent or normal."),
    ("urgency_reasons", "Reasons recorded for urgency."),
    ("tasks", "Conversation tasks formatted as kind and reason lines."),
    ("events", "Comma-separated events that triggered this action."),
    ("conversation.id", "The conversation ID."),
    ("conversation.link", "A link to the conversation in the app."),
]
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_SHEET_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def derive_events(task_kinds: Iterable[str]) -> set[str]:
    events = {"conversation_completed"}
    task_kinds = set(task_kinds)
    for event, kinds in EVENT_TASK_KINDS.items():
        if task_kinds & kinds:
            events.add(event)
    return events


def matched_events(
    action: PostAction,
    events: set[str],
    *,
    channel: str,
    service: str,
) -> set[str]:
    if not action.enabled:
        return set()
    if action.trigger.channels and channel not in action.trigger.channels:
        return set()
    if action.trigger.services and service not in action.trigger.services:
        return set()
    return set(action.trigger.events) & events


def render_template(
    template: str,
    context: Mapping[str, str],
    *,
    sheet_cell: bool = False,
) -> str:
    def replace(match: re.Match[str]) -> str:
        value = context.get(match.group(1), "")
        if sheet_cell and value.startswith(_SHEET_FORMULA_PREFIXES):
            return f"'{value}"
        return value

    return _PLACEHOLDER.sub(replace, template)


def render_subject(template: str, context: Mapping[str, str]) -> str:
    return render_template(template, context).replace("\r", " ").replace("\n", " ")


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(format_value(item) for item in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    return str(value)
