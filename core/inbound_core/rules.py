from inbound_core.config import AssistantConfig


def render_conversation_rules(
    config: AssistantConfig,
    *,
    channel: str,
    handoff_available: bool = False,
) -> list[str]:
    audience = "caller" if channel == "phone" else "customer"
    rules = config.rules
    lines = []

    if channel == "phone":
        if rules.ai_disclosure == "at_start":
            lines.append(
                "- Disclose that you are an AI assistant at the start of the call; if the "
                "greeting already says so, do not repeat it. If asked, say honestly that you "
                "are an AI assistant."
            )
        else:
            lines.append(
                "- Do not volunteer that you are an AI assistant. If asked whether you are a "
                "person, say honestly that you are an AI assistant."
            )

    if rules.style == "formal":
        lines.append("- Use a formal, professional tone.")
    elif rules.style == "friendly":
        lines.append("- Use a warm, friendly tone while staying professional.")
    elif rules.style == "neutral":
        lines.append("- Use a neutral, matter-of-fact tone.")

    if rules.off_topic == "redirect":
        lines.append(
            f"- If the {audience} raises something unrelated to {config.business_name}, say "
            f"politely that you can only help with {config.business_name} enquiries and return "
            "to their enquiry."
        )
    elif rules.off_topic == "answer_briefly":
        lines.append(
            f"- If the {audience} asks something unrelated to {config.business_name}, answer in "
            "one short sentence if you can, without inventing business facts, then return to "
            "their enquiry."
        )

    if rules.angry_caller == "de_escalate":
        instruction = (
            f"- If the {audience} is angry or upset, stay calm, acknowledge their frustration, "
            "apologise for the inconvenience without admitting fault, and focus on the next step."
        )
        if channel == "phone" and handoff_available:
            instruction += (
                " If they ask for a person or stay upset and a transfer target below fits, "
                "offer the transfer."
            )
        lines.append(instruction)

    if rules.sales_calls == "decline":
        lines.append(
            f"- If the {audience} is selling something or calls from a competitor, decline "
            "politely and close the conversation."
        )
    elif rules.sales_calls == "take_message":
        lines.append(
            f"- If the {audience} is selling something or calls from a competitor, take their "
            "name, company and a one-line message for the team, then close politely."
        )

    return lines


def render_ai_disclosure(
    config: AssistantConfig,
    *,
    channel: str,
    first_reply: bool,
) -> str:
    if channel != "whatsapp":
        return ""

    if config.rules.ai_disclosure == "when_asked":
        return (
            "- Do not volunteer that you are an AI assistant. If asked whether you are a person, "
            f"say honestly that you are the AI assistant for {config.business_name}."
        )
    if first_reply:
        return (
            f"- In this first reply only, include one short clause disclosing that you are "
            f"the AI assistant for {config.business_name}, exactly once. Do not use the spoken "
            "call greeting."
        )
    return "- This is a later reply. Do not disclose again that you are an AI assistant."
