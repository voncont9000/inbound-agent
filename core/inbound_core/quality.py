"""Conversation quality scoring: rubric, judge schema and objective metrics."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

JUDGE_VERSION = "v3"
DEFAULT_QUALITY_JUDGE_MODEL = "gemini-3.5-flash-lite"
MAX_ISSUES = 5
MAX_INSTRUCTIONS_CHARS = 12_000
MAX_TRANSCRIPT_CHARS = 40_000
SLOW_RESPONSE_SECONDS = 3.0

CHECK_KEYS = (
    "answered_question",
    "collected_required_details",
    "language_consistent",
    "no_unsupported_promises",
    "ended_properly",
    "followed_instructions",
)

OBJECTIVE_KEYS = (
    "response_p50_s",
    "response_p95_s",
    "max_response_s",
    "interruptions",
    "turns",
    "call_duration_s",
    "tts_chars",
)

RUBRIC_PROMPT = """You review one conversation between a business's AI assistant and a customer. Judge only what the transcript shows. For each check return passed=true, passed=false, or passed=null when the check does not apply, plus a one-sentence note when passed is false.
- answered_question: the assistant answered or correctly routed every question the customer asked.
- collected_required_details: the assistant collected the details its instructions require for this kind of request, or the customer declined.
- language_consistent: the assistant replied in the customer's language throughout.
- no_unsupported_promises: the assistant made no commitment (prices, times, outcomes, availability) that its instructions or knowledge do not support.
- ended_properly: the conversation ended politely with a clear next step, or the customer ended it.
- followed_instructions: the assistant followed its business instructions and policies.
Lines marked "platform" are automatic notices added by the system (for example a recording notice), not said by the assistant; they never count against any check.
overall: 1 (bad) to 5 (excellent) for how well a real customer was served. issues: up to 5 short phrases describing concrete problems, empty if none."""  # noqa: E501


def judge_schema() -> dict[str, Any]:
    check = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": list(CHECK_KEYS)},
            "passed": {"type": ["boolean", "null"]},
            "note": {"type": ["string", "null"]},
        },
        "required": ["key", "passed", "note"],
    }
    return {
        "type": "object",
        "properties": {
            "checks": {"type": "array", "items": check},
            "overall": {"type": "integer", "minimum": 1, "maximum": 5},
            "issues": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ISSUES},
        },
        "required": ["checks", "overall", "issues"],
    }


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def split_platform_notices(
    rows: list[dict[str, Any]], notices: Iterable[str]
) -> list[dict[str, Any]]:
    """Label matching automatic notices separately from assistant transcript text."""
    notice_texts = sorted(
        {notice.strip() for notice in notices if isinstance(notice, str) and notice.strip()},
        key=lambda notice: (-len(notice), notice),
    )
    split_rows: list[dict[str, Any]] = []
    for row in rows:
        text = row.get("text")
        if row.get("role") != "assistant" or not isinstance(text, str):
            split_rows.append(row)
            continue

        cursor = 0
        row_segments: list[dict[str, Any]] = []
        while cursor < len(text):
            matches = []
            for notice in notice_texts:
                position = text.find(notice, cursor)
                if position >= 0:
                    matches.append((position, notice))
            if not matches:
                break

            position, notice = min(matches, key=lambda match: (match[0], -len(match[1]), match[1]))
            assistant_text = text[cursor:position].strip()
            if assistant_text:
                row_segments.append({"role": "assistant", "text": assistant_text})
            row_segments.append({"role": "platform", "text": notice})
            cursor = position + len(notice)

        if not row_segments:
            split_rows.append(row)
            continue

        assistant_text = text[cursor:].strip()
        if assistant_text:
            row_segments.append({"role": "assistant", "text": assistant_text})
        split_rows.extend(row_segments)
    return split_rows


def build_judge_prompt(
    *,
    instructions: str,
    transcript_rows: list[dict[str, Any]],
    intake: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    channel: str,
) -> str:
    """Assemble the judge input from assistant, caller and platform transcript rows."""
    transcript = "\n".join(
        f"{index}. {row.get('role', 'unknown')}: {row.get('text', '')}"
        for index, row in enumerate(transcript_rows, start=1)
    )
    sections = [
        RUBRIC_PROMPT,
        f"CHANNEL: {channel}",
        "ASSISTANT INSTRUCTIONS AND POLICIES:\n"
        + _truncate(instructions.strip() or "(none)", MAX_INSTRUCTIONS_CHARS),
        "TRANSCRIPT:\n" + _truncate(transcript or "(empty)", MAX_TRANSCRIPT_CHARS),
        "EXTRACTED DETAILS:\n" + json.dumps(intake or {}, ensure_ascii=False, default=str),
        "TASKS CREATED:\n" + json.dumps(tasks, ensure_ascii=False, default=str),
    ]
    return "\n\n".join(sections)


def normalize_judgement(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce judge output to the stored shape: every check key once, overall 1-5, <=5 issues."""
    by_key: dict[str, dict[str, Any]] = {}
    for item in raw.get("checks") or []:
        if not isinstance(item, dict) or item.get("key") not in CHECK_KEYS:
            continue
        passed = item.get("passed")
        note = item.get("note")
        by_key.setdefault(
            item["key"],
            {
                "key": item["key"],
                "passed": passed if isinstance(passed, bool) else None,
                "note": note.strip() if isinstance(note, str) and note.strip() else None,
            },
        )
    checks = [by_key.get(key, {"key": key, "passed": None, "note": None}) for key in CHECK_KEYS]
    try:
        overall = int(raw.get("overall"))
    except (TypeError, ValueError):
        raise ValueError("Judge returned no overall score") from None
    overall = min(5, max(1, overall))
    issues = [
        issue.strip()
        for issue in raw.get("issues") or []
        if isinstance(issue, str) and issue.strip()
    ][:MAX_ISSUES]
    return {"checks": checks, "overall": overall, "issues": issues}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def objective_metrics(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """Objective voice metrics from conversations.metrics; None without voice latency data."""
    metrics = metrics or {}
    summary = metrics.get("turn_latency_summary")
    if not isinstance(summary, dict):
        return None
    turns = metrics.get("turns")
    usage = metrics.get("usage") if isinstance(metrics.get("usage"), dict) else {}
    return {
        "response_p50_s": _number(summary.get("p50_seconds")),
        "response_p95_s": _number(summary.get("p95_seconds")),
        "max_response_s": _number(summary.get("max_seconds")),
        "interruptions": _count(metrics.get("interruptions")),
        "turns": len(turns) if isinstance(turns, list) else _count(summary.get("turn_count")),
        "call_duration_s": _number(metrics.get("call_duration_seconds")),
        "tts_chars": _count(usage.get("tts_characters")),
    }


def needs_review(
    overall: int, checks: list[dict[str, Any]], objective: dict[str, Any] | None
) -> bool:
    if overall <= 3 or any(check.get("passed") is False for check in checks):
        return True
    max_response = (objective or {}).get("max_response_s")
    return max_response is not None and max_response > SLOW_RESPONSE_SECONDS


async def judge_conversation(client: Any, model: str, prompt: str) -> dict[str, Any]:
    """Run the judge with JSON-schema constrained output and return the normalized judgement."""
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": judge_schema(),
            "temperature": 0,
        },
    )
    return normalize_judgement(json.loads(response.text))
