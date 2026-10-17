from datetime import date

from inbound_voice.config import AssistantConfig, DateDeadlineUrgencyRule


def render_system_prompt(config: AssistantConfig, today: date | None = None) -> str:
    current_date = today or date.today()
    fee_table = (
        "\n".join(f"- {fee.service}: {fee.price}. {fee.notes}" for fee in config.price_list)
        or "- No published prices are configured."
    )
    hours = (
        f"{', '.join(config.office_hours.days)}, {config.office_hours.opens}–"
        f"{config.office_hours.closes} {config.office_hours.timezone}"
    )
    policies = "\n".join(f"- {policy}" for policy in config.policies)
    contacts = "\n".join(
        f"- {name.replace('_', ' ')}: {value}" for name, value in config.contacts.items()
    )
    services = []
    has_critical_fields = False
    for name, service in config.services.items():
        fields = []
        for field in service.fields:
            choices = f" Choices: {', '.join(field.choices)}." if field.choices else ""
            critical = " Critical field." if field.critical else ""
            has_critical_fields = has_critical_fields or field.critical
            fields.append(f"- {field.label} ({field.name}, {field.type}).{choices}{critical}")
        services.append(f"{service.label} ({name}):\n" + "\n".join(fields))
    enabled_actions = ", ".join(config.live_actions)
    actions = (
        f"Enabled actions: {enabled_actions}."
        if enabled_actions
        else "No live actions are enabled."
    )
    urgency_instruction = (
        "Recognize urgency immediately and flag it using flag_urgent"
        if "flag_urgent" in config.live_actions
        else "Recognize urgency immediately"
    )
    deadline_instructions = []
    for rule in config.urgency_rules:
        if not isinstance(rule, DateDeadlineUrgencyRule):
            continue
        service = config.services.get(rule.service)
        field = (
            next(
                (item for item in service.fields if item.name == rule.field),
                None,
            )
            if service
            else None
        )
        label = field.label if field else rule.field
        deadline_instructions.append(
            f"- The configured deadline rule handles {label} for {rule.service} "
            f"and flags qualifying dates within {rule.urgent_within_days} days once confirmed."
        )
        if "flag_urgent" in config.live_actions:
            deadline_instructions[-1] += (
                " Do not call flag_urgent solely for this configured deadline."
            )
    final_readback = (
        "Ask for each critical field at a natural point. If the caller does not answer, "
        "acknowledge it, move on, and return to it once before the end.\n"
        "- Read back names, phone numbers, email addresses and dates to confirm them.\n"
        "- When the caller wants to finish or says goodbye, read back all collected, "
        "unconfirmed critical details in one sentence and wait for confirmation. Do not "
        "invent values for details the caller has not provided."
        if has_critical_fields
        else ""
    )
    if final_readback:
        final_readback = f"- {final_readback}"
        if "confirm_critical_fields" in config.live_actions:
            final_readback += (
                "\n- If the caller confirms any values, call confirm_critical_fields with "
                "only those confirmed critical fields."
            )
    end_call_instruction = ""
    if "end_call" in config.live_actions:
        end_call_instruction = (
            "- After the caller confirms the final readback, say goodbye and call end_call "
            "in the same turn."
            if has_critical_fields
            else "- When the caller is ready to finish, say goodbye and call end_call "
            "in the same turn."
        )
    return f"""You are the telephone assistant for {config.business_name}.

Speak {config.language}.
Use short, natural spoken sentences. Ask one question at a time.
When the caller volunteers details, acknowledge them briefly and continue. Never ask the same
question twice in a row.

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
{final_readback}
- Collect other details conversationally; they are extracted after the call. Do not call a tool
  for each detail.
- {urgency_instruction}; do not delay the conversation to finish intake.
{chr(10).join(deadline_instructions)}
- Be honest when the business is closed. Office hours: {hours}.
{end_call_instruction}

{actions}
"""
