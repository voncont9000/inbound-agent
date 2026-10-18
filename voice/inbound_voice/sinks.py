import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class CallSink(Protocol):
    call_id: str

    def append_transcript(self, role: str, text: str, timestamp: str | None = None) -> None: ...

    def append_tool_call(
        self, name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> None: ...

    def save_intake(self, intake: dict[str, Any]) -> None: ...

    def save_live_intake(self, intake: dict[str, Any]) -> None: ...

    def save_post_call_intake(self, intake: dict[str, Any]) -> None: ...

    def save_knowledge_gaps(self, gaps: dict[str, Any]) -> None: ...

    def append_task(self, task_type: str, details: dict[str, Any]) -> None: ...

    def save_metrics(self, metrics: dict[str, Any]) -> None: ...


class LocalFilesCallSink:
    def __init__(self, runs_dir: str | Path, call_id: str) -> None:
        self.call_id = call_id
        self.directory = Path(runs_dir) / call_id
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "transcript.jsonl").touch(exist_ok=True)
        (self.directory / "tool_calls.jsonl").touch(exist_ok=True)
        self._write_json(
            "intake.json",
            {
                "service": None,
                "fields": {},
                "needs_review": [],
                "extraction_status": "pending",
                "conflict_check": "pending",
                "versions": {
                    "live": "intake.live.json",
                    "post_call": "intake.post_call.json",
                },
            },
        )
        self._write_json("tasks.json", [])
        self._write_json("metrics.json", {})

    def append_transcript(self, role: str, text: str, timestamp: str | None = None) -> None:
        entry = {
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
            "role": role,
            "text": text,
        }
        with (self.directory / "transcript.jsonl").open("a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def append_tool_call(
        self, name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "name": name,
            "arguments": arguments,
            "result": result,
        }
        with (self.directory / "tool_calls.jsonl").open("a", encoding="utf-8") as tool_calls:
            tool_calls.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._write_json("intake.json", intake)

    def save_live_intake(self, intake: dict[str, Any]) -> None:
        self._write_json("intake.live.json", intake)

    def save_post_call_intake(self, intake: dict[str, Any]) -> None:
        self._write_json("intake.post_call.json", intake)

    def save_knowledge_gaps(self, gaps: dict[str, Any]) -> None:
        self._write_json("knowledge_gaps.json", gaps)

    def append_task(self, task_type: str, details: dict[str, Any]) -> None:
        path = self.directory / "tasks.json"
        tasks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        tasks.append(
            {
                "type": task_type,
                "created_at": datetime.now(UTC).isoformat(),
                **details,
            }
        )
        self._write_json("tasks.json", tasks)

    def save_metrics(self, metrics: dict[str, Any]) -> None:
        self._write_json("metrics.json", metrics)

    def _write_json(self, filename: str, data: Any) -> None:
        (self.directory / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
