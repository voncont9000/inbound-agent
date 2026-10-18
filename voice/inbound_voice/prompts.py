from datetime import date, datetime

from inbound_core.config import AssistantConfig
from inbound_core.prompts import render_business_context, render_deadline_instructions
from inbound_core.transfer import available_transfer_targets

EMAIL_SPELLING_RULE = (
    "- When spelling an email address back, write it as separate letters and spoken words, "
    'with no hyphens, full stops or @ symbols: say "dot", "at", "underscore" and "dash" '
    "as words, and say common domains as words. For example, jane.mcallister@gmail.com is "
    '"J, A, N, E, dot, M, C, A, L, L, I, S, T, E, R, at gmail dot com".'
)


def render_system_prompt(
    config: AssistantConfig,
    today: date | None = None,
    caller_id: str | None = None,
    context_card: str | None = None,
    now: datetime | None = None,
) -> str:
    current_date = today or date.today()
    business_context = render_business_context(
        config,
        current_date,
        context_card,
        channel="phone",
        persona=(
            f"You are the telephone assistant for {config.business_name}.\nSpeak {config.language}."
        ),
        include_deadline_instructions=False,
    )
    readback_instructions = {
        "off": "Do not read details back to the caller.",
        "email": (
            "When the caller gives an email address, spell it back once, letter by letter, "
            "and ask them to confirm. Do not read back other details.\n" + EMAIL_SPELLING_RULE
        ),
        "critical": (
            "Ask for each critical field at a natural point. If the caller does not answer, "
            "acknowledge it, move on, and return to it once before the end.\n"
            "- Read back names, phone numbers, email addresses and dates to confirm them.\n"
            "- When the caller wants to finish or says goodbye, read back all collected, "
            "unconfirmed critical details in one sentence, ask the caller to confirm them, and "
            "wait for confirmation. Do not invent values for details the caller has not provided.\n"
            + EMAIL_SPELLING_RULE
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
        if action not in {"end_call", "transfer_call"}
    ]
    enabled_actions = "\n".join(
        f"- Enabled action: {action}."
        for action in config.live_actions
        if action != "transfer_call"
    )
    deadline_instructions = render_deadline_instructions(config, channel="phone")
    transfer_targets = available_transfer_targets(config, now)
    transfer_instructions = ""
    if transfer_targets:
        transfer_instructions = (
            "\n\nTRANSFER TO A PERSON\n"
            "Only offer or start a transfer when the business instructions above allow it and "
            "the caller's situation matches one of these targets:\n"
            + "\n".join(
                f"- {target.label} (target_key: {target.key}): {target.when_to_use}"
                for target in transfer_targets
            )
            + "\n- When a target matches and a transfer is appropriate, call "
            "transfer_call straight away without announcing the transfer; "
            "the system will tell the caller."
            "\n- If no target matches, do not mention transfers."
        )
    return f"""{business_context}

CALL BEHAVIOUR MECHANICS
{readback_instructions}
{caller_id_instruction}
{chr(10).join(deadline_instructions)}
{end_call_instruction}
{chr(10).join(other_enabled_actions)}

{enabled_actions}
{transfer_instructions}"""
