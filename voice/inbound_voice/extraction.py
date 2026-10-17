from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError

from inbound_voice.config import AssistantConfig


async def extract_intake(
    transcript_rows: list[dict[str, Any]],
    live_intake: dict[str, Any],
    config: AssistantConfig,
    client: Any,
    model: str,
) -> dict[str, Any]:
    if client is None or not model.strip():
        return {
            "status": "skipped",
            "service": live_intake.get("service"),
            "model": model,
            "fields": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    try:
        prompt = _extraction_prompt(transcript_rows, live_intake, config)
        field_names = sorted(
            {field.name for service in config.services.values() for field in service.fields}
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "service": {"type": "STRING", "enum": list(config.services)},
                "fields": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING", "enum": field_names},
                            "value": {"type": "STRING", "nullable": True},
                            "confidence": {
                                "type": "NUMBER",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "evidence": {"type": "STRING"},
                        },
                        "required": ["name", "value", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["service", "fields"],
            "additionalProperties": False,
        }
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
                "thinking_config": {"thinking_level": "low"},
            },
        )
        parsed = json.loads(response.text)
        service_name = parsed["service"]
        service = config.services[service_name]
        service_model = _service_model(service)
        fields = {}
        for item in parsed.get("fields", []):
            name = item.get("name")
            if name not in service_model.model_fields:
                continue
            raw_value = item.get("value")
            confidence = _confidence(item.get("confidence"))
            invalid = False
            value = raw_value
            if raw_value is not None:
                try:
                    validated = service_model.model_validate({name: raw_value})
                    value = validated.model_dump(mode="json", exclude_unset=True)[name]
                except ValidationError:
                    confidence = 0.0
                    invalid = True
            fields[name] = {
                "value": value,
                "confidence": confidence,
                "evidence": str(item.get("evidence", "")),
                **({"invalid": True} if invalid else {}),
            }
        return {
            "status": "completed",
            "service": service_name,
            "model": model,
            "fields": fields,
            "usage": _usage(response),
        }
    except Exception as error:
        return {
            "status": "failed",
            "service": live_intake.get("service"),
            "model": model,
            "fields": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": f"{type(error).__name__}: {error}",
        }


def merge_intake(
    live: dict[str, Any],
    post_call: dict[str, Any],
    service_model: type[BaseModel],
    threshold: float,
) -> dict[str, Any]:
    service = live.get("service") or post_call.get("service")
    live_fields = live.get("fields", {}) if live.get("service") == service else {}
    post_fields = post_call.get("fields", {}) if post_call.get("service") == service else {}
    merged_fields = {}
    needs_review = []

    for name in dict.fromkeys((*live_fields, *post_fields)):
        live_entry = live_fields.get(name)
        post_entry = post_fields.get(name)
        live_value = live_entry.get("value") if live_entry else None
        post_value = post_entry.get("value") if post_entry else None
        has_live = live_entry is not None and live_value is not None
        has_post = post_entry is not None and post_value is not None
        if not has_live and not has_post:
            continue

        sources = {}
        if live_entry is not None:
            sources["live"] = {
                "value": live_value,
                "confirmed_at": live_entry.get("confirmed_at"),
            }
        if post_entry is not None:
            sources["post_call"] = {
                "value": post_value,
                "confidence": post_entry.get("confidence", 0),
                "evidence": post_entry.get("evidence", ""),
            }

        confidence = 0.0
        alternative = None
        invalid = bool(post_entry and post_entry.get("invalid"))
        if has_live and has_post:
            if _normalise(live_value, name, service_model) == _normalise(
                post_value, name, service_model
            ):
                value = live_value
                status = "agreed"
                confidence = max(float(post_entry.get("confidence", 0)), 0.95)
            elif live_entry.get("confirmed", False):
                value = live_value
                status = "conflict"
                confidence = 0.95
                alternative = post_value
            else:
                value = post_value
                status = "corrected"
                confidence = float(post_entry.get("confidence", 0))
                alternative = live_value
        elif has_live:
            value = live_value
            status = "live_only"
            confidence = 0.95
        else:
            value = post_value
            status = "filled"
            confidence = float(post_entry.get("confidence", 0))

        review = status == "conflict" or invalid or confidence < threshold
        field_result = {
            "value": value,
            "status": status,
            "confidence": confidence,
            "needs_review": review,
            "sources": sources,
        }
        if alternative is not None:
            field_result["alternative"] = alternative
        if invalid:
            field_result["invalid"] = True
        merged_fields[name] = field_result
        if review:
            needs_review.append(name)

    return {"service": service, "fields": merged_fields, "needs_review": needs_review}


def _service_model(service: Any) -> type[BaseModel]:
    from inbound_voice.intake import build_service_model

    return build_service_model(service)


def _extraction_prompt(
    transcript_rows: list[dict[str, Any]],
    live_intake: dict[str, Any],
    config: AssistantConfig,
) -> str:
    transcript = "\n".join(
        f"{index}. {row.get('role', 'unknown')}: {row.get('text', '')}"
        for index, row in enumerate(transcript_rows, start=1)
    )
    services = []
    for name, service in config.services.items():
        field_lines = []
        for field in service.fields:
            choices = f"; choices={field.choices}" if field.choices else ""
            field_lines.append(
                f"- {field.name} ({field.label}; type={field.type}{choices}): {field.description}"
            )
        services.append(f"{name} ({service.label}):\n" + "\n".join(field_lines))
    return f"""Extract intake facts from this call.
Use only facts stated by the caller. Return null for facts not stated. Dates must be ISO
YYYY-MM-DD using today's date ({date.today().isoformat()}) to resolve relative dates.
Evidence must be an exact quote from the caller's transcript.

Confirmed live values:
{json.dumps(live_intake, ensure_ascii=False)}

Configured services and fields:
{chr(10).join(services)}

Numbered transcript:
{transcript}
"""


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        return 0.0
    return confidence


def _usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    return {
        "input_tokens": int(
            getattr(metadata, "prompt_token_count", 0) or getattr(metadata, "input_tokens", 0) or 0
        ),
        "output_tokens": int(
            getattr(metadata, "candidates_token_count", 0)
            or getattr(metadata, "output_tokens", 0)
            or 0
        ),
    }


def _normalise(value: Any, name: str, model: type[BaseModel]) -> Any:
    field_info = model.model_fields.get(name)
    field_type = (field_info.json_schema_extra or {}).get("field_type") if field_info else None
    if field_type == "phone":
        return re.sub(r"\D", "", str(value))
    if field_type == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            return value
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return value
