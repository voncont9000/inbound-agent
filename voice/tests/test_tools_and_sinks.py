import json
from datetime import date, timedelta

from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools


def test_tool_handlers_write_call_files(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "test-call")
    assert {path.name for path in sink.directory.iterdir()} == {
        "transcript.jsonl",
        "intake.json",
        "tasks.json",
        "metrics.json",
    }
    tools = CallTools(sink)
    sink.append_transcript("caller", "I need help with a purchase.")

    result = tools.save_intake(
        "conveyancing",
        {
            "caller_name": "Alex",
            "transaction_type": "purchase",
            "other_parties": ["Taylor Seller"],
        },
    )
    tools.flag_urgent("Hearing today.")
    tools.log_complaint("Caller unhappy about delay.")
    tools.request_callback("Needs solicitor advice.", "Tomorrow morning")
    tools.end_call()

    assert result["ok"] is True
    intake = json.loads((sink.directory / "intake.json").read_text())
    assert intake["conflict_check"] == "pending"
    assert intake["fields"]["other_parties"] == ["Taylor Seller"]
    tasks = json.loads((sink.directory / "tasks.json").read_text())
    assert [task["type"] for task in tasks] == [
        "urgent",
        "complaint",
        "callback",
        "end_call",
    ]
    transcript = (sink.directory / "transcript.jsonl").read_text().splitlines()
    assert json.loads(transcript[0])["text"] == "I need help with a purchase."


def test_invalid_intake_errors_are_returned_for_reask(tmp_path) -> None:
    tools = CallTools(LocalFilesCallSink(tmp_path, "bad-intake"))

    result = tools.save_intake("employment", {"issue_type": "wrong", "unexpected": 1})

    assert result["ok"] is False
    assert len(result["errors"]) >= 2


def test_employment_deadline_near_limit_flags_urgent(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "deadline")
    tools = CallTools(sink)
    termination_date = date.today() - timedelta(days=70)

    result = tools.save_intake(
        "employment",
        {"issue_type": "dismissal", "termination_date": termination_date.isoformat()},
    )
    follow_up = tools.save_intake("employment", {"employer_name": "Example Ltd"})

    assert result["ok"] is True
    assert follow_up["ok"] is True
    tasks = json.loads((sink.directory / "tasks.json").read_text())
    assert tasks[0]["type"] == "urgent"
