from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

from inbound_core.config import (
    AssistantConfig,
    DateDeadlineUrgencyRule,
    SentenceUrgencyRule,
)
from inbound_core.intake import days_until_deadline
from inbound_core.phone import normalise_phone

DEFAULT_EXTRACTION_MODEL = "gemini-3.8-flash"


EMPTY_CALLBACK_REQUEST = {
    "requested": False,
    "reason": None,
    "preferred_time": None,
    "preferred_time_iso": None,
}


EMPTY_COMPLAINT = {"made": False, "summary": None}


async def extract_intake(
    transcript_rows: list[dict[str, Any]],
    live_intake: dict[str, Any],
    config: AssistantConfig,
    client: Any,
    model: str,
    call_started_at: datetime | None = None,
    *,
    channel: str = "phone",
) -> dict[str, Any]:
    if client is None or not model.strip():
        return {
            "status": "skipped",
            "service": live_intake.get("service"),
            "model": model,
            "fields": {},
            "summary": None,
            "callback_requested": EMPTY_CALLBACK_REQUEST.copy(),
            "complaint": EMPTY_COMPLAINT.copy(),
            "unanswered_questions": [],
            "urgency_matches": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    try:
        prompt = _extraction_prompt(
            transcript_rows,
            live_intake,
            config,
            call_started_at,
            channel=channel,
        )
        schema = _extraction_schema(config)
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": schema,
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
                "confirmed_by_caller": bool(item.get("confirmed_by_caller", False)),
                **({"invalid": True} if invalid else {}),
            }
        sentence_rules = {
            index
            for index, rule in enumerate(config.urgency_rules)
            if isinstance(rule, SentenceUrgencyRule)
        }
        urgency_matches = []
        for item in parsed.get("urgency_matches", []):
            if not isinstance(item, dict):
                continue
            rule_index = item.get("rule")
            reason = item.get("reason")
            if (
                isinstance(rule_index, bool)
                or not isinstance(rule_index, int)
                or rule_index not in sentence_rules
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                continue
            evidence = item.get("evidence")
            urgency_matches.append(
                {
                    "rule": rule_index,
                    "reason": reason.strip(),
                    "evidence": evidence if isinstance(evidence, str) else "",
                }
            )
        return {
            "status": "completed",
            "service": service_name,
            "model": model,
            "summary": _summary(parsed.get("summary")),
            "fields": fields,
            "callback_requested": _callback_request(parsed.get("callback_requested")),
            "complaint": _complaint(parsed.get("complaint")),
            "unanswered_questions": _unanswered_questions(parsed.get("unanswered_questions")),
            "urgency_matches": urgency_matches,
            "usage": _usage(response),
        }
    except Exception as error:
        return {
            "status": "failed",
            "service": live_intake.get("service"),
            "model": model,
            "summary": None,
            "fields": {},
            "callback_requested": EMPTY_CALLBACK_REQUEST.copy(),
            "complaint": EMPTY_COMPLAINT.copy(),
            "unanswered_questions": [],
            "urgency_matches": [],
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
            source_name = post_entry.get("source", "post_call")
            sources[source_name] = {
                "value": post_value,
                "confidence": post_entry.get("confidence", 0),
                "evidence": post_entry.get("evidence", ""),
                "confirmed_by_caller": bool(post_entry.get("confirmed_by_caller", False)),
            }
            for alternative in post_entry.get("alternatives", []):
                sources["caller_id"] = {
                    "value": alternative,
                    "confidence": 1.0,
                    "evidence": "Caller ID",
                    "confirmed_by_caller": False,
                }

        confidence = 0.0
        alternative = None
        invalid = bool(post_entry and post_entry.get("invalid"))
        post_source = post_entry.get("source") if post_entry else None
        if has_live and has_post and post_source == "caller_id":
            if _normalise(live_value, name, service_model) == _normalise(
                post_value, name, service_model
            ):
                value = post_value
            else:
                value = live_value
                alternative = post_value
            status = "caller_id"
            confidence = 1.0
        elif has_live and has_post:
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
            status = (
                "caller_id"
                if post_source == "caller_id"
                else (
                    "filled_confirmed" if post_entry.get("confirmed_by_caller", False) else "filled"
                )
            )
            confidence = float(post_entry.get("confidence", 0))

        if post_entry and post_entry.get("caller_id_alternative"):
            status = "caller_id"

        review = status == "conflict" or invalid or confidence < threshold
        field_result = {
            "value": value,
            "status": status,
            "confidence": confidence,
            "needs_review": review,
            "confirmed_by_caller": bool(
                post_entry and post_entry.get("confirmed_by_caller", False)
            ),
            "sources": sources,
        }
        if alternative is not None:
            field_result["alternative"] = alternative
        if post_source == "caller_id" and has_live and alternative is not None:
            field_result["alternatives"] = [alternative]
        if post_entry and post_entry.get("alternatives"):
            field_result["alternatives"] = list(post_entry["alternatives"])
        if invalid:
            field_result["invalid"] = True
        merged_fields[name] = field_result
        if review:
            needs_review.append(name)

    return {"service": service, "fields": merged_fields, "needs_review": needs_review}


def build_intake_artifacts(
    live_intake: dict[str, Any],
    result: dict[str, Any],
    config: AssistantConfig,
    caller_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    service_name = result.get("service") or live_intake.get("service")
    extracted_fields = dict(result.get("fields", {}))
    service_config = config.services.get(service_name)
    phone_field = next(
        (
            field.name
            for field in (service_config.fields if service_config else [])
            if field.type == "phone" or field.name == "phone"
        ),
        None,
    )
    caller_number = normalise_phone(caller_id, strict=False)
    if caller_number and phone_field:
        extracted_phone = extracted_fields.get(phone_field)
        if extracted_phone and extracted_phone.get("value"):
            caller_digits = re.sub(r"\D", "", caller_number)
            stated_digits = re.sub(r"\D", "", str(extracted_phone["value"]))
            if stated_digits != caller_digits:
                extracted_fields[phone_field] = {
                    **extracted_phone,
                    "alternatives": [caller_number],
                    "caller_id_alternative": True,
                }
        else:
            extracted_fields[phone_field] = {
                "value": caller_number,
                "confidence": 1.0,
                "evidence": "Caller ID",
                "confirmed_by_caller": False,
                "source": "caller_id",
            }
    post_call = {
        "service": service_name,
        "model": result.get("model", ""),
        "summary": _summary(result.get("summary")),
        "fields": extracted_fields,
        "callback_requested": _callback_request(result.get("callback_requested")),
        "complaint": _complaint(result.get("complaint")),
        "urgency_matches": list(result.get("urgency_matches", [])),
    }
    service_model = (
        _service_model(config.services[service_name]) if service_name in config.services else None
    )
    merged = (
        merge_intake(
            live_intake,
            post_call,
            service_model,
            config.review_confidence_threshold,
        )
        if service_model is not None
        else {"service": service_name, "fields": {}, "needs_review": []}
    )
    merged.update(
        {
            "extraction_status": result["status"],
            "conflict_check": "pending",
            "versions": {"live": "intake.live.json", "post_call": "intake.post_call.json"},
        }
    )
    if result.get("error"):
        merged["extraction_error"] = result["error"]
    return post_call, merged


def build_post_call_tasks(
    post_call: dict[str, Any],
    merged: dict[str, Any],
    config: AssistantConfig,
    existing_tasks: list[dict[str, Any]] | None = None,
    *,
    call_ended_at: datetime | None = None,
) -> list[dict[str, Any]]:
    tasks = existing_tasks or []
    generated = []
    service_name = merged.get("service")
    fields = merged.get("fields", {})
    for index, rule in enumerate(config.urgency_rules):
        if isinstance(rule, SentenceUrgencyRule):
            for match in post_call.get("urgency_matches", []):
                rule_index = match.get("rule") if isinstance(match, dict) else None
                if (
                    isinstance(rule_index, bool)
                    or not isinstance(rule_index, int)
                    or rule_index != index
                    or not isinstance(match.get("reason"), str)
                    or not match["reason"].strip()
                    or any(
                        task.get("type") == "urgent" and task.get("rule") == index
                        for task in [*tasks, *generated]
                    )
                ):
                    continue
                generated.append(
                    {
                        "type": "urgent",
                        "reason": match["reason"],
                        "rule": index,
                        "rule_text": rule.text,
                        "evidence": match.get("evidence", ""),
                        "source": "post_call",
                    }
                )
            continue
        if not isinstance(rule, DateDeadlineUrgencyRule) or rule.service != service_name:
            continue
        entry = fields.get(rule.field, {})
        value = entry.get("value") if isinstance(entry, dict) else None
        if not value:
            continue
        try:
            days_left = days_until_deadline(
                date.fromisoformat(str(value)), rule.add_months, rule.minus_days
            )
        except ValueError:
            continue
        if days_left > rule.urgent_within_days:
            continue
        if any(
            task.get("type") == "urgent"
            and task.get("rule") == index
            and task.get("value") == value
            for task in [*tasks, *generated]
        ):
            continue
        generated.append(
            {
                "type": "urgent",
                "reason": f"{rule.reason} Calculated deadline is {days_left} days away.",
                "rule": index,
                "value": value,
                "source": "post_call",
            }
        )

    callback = _callback_request(post_call.get("callback_requested"))
    if callback["requested"] and not any(task.get("type") == "callback" for task in tasks):
        generated.append(
            {
                "type": "callback",
                "reason": callback["reason"] or "Caller requested a callback.",
                "preferred_time": callback["preferred_time"],
                "due_at": _callback_due_at(
                    callback.get("preferred_time_iso"),
                    config,
                    call_ended_at=call_ended_at,
                ),
                "source": "post_call",
            }
        )

    complaint = _complaint(post_call.get("complaint"))
    if complaint["made"] and not any(task.get("type") == "complaint" for task in tasks):
        generated.append(
            {
                "type": "complaint",
                "summary": complaint["summary"] or "Caller reported a complaint.",
                "source": "post_call",
            }
        )
    return generated


def _callback_request(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "requested": bool(value.get("requested", False)),
        "reason": value.get("reason"),
        "preferred_time": value.get("preferred_time"),
        "preferred_time_iso": value.get("preferred_time_iso"),
    }


def _complaint(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {"made": bool(value.get("made", False)), "summary": value.get("summary")}


def _summary(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    summary = " ".join(value.split())
    return summary or None


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _office_schedule(
    config: AssistantConfig,
) -> tuple[ZoneInfo, set[int], datetime_time, datetime_time] | None:
    office_hours = config.office_hours
    if not office_hours.days:
        return None
    try:
        timezone = ZoneInfo(office_hours.timezone or "Europe/London")
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Europe/London")
    try:
        opens = datetime_time.fromisoformat(office_hours.opens).replace(tzinfo=None)
        closes = datetime_time.fromisoformat(office_hours.closes).replace(tzinfo=None)
    except ValueError:
        return None
    day_names = {
        name.casefold(): index
        for index, name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        )
    }
    day_names.update({name[:3].casefold(): index for name, index in day_names.copy().items()})
    days = {
        day_names[day.strip().casefold()]
        for day in office_hours.days
        if day.strip().casefold() in day_names
    }
    if not days or opens >= closes:
        return None
    return timezone, days, opens, closes


def _is_during_office_hours(
    value: datetime,
    schedule: tuple[ZoneInfo, set[int], datetime_time, datetime_time],
) -> bool:
    timezone, days, opens, closes = schedule
    local = value.astimezone(timezone)
    local_time = local.timetz().replace(tzinfo=None)
    return local.weekday() in days and opens <= local_time < closes


def _next_office_opening(
    after: datetime,
    schedule: tuple[ZoneInfo, set[int], datetime_time, datetime_time],
) -> datetime | None:
    timezone, days, opens, _ = schedule
    local_after = after.astimezone(timezone)
    for offset in range(8):
        opening_date = local_after.date() + timedelta(days=offset)
        if opening_date.weekday() not in days:
            continue
        opening = datetime.combine(opening_date, opens, tzinfo=timezone)
        if opening > local_after:
            return opening
    return None


def _callback_due_at(
    preferred_time_iso: Any,
    config: AssistantConfig,
    *,
    call_ended_at: datetime | None,
) -> str | None:
    call_end = call_ended_at or datetime.now(UTC)
    if call_end.tzinfo is None or call_end.utcoffset() is None:
        call_end = call_end.replace(tzinfo=UTC)
    preferred = _parse_aware_datetime(preferred_time_iso)
    schedule = _office_schedule(config)
    if preferred is not None and call_end <= preferred <= call_end + timedelta(days=14):
        if schedule is None or _is_during_office_hours(preferred, schedule):
            return preferred.isoformat()
        opening = _next_office_opening(preferred, schedule)
        return opening.isoformat() if opening is not None else None
    if schedule is None:
        return None
    opening = _next_office_opening(call_end, schedule)
    return opening.isoformat() if opening is not None else None


def _unanswered_questions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"question": item["question"], "evidence": item["evidence"]}
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("question"), str)
        and item["question"].strip()
        and isinstance(item.get("evidence"), str)
    ]


def build_knowledge_gap_tasks(unanswered_questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "knowledge_gap",
            "question": item["question"],
            "evidence": item["evidence"],
            "source": "post_call",
        }
        for item in unanswered_questions
        if item.get("question") and item.get("evidence")
    ]


