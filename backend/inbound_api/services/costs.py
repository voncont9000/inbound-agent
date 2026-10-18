from typing import Any

from inbound_api.models import Conversation


def add_conversation_cost(conversation: Conversation, cost: Any) -> None:
    """Accumulate a per-reply cost estimate (``inbound_core.pricing.calculate_cost``) on the
    conversation, so text channels report a per-conversation cost like voice calls do."""
    if not isinstance(cost, dict) or not isinstance(cost.get("total"), int | float):
        return
    current = conversation.estimated_cost if isinstance(conversation.estimated_cost, dict) else {}
    components = dict(current.get("components") or {})
    for name, value in (cost.get("components") or {}).items():
        if isinstance(value, int | float):
            previous = components.get(name)
            components[name] = (previous if isinstance(previous, int | float) else 0.0) + value
    unknown = sorted(
        set(current.get("unknown_components") or []) | set(cost.get("unknown_components") or [])
    )
    previous_total = current.get("total")
    conversation.estimated_cost = {
        "currency": current.get("currency") or cost.get("currency") or "USD",
        "components": components,
        "total": round(
            (previous_total if isinstance(previous_total, int | float) else 0.0) + cost["total"],
            8,
        ),
        "unknown_components": unknown,
    }
