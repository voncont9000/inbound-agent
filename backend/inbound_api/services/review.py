from typing import Any


def field_needs_review(field: dict[str, Any], threshold: float) -> bool:
    confidence = field.get("confidence")
    return field.get("status") != "human" and (
        field.get("needs_review") is True
        or (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and confidence < threshold
        )
    )


def merged_needs_review(data: dict[str, Any], threshold: float) -> bool:
    fields = data.get("fields")
    return isinstance(fields, dict) and any(
        isinstance(field, dict) and field_needs_review(field, threshold)
        for field in fields.values()
    )