def _service_model(service: Any) -> type[BaseModel]:
    from inbound_core.intake import build_service_model

    return build_service_model(service)


def _extraction_schema(config: AssistantConfig) -> dict[str, Any]:
    field_names = sorted(
        {field.name for service in config.services.values() for field in service.fields}
    )
    schema = {
        "type": "object",
        "properties": {
            "service": {"type": "string", "enum": list(config.services)},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": field_names},
                        "value": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "null"},
                            ]
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence": {"type": "string"},
                        "confirmed_by_caller": {"type": "boolean"},
                    },
                    "required": [
                        "name",
                        "value",
                        "confidence",
                        "evidence",
                        "confirmed_by_caller",
                    ],
                    "additionalProperties": False,
                },
            },
            "callback_requested": {
                "type": "object",
                "properties": {
                    "requested": {"type": "boolean"},
                    "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "preferred_time": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "preferred_time_iso": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": [
                    "requested",
                    "reason",
                    "preferred_time",
                    "preferred_time_iso",
                ],
                "additionalProperties": False,
            },
            "summary": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "complaint": {
                "type": "object",
                "properties": {
                    "made": {"type": "boolean"},
                    "summary": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["made", "summary"],
                "additionalProperties": False,
            },
            "unanswered_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["question", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "service",
            "fields",
            "summary",
            "callback_requested",
            "complaint",
            "unanswered_questions",
        ],
        "additionalProperties": False,
    }
    if any(isinstance(rule, SentenceUrgencyRule) for rule in config.urgency_rules):
        schema["properties"]["urgency_matches"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule": {"type": "integer"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["rule", "reason", "evidence"],
                "additionalProperties": False,
            },
        }
        schema["required"].append("urgency_matches")
    return schema


