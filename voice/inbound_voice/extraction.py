from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inbound_core.config import load_assistant_config
from inbound_core.extraction import (
    DEFAULT_EXTRACTION_MODEL,
    EMPTY_CALLBACK_REQUEST,
    EMPTY_COMPLAINT,
    _callback_due_at,
    _callback_request,
    _complaint,
    _confidence,
    _extraction_prompt,
    _extraction_schema,
    _is_during_office_hours,
    _next_office_opening,
    _normalise,
    _office_schedule,
    _parse_aware_datetime,
    _service_model,
    _summary,
    _unanswered_questions,
    _usage,
    build_intake_artifacts,
    build_knowledge_gap_tasks,
    build_post_call_tasks,
    extract_intake,
    merge_intake,
)
from inbound_core.pricing import calculate_cost, load_price_table

__all__ = [
    "DEFAULT_EXTRACTION_MODEL",
    "EMPTY_CALLBACK_REQUEST",
    "EMPTY_COMPLAINT",
    "_callback_due_at",
    "_callback_request",
    "_complaint",
    "_confidence",
    "_extraction_prompt",
    "_extraction_schema",
    "_is_during_office_hours",
    "_next_office_opening",
    "_normalise",
    "_office_schedule",
    "_parse_aware_datetime",
    "_service_model",
    "_summary",
    "_unanswered_questions",
    "_usage",
    "build_intake_artifacts",
    "build_knowledge_gap_tasks",
    "build_post_call_tasks",
    "extract_intake",
    "merge_intake",
]

VOICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml"


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
    call_times = _read_call_times(directory / "call_times.json")
    call_started_at = _parse_aware_datetime(call_times.get("started_at"))
    call_ended_at = _parse_aware_datetime(call_times.get("ended_at"))
    try:
        result = await extract_intake(
            transcript_rows,
            live_intake,
            config,
            client,
            model,
            call_started_at=call_started_at,
        )
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
            *build_post_call_tasks(
                post_call,
                merged,
                config,
                tasks,
                call_ended_at=call_ended_at,
            ),
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


def _read_call_times(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


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
