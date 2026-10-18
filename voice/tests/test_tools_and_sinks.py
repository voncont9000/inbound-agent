import json
from datetime import date, timedelta

import pytest
from inbound_core.config import AppointmentType, BookingConfig

from inbound_voice.config import load_assistant_config
from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import DEFAULT_CONFIG_PATH, CallTools, tool_specs


def test_tool_specs_include_only_enabled_actions_and_no_save_intake() -> None:
    names = {spec["name"] for spec in tool_specs()}

    assert names == set()
    assert "save_intake" not in names


def test_tool_specs_omit_disabled_actions() -> None:
    assistant = load_assistant_config(DEFAULT_CONFIG_PATH).model_copy(
        update={"live_actions": ["confirm_critical_fields", "end_call"]}
    )

    assert {spec["name"] for spec in tool_specs(assistant)} == {
        "confirm_critical_fields",
        "end_call",
    }


def test_booking_tool_specs_require_config_route_availability_and_actions() -> None:
    assistant = load_assistant_config(DEFAULT_CONFIG_PATH).model_copy(
        update={
            "booking": BookingConfig(
                appointment_types=[
                    AppointmentType(
                        key="consultation",
                        label="Initial consultation",
                        duration_minutes=30,
                        when_to_use="For new callers",
                    )
                ]
            ),
            "live_actions": ["check_availability", "book_appointment"],
        }
    )

    assert tool_specs(assistant) == []
    specs = tool_specs(assistant, booking_available=True)
    assert {item["name"] for item in specs} == {
        "check_availability",
        "book_appointment",
    }
    assert all(
        "immediately without announcing" in item["description"] for item in specs
    )
    assert all("same response" not in item["description"] for item in specs)
    disabled = assistant.model_copy(update={"live_actions": []})
    assert tool_specs(disabled, booking_available=True) == []


@pytest.mark.asyncio
async def test_async_booking_dispatch_refuses_when_route_disables_booking(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "booking-disabled")
    assistant = load_assistant_config(DEFAULT_CONFIG_PATH).model_copy(
        update={"live_actions": ["check_availability", "book_appointment"]}
    )
    tools = CallTools(sink, assistant, booking_available=False)

    result = await tools.dispatch_async("check_availability", {"appointment_type": "consult"})

    assert result == {"ok": False, "error": "Appointment booking is not available."}


def test_critical_fields_are_validated_and_saved_live(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "live-intake")
    tools = CallTools(sink)

    rejected = tools.confirm_critical_fields("employment", {"issue_type": "dismissal"})
    name_result = tools.confirm_critical_fields("employment", {"caller_name": "Alex Smith"})
    phone_result = tools.confirm_critical_fields("employment", {"phone": "+44 7700 900123"})
    invalid_email = tools.confirm_critical_fields("employment", {"email": "invalid"})
    saved = json.loads((sink.directory / "intake.live.json").read_text())

    assert rejected == {
        "ok": False,
        "allowed_fields": [
            "caller_name",
            "phone",
            "email",
            "termination_date",
            "last_act_complained_of",
        ],
    }
    assert name_result["ok"]
    assert phone_result["ok"]
    assert invalid_email["ok"] is False
    assert saved["service"] == "employment"
    assert saved["fields"]["caller_name"]["value"] == "Alex Smith"
    assert saved["fields"]["caller_name"]["confirmed"] is True
    assert saved["fields"]["caller_name"]["confirmed_at"]
    assert saved["fields"]["phone"]["value"] == "+447700900123"


def test_dispatch_logs_enabled_actions_and_rejects_disabled_actions(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "logged-tools")
    tools = CallTools(sink)
    tools.config = tools.config.model_copy(
        update={"live_actions": ["confirm_critical_fields", "request_callback"]}
    )

    invalid = tools.dispatch("flag_urgent", {"reason": "Hearing today."})
    valid = tools.dispatch("request_callback", {"reason": "Needs a call."})
    entries = [
        json.loads(line) for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
    ]

    assert invalid["ok"] is False
    assert valid["ok"] is True
    assert [entry["name"] for entry in entries] == ["flag_urgent", "request_callback"]
    assert [entry["result"]["ok"] for entry in entries] == [False, True]
    task = json.loads((sink.directory / "tasks.json").read_text())[0]
    assert task["source"] == "live"


def test_deadline_rule_flags_urgent_once_per_rule_and_value(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "deadline")
    tools = CallTools(sink)
    termination_date = date.today() - timedelta(days=70)

    first = tools.confirm_critical_fields(
        "employment", {"termination_date": termination_date.isoformat()}
    )
    tools.confirm_critical_fields("employment", {"termination_date": termination_date.isoformat()})

    assert first["ok"] is True
    tasks = json.loads((sink.directory / "tasks.json").read_text())
    assert [task["type"] for task in tasks] == ["urgent"]
    assert tasks[0]["source"] == "live"


def test_flag_urgent_and_deadline_rule_deduplicate_same_date(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "deadline-dedup")
    tools = CallTools(sink)
    termination_date = date.today() - timedelta(days=70)
    reason = (
        f"Employment termination date provided as {termination_date.isoformat()}, "
        "checking proximity to tribunal time limits."
    )

    flagged = tools.flag_urgent(reason)
    confirmed = tools.confirm_critical_fields(
        "employment",
        {"termination_date": termination_date.isoformat()},
    )
    tasks = json.loads((sink.directory / "tasks.json").read_text())

    assert flagged["ok"] is True
    assert confirmed["ok"] is True
    assert len(tasks) == 1
    assert tasks[0]["rule"] == 1
    assert tasks[0]["value"] == termination_date.isoformat()
    assert tasks[0]["source"] == "live"


def test_keyword_and_deadline_rules_keep_independent_urgent_tasks(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "keyword-and-deadline")
    tools = CallTools(sink)
    termination_date = date.today() - timedelta(days=70)

    tools.check_caller_text("I have a hearing today.")
    tools.confirm_critical_fields(
        "employment",
        {"termination_date": termination_date.isoformat()},
    )
    tasks = json.loads((sink.directory / "tasks.json").read_text())

    assert len(tasks) == 2
    assert all(task["type"] == "urgent" for task in tasks)


def test_keyword_urgency_rule_flags_once_per_rule(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "keyword-urgency")
    tools = CallTools(sink)

    matches = tools.check_caller_text("I have a hearing today.")
    repeated = tools.check_caller_text("My hearing today is urgent.")
    tasks = json.loads((sink.directory / "tasks.json").read_text())

    assert matches
    assert repeated == []
    assert len(tasks) == 1
    assert tasks[0]["matched_phrase"] == "hearing today"
    assert tasks[0]["source"] == "live"
