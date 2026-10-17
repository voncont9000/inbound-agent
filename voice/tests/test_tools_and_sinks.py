import json
from datetime import date, timedelta

from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools, tool_specs


def test_save_intake_tool_describes_preset_fields_and_choices() -> None:
    specification = next(spec for spec in tool_specs() if spec["name"] == "save_intake")
    description = specification["properties"]["fields"]["description"]

    assert "termination_date: date" in description
    assert "acas_early_conciliation_started: boolean" in description
    assert "postcode: string" in description
    assert "'redundancy'" in description
    assert "Dates must use ISO YYYY-MM-DD" in description
    assert "Fields merge across calls" in description


def test_tool_handlers_write_call_files(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "test-call")
    assert {path.name for path in sink.directory.iterdir()} == {
        "transcript.jsonl",
        "tool_calls.jsonl",
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


def test_dispatch_logs_invalid_and_valid_save_intake_calls(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "logged-tool-calls")
    tools = CallTools(sink)

    invalid = tools.dispatch(
        "save_intake", {"practice_area": "employment", "fields": {"issue_type": "wrong"}}
    )
    valid = tools.dispatch(
        "save_intake", {"practice_area": "employment", "fields": {"issue_type": "dismissal"}}
    )

    entries = [
        json.loads(line) for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
    ]
    assert len(entries) == 2
    assert [entry["name"] for entry in entries] == ["save_intake", "save_intake"]
    assert entries[0]["arguments"]["fields"]["issue_type"] == "wrong"
    assert entries[1]["arguments"]["fields"]["issue_type"] == "dismissal"
    assert [entry["result"]["ok"] for entry in entries] == [False, True]
    assert invalid["ok"] is False
    assert valid["ok"] is True
    assert all("timestamp" in entry for entry in entries)


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
    assert [task["type"] for task in tasks] == ["urgent"]


def test_changed_termination_date_flags_new_urgent_deadline(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "changed-deadline")
    tools = CallTools(sink)
    termination_date = date.today() - timedelta(days=70)

    tools.save_intake(
        "employment",
        {"issue_type": "dismissal", "termination_date": termination_date.isoformat()},
    )
    tools.save_intake(
        "employment",
        {"termination_date": (termination_date + timedelta(days=1)).isoformat()},
    )

    tasks = json.loads((sink.directory / "tasks.json").read_text())
    assert [task["type"] for task in tasks] == ["urgent", "urgent"]