def _extraction_prompt(
    transcript_rows: list[dict[str, Any]],
    live_intake: dict[str, Any],
    config: AssistantConfig,
    call_started_at: datetime | None = None,
    *,
    channel: str = "phone",
) -> str:
    schedule = _office_schedule(config)
    timezone = schedule[0] if schedule is not None else ZoneInfo("Europe/London")
    started_at = call_started_at or datetime.now(UTC)
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        started_at = started_at.replace(tzinfo=UTC)
    local_started_at = started_at.astimezone(timezone)
    office_hours = config.office_hours
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
    unanswered_questions_instruction = (
        "List in unanswered_questions each question the caller asked that the assistant did not "
        "answer from business knowledge (it declined, said it would pass it on, or gave no "
        "answer). Evidence must be an exact quote from the caller."
    )
    sentence_rules = [
        (index, rule)
        for index, rule in enumerate(config.urgency_rules)
        if isinstance(rule, SentenceUrgencyRule)
    ]
    urgency_instruction = ""
    if sentence_rules:
        urgency_instruction = (
            "Urgency rules (judge each against the conversation; use the local date above for "
            "time windows):\n"
            + "\n".join(f"{index}. {rule.text}" for index, rule in sentence_rules)
            + "\nList in urgency_matches each rule above that the conversation clearly meets, "
            f"with rule set to its number, a one-sentence reason citing the facts, and an exact "
            f"quote from the {'caller' if channel == 'phone' else 'customer'} as evidence. "
            "Return an empty list if none apply."
        )
    callback_requested_instruction = (
        "Giving a name, phone number or email is not agreement to a callback. "
        "callback_requested is true only if the caller asks for a callback or answers yes to an "
        "offer of one. "
        "An assistant offer without caller agreement is false. Keep preferred_time as the "
        "caller's original wording. Resolve a clear preferred time into preferred_time_iso as "
        "ISO 8601 with a UTC offset in the office-hours timezone; use null if it is unclear."
    )
    is_phone = channel == "phone"
    interaction = "call" if is_phone else "message thread"
    interaction_noun = "call" if is_phone else "conversation"
    speaker = "caller" if is_phone else "customer"
    transcript_source = "caller's transcript" if is_phone else "customer's messages"
    start_label = "Call" if is_phone else "Conversation"
    return f"""Extract intake facts and requested post-call actions from this {interaction}.
Use only facts stated by the {speaker}. Return null for facts not stated. Dates must be ISO
YYYY-MM-DD using the {interaction_noun}'s local date
({local_started_at.date().isoformat()}) to resolve relative dates.
Evidence must be an exact quote from the {transcript_source}.
{start_label} start: {local_started_at.isoformat()} ({timezone.key}).
Office hours: {", ".join(office_hours.days)} {office_hours.opens}-{office_hours.closes}
({office_hours.timezone}).
Return summary as one or two neutral sentences describing the enquiry, with no advice.
For list fields such as key_dates, return a list of strings such as "2026-08-10: dismissed".
When the caller spells a value, preserve that spelling. For caller_name, when an email
local part clearly matches the caller's name, use its spelling (for example,
"siobhan.mcallister@" means "Siobhan McAllister").
Set confirmed_by_caller true only when the assistant read that value back and the caller
affirmed it. {callback_requested_instruction}
Include the reason and preferred time if stated. Set complaint.made only when the
caller made a complaint, and summarize it briefly.
{unanswered_questions_instruction}
{urgency_instruction}

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
