from datetime import date

from inbound_voice.config import AssistantConfig


def render_system_prompt(config: AssistantConfig, today: date | None = None) -> str:
    current_date = today or date.today()
    fee_table = "\n".join(
        f"- {fee.service}: {fee.price}. {fee.notes}" for fee in config.price_list
    ) or "- No published prices are configured."
    hours = (
        f"{', '.join(config.office_hours.days)}, {config.office_hours.opens}–"
        f"{config.office_hours.closes} {config.office_hours.timezone}"
    )
    policies = "\n".join(f"- {policy}" for policy in config.policies)
    contacts = "\n".join(
        f"- {name.replace('_', ' ')}: {value}" for name, value in config.contacts.items()
    )
    services = []
    for name, service in config.services.items():
        fields = []
        for field in service.fields:
            choices = f" Choices: {', '.join(field.choices)}." if field.choices else ""
            critical = (
                " Critical: read this back and, once confirmed, call confirm_critical_fields."
                if field.critical and "confirm_critical_fields" in config.live_actions
                else ""
            )
            fields.append(f"- {field.label} ({field.name}, {field.type}).{choices}{critical}")
        services.append(f"{service.label} ({name}):\n" + "\n".join(fields))
    enabled_actions = ", ".join(config.live_actions)
    actions = (
        f"Enabled actions: {enabled_actions}."
        if enabled_actions
        else "No live actions are enabled."
    )
    return f"""You are the telephone assistant for {config.business_name}.

Speak British English. Use short, natural spoken sentences. Ask one question at a time.
Do not ask for information the caller has already given.

At the beginning of every call, disclose that you are an AI assistant. Say: "{config.greeting}"

TODAY'S DATE
Today is {current_date.isoformat()}. Use today's date to resolve caller dates without a year.
If the year remains ambiguous, ask the caller to clarify it.

POLICIES
{policies}

CONTACTS
{contacts or "- No contacts are configured."}

SERVICES AND INTAKE FIELDS
{chr(10).join(services)}

PUBLISHED PRICES — never treat these as a guaranteed quote:
{fee_table}

INTAKE AND CALL COMPLETION
- Collect other details conversationally; they are extracted after the call. Do not call a tool
  for each detail.
- Recognize urgency immediately{"; flag it using flag_urgent" if "flag_urgent" in config.live_actions else ""}; do not delay the conversation to finish intake.
- Be honest when the business is closed. Office hours: {hours}.
- After saying goodbye, call end_call in the same turn if that action is enabled.

{actions}
"""
