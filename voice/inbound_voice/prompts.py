from datetime import date

from inbound_voice.config import AssistantConfig, DateDeadlineUrgencyRule


def render_system_prompt(
    config: AssistantConfig,
    today: date | None = None,
    caller_id: str | None = None,
) -> str:
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
    knowledge = _render_knowledge(config)
    services = []
    for name, service in config.services.items():
        fields = []
        for field in service.fields:
            choices = f" Choices: {', '.join(field.choices)}." if field.choices else ""
            critical = " Critical field." if field.critical else ""
            fields.append(f"- {field.label} ({field.name}, {field.type}).{choices}{critical}")
        services.append(f"{service.label} ({name}):\n" + "\n".join(fields))
    enabled_actions = "\n".join(f"- Enabled action: {action}." for action in config.live_actions)
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
            + (
                f"and flags qualifying dates within {rule.urgent_within_days} days once "
                "confirmed live."
                if "confirm_critical_fields" in config.live_actions
                else f"and creates an urgent task after the call for qualifying dates within "
                f"{rule.urgent_within_days} days."
            )
        )
        if "flag_urgent" in config.live_actions:
            deadline_instructions[-1] += (
                " Do not call flag_urgent solely for this configured deadline."
            )
    readback_instructions = {
        "off": "Do not read details back to the caller.",
        "email": (
            "When the caller gives an email address, spell it back once, letter by letter, "
            "and ask them to confirm. Do not read back other details."
        ),
        "critical": (
            "Ask for each critical field at a natural point. If the caller does not answer, "
            "acknowledge it, move on, and return to it once before the end.\n"
            "- Read back names, phone numbers, email addresses and dates to confirm them.\n"
            "- When the caller wants to finish or says goodbye, read back all collected, "
            "unconfirmed critical details in one sentence, ask the caller to confirm them, and "
            "wait for confirmation. Do not invent values for details the caller has not provided."
        ),
    }[config.readback]
    caller_id_instruction = ""
    if caller_id:
        caller_id_instruction = (
            f"The caller is calling from {caller_id}. Do not ask for their phone number; ask once "
            "whether this is the best number to reach them on, and only take a different number "
            "if they say no."
        )
    if "confirm_critical_fields" in config.live_actions:
        readback_instructions += (
            "\n- If the caller confirms any values, call confirm_critical_fields with only "
            "those confirmed critical fields."
        )
    end_call_instruction = ""
    if "end_call" in config.live_actions:
        end_call_instruction = (
            "- When the caller is ready to finish, call end_call in the same response as "
            "your goodbye. Do not wait for another LLM turn after the tool call."
        )
    other_enabled_actions = [
        f"- Use {action} when appropriate."
        for action in config.live_actions
        if action != "end_call"
    ]
    instructions = config.instructions.format(
        business_name=config.business_name,
        greeting=config.greeting,
        office_hours=hours,
    )
    return f"""You are the telephone assistant for {config.business_name}.
Speak {config.language}.
{instructions}

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

OFFICE HOURS
{hours}

{knowledge}

CALL BEHAVIOUR MECHANICS
{readback_instructions}
{caller_id_instruction}
{chr(10).join(deadline_instructions)}
{end_call_instruction}
{chr(10).join(other_enabled_actions)}

{enabled_actions}
"""


def _render_knowledge(config: AssistantConfig) -> str:
    knowledge = config.knowledge
    if not knowledge.faqs and not knowledge.digest_content:
        return ""
    lines = [
        "Business knowledge (answer questions only from this, the fee list and the details above):"
    ]
    if knowledge.faqs:
        lines.append("FAQs set by the business (these override the reference sheet):")
        lines.extend(f"- Q: {faq.question} A: {faq.answer}" for faq in knowledge.faqs)
    if knowledge.digest_content:
        lines.extend(("Reference sheet:", knowledge.digest_content.strip()))
    lines.extend(
        (
            "- If the answer is not in this knowledge, say you don't have that information. "
            "Do not guess.",
            '- Never mention sources, URLs in brackets or "CONFLICT" to the caller; where '
            "facts conflict, say the team will confirm.",
            "- Keep answers to one or two short sentences, then return to the caller's enquiry.",
        )
    )
    return "\n".join(lines)
