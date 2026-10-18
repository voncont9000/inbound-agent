from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inbound_core.call_report import CallReport, TranscriptTurn


def build_call_report(
    run_dir: Path,
    external_call_id: str,
    ended_at: datetime | None = None,
) -> CallReport:
    ended = ended_at or datetime.now(UTC)
    turns: list[TranscriptTurn] = []
    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.exists():
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            role = row.get("role")
            text = row.get("text")
            if role not in {"caller", "assistant"} or not isinstance(text, str) or not text.strip():
                continue
            timestamp = row.get("timestamp") or ended
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            turns.append(TranscriptTurn(role=role, text=text, timestamp=timestamp))

    live_intake = _read_json(run_dir / "intake.live.json", {})
    post_call_intake = _optional_json(run_dir / "intake.post_call.json")
    merged_intake = _read_json(run_dir / "intake.json", {})
    tasks = _read_json(run_dir / "tasks.json", [])
    gaps_data = _read_json(run_dir / "knowledge_gaps.json", {})
    gaps = gaps_data.get("unanswered_questions", []) if isinstance(gaps_data, dict) else []
    if not isinstance(gaps, list) or any(not isinstance(item, dict) for item in gaps):
        gaps = []
    metrics = _read_json(run_dir / "metrics.json", {})
    estimated_cost = metrics.pop("estimated_cost", None) or None
    end_reason = next(
        (
            task.get("source")
            for task in reversed(tasks)
            if isinstance(task, dict) and task.get("type") == "end_call"
        ),
        None,
    )
    return CallReport(
        external_call_id=external_call_id,
        ended_at=ended,
        end_reason=end_reason,
        transcript=turns,
        live_intake=live_intake,
        post_call_intake=post_call_intake,
        merged_intake=merged_intake,
        tasks=tasks,
        knowledge_gaps=gaps,
        metrics=metrics,
        estimated_cost=estimated_cost,
    )


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path, None)
