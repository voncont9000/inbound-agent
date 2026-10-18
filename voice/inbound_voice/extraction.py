from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from inbound_voice.config import (
    AssistantConfig,
    DateDeadlineUrgencyRule,
    load_assistant_config,
)
from inbound_voice.intake import days_until_deadline
from inbound_voice.metrics import calculate_cost, load_price_table

VOICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml"
DEFAULT_EXTRACTION_MODEL = "gemini-3.8-flash"
EMPTY_CALLBACK_REQUEST = {"requested": False, "reason": None, "preferred_time": None}
EMPTY_COMPLAINT = {"made": False, "summary": None}


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
            "callback_requested": EMPTY_CALLBACK_REQUEST.copy(),
            "complaint": EMPTY_COMPLAINT.copy(),
            "unanswered_questions": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    try:
        prompt = _extraction_prompt(transcript_rows, live_intake, config)
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
        return {
            "status": "completed",
            "service": service_name,
            "model": model,
            "fields": fields,
            "callback_requested": _callback_request(parsed.get("callback_requested")),
            "complaint": _complaint(parsed.get("complaint")),
            "unanswered_questions": _unanswered_questions(parsed.get("unanswered_questions")),
            "usage": _usage(response),
        }
    except Exception as error:
        return {
            "status": "failed",
            "service": live_intake.get("service"),
            "model": model,
            "fields": {},
            "callback_requested": EMPTY_CALLBACK_REQUEST.copy(),
            "complaint": EMPTY_COMPLAINT.copy(),
            "unanswered_questions": [],
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
    if caller_id and phone_field:
        extracted_phone = extracted_fields.get(phone_field)
        if extracted_phone and extracted_phone.get("value"):
            caller_digits = re.sub(r"\D", "", caller_id)
            stated_digits = re.sub(r"\D", "", str(extracted_phone["value"]))
            if stated_digits != caller_digits:
                extracted_fields[phone_field] = {
                    **extracted_phone,
                    "alternatives": [caller_id],
                    "caller_id_alternative": True,
                }
        else:
            extracted_fields[phone_field] = {
                "value": caller_id,
                "confidence": 1.0,
                "evidence": "Caller ID",
                "confirmed_by_caller": False,
                "source": "caller_id",
            }
    post_call = {
        "service": service_name,
        "model": result.get("model", ""),
        "fields": extracted_fields,
        "callback_requested": _callback_request(result.get("callback_requested")),
        "complaint": _complaint(result.get("complaint")),
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
) -> list[dict[str, Any]]:
    tasks = existing_tasks or []
    generated = []
    service_name = merged.get("service")
    fields = merged.get("fields", {})
    for index, rule in enumerate(config.urgency_rules):
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
    }


def _complaint(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {"made": bool(value.get("made", False)), "summary": value.get("summary")}


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


async def reextract_run(
    run_dir: str | Path,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(run_dir)
    config = load_assistant_config(config_path or DEFAULT_CONFIG_PATH)
    transcript_rows = [
        json.loads(line)
        for line in (directory / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    live_path = directory / "intake.live.json"
    live_intake = (
        json.loads(live_path.read_text(encoding="utf-8"))
        if live_path.exists()
        else {"service": None, "fields": {}}
    )
    model = os.environ.get("EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL).strip()
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    client = None
    if api_key and model:
        from google import genai

        client = genai.Client(api_key=api_key)
    try:
        result = await extract_intake(transcript_rows, live_intake, config, client, model)
    finally:
        if client is not None:
            await client.aio.aclose()

    post_call, merged = build_intake_artifacts(live_intake, result, config)
    _write_json(directory / "intake.post_call.json", post_call)
    _write_json(directory / "intake.json", merged)
    tasks_path = directory / "tasks.json"
    tasks = json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else []
    tasks = [task for task in tasks if task.get("type") != "knowledge_gap"]
    _write_json(
        directory / "knowledge_gaps.json",
        {"unanswered_questions": result.get("unanswered_questions", [])},
    )
    tasks.extend(
        {"created_at": datetime.now(UTC).isoformat(), **task}
        for task in [
            *build_post_call_tasks(post_call, merged, config, tasks),
            *build_knowledge_gap_tasks(result.get("unanswered_questions", [])),
        ]
    )
    _write_json(tasks_path, tasks)

    metrics_path = directory / "metrics.json"
    estimated_cost = None
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        providers = metrics.setdefault("providers", {})
        providers["extraction_model"] = model
        providers["extraction_status"] = result["status"]
        usage = metrics.setdefault("usage", {})
        extraction_usage = result.get("usage", {})
        usage["extraction_input_tokens"] = extraction_usage.get("input_tokens", 0)
        usage["extraction_output_tokens"] = extraction_usage.get("output_tokens", 0)
        estimated_cost = calculate_cost(
            metrics,
            load_price_table(VOICE_ROOT / "config" / "prices.yaml"),
        )
        metrics["estimated_cost"] = estimated_cost
        _write_json(metrics_path, metrics)

    return {
        "run_dir": str(directory),
        "extraction_status": result["status"],
        "extraction_model": model,
        "extraction_error": result.get("error"),
        "estimated_cost": estimated_cost,
        "needs_review": merged["needs_review"],
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _service_model(service: Any) -> type[BaseModel]:
    from inbound_voice.intake import build_service_model

    return build_service_model(service)


def _extraction_schema(config: AssistantConfig) -> dict[str, Any]:
    field_names = sorted(
        {field.name for service in config.services.values() for field in service.fields}
    )
    return {
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
                },
                "required": ["requested", "reason", "preferred_time"],
                "additionalProperties": False,
            },
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
            "callback_requested",
            "complaint",
            "unanswered_questions",
        ],
        "additionalProperties": False,
    }


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
    unanswered_questions_instruction = (
        "List in unanswered_questions each question the caller asked that the assistant did not "
        "answer from business knowledge (it declined, said it would pass it on, or gave no "
        "answer). Evidence must be an exact quote from the caller."
    )
    callback_requested_instruction = (
        "Giving a name, phone number or email is not agreement to a callback. "
        "callback_requested is true only if the caller asks for a callback or answers yes to an "
        "offer of one. "
        "An assistant offer without caller agreement is false."
    )
    return f"""Extract intake facts and requested post-call actions from this call.
Use only facts stated by the caller. Return null for facts not stated. Dates must be ISO
YYYY-MM-DD using today's date ({date.today().isoformat()}) to resolve relative dates.
Evidence must be an exact quote from the caller's transcript.
For list fields such as key_dates, return a list of strings such as "2026-08-10: dismissed".
When the caller spells a value, preserve that spelling. For caller_name, when an email
local part clearly matches the caller's name, use its spelling (for example,
"siobhan.mcallister@" means "Siobhan McAllister").
Set confirmed_by_caller true only when the assistant read that value back and the caller
affirmed it. {callback_requested_instruction}
Include the reason and preferred time if stated. Set complaint.made only when the
caller made a complaint, and summarize it briefly.
{unanswered_questions_instruction}

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config")
    arguments = parser.parse_args()
    result = asyncio.run(reextract_run(arguments.run_dir, arguments.config))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["extraction_status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
