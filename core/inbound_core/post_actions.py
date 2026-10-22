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
    ("contact.first_name", "The first part of the customer's name, when known."),
    ("contact.email", "The customer's email address, when known."),
    ("contact.phone", "The customer's phone number, when known."),
    ("channel", "The conversation channel."),
    ("test_call", "True for browser test calls."),
    ("started_at", "The conversation start time in the assistant's timezone."),
    ("service", "The selected service label."),
    ("service_key", "The selected service key."),
    ("intake.<field_name>", "An individual merged intake field value."),
    ("intake_table", "Merged intake fields formatted as Label: value lines."),
    ("summary", "The conversation summary."),
    ("customer_recap", "A customer-facing recap of the conversation."),
    ("next_step", "The title of the next open callback or follow-up task."),
    ("urgency", "Whether the conversation is urgent or normal."),
    ("urgency_reasons", "Reasons recorded for urgency."),
    ("tasks", "Conversation tasks formatted as kind and reason lines."),
    ("events", "Comma-separated events that triggered this action."),
    ("conversation.id", "The conversation ID."),
    ("conversation.link", "A link to the conversation in the app."),
]
EMAIL_TEMPLATES: list[dict[str, str]] = [
    {
        "key": "inquiry_confirmation",
        "label": "Confirmation of inquiry",
        "description": "Confirm receipt of an enquiry and explain what happens next.",
        "audience": "customer",
        "subject_template": "We've received your enquiry – {{business_name}}",
        "body_template": "\n\n".join(
            [
                "Hello {{contact.first_name|there}},",
                (
                    "Thank you for contacting {{business_name}}. This email confirms that we have "
                    "received your enquiry."
                ),
                "{{customer_recap}}",
                "Next step: {{next_step}}",
                (
                    "A member of our team will get back to you as soon as possible. If anything "
                    "changes in the meantime, simply reply to this email."
                ),
                "Kind regards,\n{{business_name}}",
            ]
        ),
    },
    {
        "key": "callback_confirmation",
        "label": "Callback confirmation",
        "description": (
            "Confirm an agreed callback and give the customer a way to update their details."
        ),
        "audience": "customer",
        "subject_template": "We'll call you back – {{business_name}}",
        "body_template": "\n\n".join(
            [
                "Hello {{contact.first_name|there}},",
                (
                    "Thank you for speaking with {{business_name}}. We'll call you back as soon as "
                    "we can."
                ),
                "{{customer_recap}}",
                "Number we'll call: {{contact.phone}}",
                "If another number or time suits you better, just reply to this email.",
                "Kind regards,\n{{business_name}}",
            ]
        ),
    },
    {
        "key": "request_received",
        "label": "Request received",
        "description": "Acknowledge a request and describe the configured next step.",
        "audience": "customer",
        "subject_template": "Your request – {{business_name}}",
        "body_template": "\n\n".join(
            [
                "Hello {{contact.first_name|there}},",
                "Thank you for your request. We'll confirm the details with you shortly.",
                "{{customer_recap}}",
                "Next step: {{next_step}}",
                "Kind regards,\n{{business_name}}",
            ]
        ),
    },
    {
        "key": "conversation_summary",
        "label": "Summary of your conversation",
        "description": "Share a recap and the details recorded from the conversation.",
        "audience": "customer",
        "subject_template": "Summary of your conversation with {{business_name}}",
        "body_template": "\n\n".join(
            [
                "Hello {{contact.first_name|there}},",
                "Here is a summary of your conversation with {{business_name}}.",
                "{{customer_recap}}",
                "The details you gave us:\n{{intake_table}}",
                "If anything is wrong or missing, reply to this email and we'll update it.",
                "Kind regards,\n{{business_name}}",
            ]
        ),
    },
    {
        "key": "staff_new_enquiry",
        "label": "New enquiry",
        "description": "Send your team the conversation summary and recorded details.",
        "audience": "staff",
        "subject_template": "New enquiry from {{contact.name|a customer}}",
        "body_template": "\n\n".join(
            [
                "{{summary}}",
                (
                    "Next step: {{next_step}}\nName: {{contact.name}}\nPhone: {{contact.phone}}\n"
                    "Email: {{contact.email}}\nChannel: {{channel}}"
                ),
                "{{intake_table}}",
                "{{conversation.link}}",
            ]
        ),
    },
    {
        "key": "staff_urgent",
        "label": "Urgent enquiry",
        "description": "Alert your team to an urgent conversation and its recorded details.",
        "audience": "staff",
        "subject_template": "Urgent: {{contact.name|a customer}} needs attention",
        "body_template": "\n\n".join(
            [
                "Why it's urgent: {{urgency_reasons}}",
                "{{summary}}",
                (
                    "Next step: {{next_step}}\nName: {{contact.name}}\nPhone: {{contact.phone}}\n"
                    "Email: {{contact.email}}\nChannel: {{channel}}"
                ),
                "{{conversation.link}}",
            ]
        ),
    },
]
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*(?:\|\s*([^{}|\n]*?))?\s*\}\}")
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
    context: Mapping[str, Any],
    *,
    sheet_cell: bool = False,
) -> str:
    return _PLACEHOLDER.sub(
        lambda match: _placeholder_value(match, context, sheet_cell=sheet_cell),
        template,
    )


def render_email_body(template: str, context: Mapping[str, Any]) -> str:
    lines = []
    for line in template.splitlines():
        placeholders = list(_PLACEHOLDER.finditer(line))
        if placeholders and all(
            not _placeholder_value(match, context).strip() for match in placeholders
        ):
            continue
        lines.append(
            _PLACEHOLDER.sub(
                lambda match: _placeholder_value(match, context),
                line,
            )
        )
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _placeholder_value(
    match: re.Match[str],
    context: Mapping[str, Any],
    *,
    sheet_cell: bool = False,
) -> str:
    value = context.get(match.group(1), "")
    if isinstance(value, bool):
        value = str(value).lower()
    elif not isinstance(value, str):
        value = format_value(value)
    fallback = match.group(2)
    if not value.strip() and fallback is not None:
        value = fallback.strip()
    if sheet_cell and value.startswith(_SHEET_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def render_subject(template: str, context: Mapping[str, Any]) -> str:
    return render_template(template, context).replace("\r", " ").replace("\n", " ")


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(format_value(item) for item in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    return str(value)
