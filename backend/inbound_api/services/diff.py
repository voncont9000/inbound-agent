from typing import Any


def config_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}

    def visit(before: Any, after: Any, path: str) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in dict.fromkeys((*before.keys(), *after.keys())):
                child = f"{path}.{key}" if path else key
                visit(before.get(key), after.get(key), child)
        elif before != after:
            changes[path] = {"from": before, "to": after}

    visit(old, new, "")
    return changes
