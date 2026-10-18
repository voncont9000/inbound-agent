import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import aiohttp
from inbound_core.config import OfficeHours, RulesConfig, TransferTarget

from bot import complete_transfer
from inbound_voice.config import load_assistant_config
from inbound_voice.metrics import CallMetricsObserver
from inbound_voice.prompts import render_system_prompt
from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools, tool_specs
from inbound_voice.transfer import (
    FakeTwilioCallControl,
    RestTwilioCallControl,
    SimulatedCallTransfer,
    TransferRegistry,
    TransferStart,
    TwilioCallTransfer,
    build_transfer_failed_twiml,
    build_transfer_twiml,
    call_control_from_env,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 6, 8, 30, tzinfo=UTC)
TARGET = TransferTarget(
    key="duty_solicitor",
    label="Duty solicitor",
    phone="+447700900123",
    when_to_use="The caller explicitly asks to speak to a person now.",
    office_hours_only=True,
)


def assistant_config(*, enabled: bool = True, targets: bool = True):
    config = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    return config.model_copy(
        update={
            "live_actions": ["transfer_call"] if enabled else [],
            "transfer_targets": [TARGET] if targets else [],
            "office_hours": OfficeHours(
                days=["Monday"],
                opens="09:00",
                closes="17:00",
                timezone="Europe/London",
            ),
        }
    )


def test_transfer_tool_is_hidden_when_disabled_empty_or_outside_hours() -> None:
    enabled_config = assistant_config()
    closed_at = datetime(2026, 7, 6, 16, 30, tzinfo=UTC)

    assert tool_specs(assistant_config(enabled=False), now=NOW) == []
    assert tool_specs(assistant_config(targets=False), now=NOW) == []
    assert tool_specs(enabled_config, now=closed_at) == []


def test_transfer_tool_exposes_only_available_target_keys() -> None:
    config = assistant_config()
    config.transfer_targets.append(
        TransferTarget(
            key="weekend_duty",
            label="Weekend duty",
            phone="+447700900124",
            when_to_use="The caller needs weekend support.",
            office_hours_only=False,
        )
    )

    spec = next(spec for spec in tool_specs(config, now=NOW) if spec["name"] == "transfer_call")

    assert spec["properties"]["target_key"]["enum"] == ["duty_solicitor", "weekend_duty"]
    assert spec["required"] == ["target_key", "reason"]
    assert "without announcing the transfer" in spec["description"]
    assert "the system will tell the caller" in spec["description"]


def test_call_tools_validates_and_allows_only_one_pending_transfer(tmp_path) -> None:
    tools = CallTools(
        LocalFilesCallSink(tmp_path, "transfer-tool"),
        assistant_config(),
        now=NOW,
    )

    assert tools.transfer_call("unknown", "Caller requests a person.") == {
        "ok": False,
        "error": "Unknown transfer target.",
    }
    assert tools.transfer_call("duty_solicitor", "Caller requests a person.") == {
        "ok": True,
        "status": "Transferring the caller.",
    }
    assert tools.transfer_call("duty_solicitor", "Caller requests a person.") == {
        "ok": False,
        "error": "A transfer is already pending.",
    }
    assert tools.transfer_request is not None
    assert tools.transfer_request.target == TARGET


def test_prompt_mentions_transfer_only_when_a_target_is_available() -> None:
    hidden = render_system_prompt(assistant_config(targets=False), now=NOW)
    available = render_system_prompt(assistant_config(), now=NOW)

    assert "transfer_call" not in hidden
    assert "TRANSFER TO A PERSON" not in hidden
    assert "TRANSFER TO A PERSON" in available
    assert "- Duty solicitor (target_key: duty_solicitor): " in available
    assert TARGET.when_to_use in available
    assert "call transfer_call straight away without announcing the transfer" in available
    assert "the system will tell the caller" in available
    assert "say one short sentence" not in available.lower()


def test_angry_caller_transfer_offer_requires_an_enabled_available_target() -> None:
    rules = RulesConfig(angry_caller="de_escalate")
    unavailable = assistant_config(enabled=False, targets=False).model_copy(update={"rules": rules})
    available = assistant_config().model_copy(update={"rules": rules})

    unavailable_prompt = render_system_prompt(unavailable, now=NOW)
    available_prompt = render_system_prompt(available, now=NOW)

    assert "If the caller is angry or upset" in unavailable_prompt
    assert "offer the transfer" not in unavailable_prompt
    assert "If the caller is angry or upset" in available_prompt
    assert "offer the transfer" in available_prompt


def test_transfer_completion_records_failed_dial_and_metrics(tmp_path) -> None:
    class FailedTransfer:
        async def start(self, target, reason):
            assert target == TARGET
            assert reason == "Caller requests a person."
            return TransferStart("dialling")

        async def outcome(self, timeout):
            assert timeout == 4
            return "no-answer"

        def prepare(self):
            pass

    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "failed-transfer")
        metrics = CallMetricsObserver(sink, telephony=True)
        tools = CallTools(sink, assistant_config(), now=NOW)
        tools.transfer_call("duty_solicitor", "Caller requests a person.")

        result = await complete_transfer(
            sink,
            metrics,
            tools,
            FailedTransfer(),
            {"TRANSFER_OUTCOME_WAIT_SECS": "4"},
        )

        assert result is not None
        assert result["target_key"] == "duty_solicitor"
        assert result["status"] == "no-answer"
        assert json.loads((sink.directory / "metrics.json").read_text())["transfer"] == result
        tasks = json.loads((sink.directory / "tasks.json").read_text())
        assert any(task["type"] == "end_call" and task["source"] == "transferred" for task in tasks)
        assert any(
            task["type"] == "transfer_failed"
            and task["dial_status"] == "no-answer"
            and task["target_key"] == "duty_solicitor"
            for task in tasks
        )

    asyncio.run(run())


def test_transfer_completion_reuses_start_from_tool_handler(tmp_path) -> None:
    class StoredTransfer:
        async def start(self, target, reason):
            raise AssertionError("A started transfer must not be started again.")

        async def outcome(self, timeout):
            assert timeout == 4
            return "answered"

        def prepare(self):
            pass

    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "stored-transfer")
        metrics = CallMetricsObserver(sink, telephony=True)
        tools = CallTools(sink, assistant_config(), now=NOW)
        tools.transfer_call("duty_solicitor", "Caller requests a person.")
        tools.transfer_start = TransferStart("dialling")
        tools.transfer_started_at = time.monotonic()

        result = await complete_transfer(
            sink,
            metrics,
            tools,
            StoredTransfer(),
            {"TRANSFER_OUTCOME_WAIT_SECS": "4"},
        )

        assert result is not None
        assert result["status"] == "answered"
        assert result["latency_ms"] >= 0

    asyncio.run(run())


def test_simulated_transfer_records_transferred_end_and_simulated_status(tmp_path) -> None:
    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "simulated-transfer")
        metrics = CallMetricsObserver(sink, telephony=False)
        tools = CallTools(sink, assistant_config(), now=NOW)
        tools.transfer_call("duty_solicitor", "Caller requests a person.")

        result = await complete_transfer(
            sink,
            metrics,
            tools,
            SimulatedCallTransfer(),
            {},
        )

        assert result is not None
        assert result["status"] == "simulated"
        tasks = json.loads((sink.directory / "tasks.json").read_text())
        assert any(task["type"] == "end_call" and task["source"] == "transferred" for task in tasks)
        assert not any(task["type"] == "transfer_failed" for task in tasks)

    asyncio.run(run())


def test_twilio_transfer_updates_call_with_dial_twiml_and_resolves_outcome() -> None:
    async def run() -> None:
        registry = TransferRegistry()
        control = FakeTwilioCallControl()
        prepared = []
        transfer = TwilioCallTransfer(
            control,
            registry,
            "CA123",
            "https://voice.example.com",
            "Nobody can answer.",
            lambda: prepared.append(True),
        )

        transfer.prepare()
        start = await transfer.start(TARGET, "Caller requests a person.")

        assert prepared == [True]
        assert start == TransferStart("dialling")
        assert control.updates[0][0] == "CA123"
        root = ET.fromstring(control.updates[0][1])
        dial = root.find("Dial")
        assert dial is not None
        assert dial.attrib == {
            "timeout": "20",
            "action": "https://voice.example.com/twilio/transfer-status",
            "method": "POST",
        }
        number = dial.find("Number")
        assert number is not None
        assert number.text == TARGET.phone
        assert number.attrib == {
            "statusCallbackEvent": "answered",
            "statusCallback": ("https://voice.example.com/twilio/transfer-status?event=answered"),
            "statusCallbackMethod": "POST",
        }
        registry.resolve("CA123", "answered")
        assert await transfer.outcome(0.1) == "answered"
        assert registry.entry("CA123") is None

    asyncio.run(run())


def test_twilio_transfer_update_error_is_reported() -> None:
    class BrokenControl:
        async def update_call(self, call_sid, twiml):
            raise RuntimeError("provider unavailable")

    async def run() -> None:
        registry = TransferRegistry()
        transfer = TwilioCallTransfer(
            BrokenControl(),
            registry,
            "CA123",
            "https://voice.example.com",
            "Nobody can answer.",
            lambda: None,
        )

        result = await transfer.start(TARGET, "Caller requests a person.")

        assert result.status == "error"
        assert result.error == "provider unavailable"
        assert registry.entry("CA123") is None

    asyncio.run(run())


def test_transfer_twiml_escapes_text_and_fake_control_writes_jsonl(tmp_path) -> None:
    failure = build_transfer_failed_twiml("Sorry, nobody & <no one>.")
    say = ET.fromstring(failure).find("Say")
    assert say is not None
    assert say.text == "Sorry, nobody & <no one>."

    async def run() -> None:
        log_path = tmp_path / "fake-calls.jsonl"
        control = FakeTwilioCallControl(log_path)
        twiml = build_transfer_twiml(
            "+447700900123",
            "https://voice.example.com/twilio/transfer-status",
            "https://voice.example.com/twilio/transfer-status?event=answered",
        )
        await control.update_call("CA123", twiml)

        assert control.updates == [("CA123", twiml)]
        entry = json.loads(log_path.read_text())
        assert entry["call_sid"] == "CA123"
        assert entry["twiml"] == twiml
        assert entry["at"]

    asyncio.run(run())


def test_rest_twilio_call_control_posts_form_with_basic_auth_and_timeout() -> None:
    calls = []
    options = {}

    class ResponseContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            calls.append(("raise_for_status",))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def post(self, url, *, data):
            calls.append((url, data))
            return ResponseContext()

    def session_factory(**kwargs):
        options.update(kwargs)
        return Session()

    async def run() -> None:
        control = RestTwilioCallControl("AC123", "auth", session_factory)
        await control.update_call("CA456", "<Response/>")

    asyncio.run(run())

    assert calls == [
        (
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls/CA456.json",
            {"Twiml": "<Response/>"},
        ),
        ("raise_for_status",),
    ]
    assert isinstance(options["auth"], aiohttp.BasicAuth)
    assert options["auth"].login == "AC123"
    assert options["auth"].password == "auth"
    assert options["timeout"].total == 5


def test_call_control_defaults_to_fake_and_requires_credentials_for_rest() -> None:
    assert isinstance(call_control_from_env({}), FakeTwilioCallControl)
    assert isinstance(
        call_control_from_env({"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "auth"}),
        RestTwilioCallControl,
    )
    assert isinstance(call_control_from_env({"TWILIO_CALL_CONTROL": "fake"}), FakeTwilioCallControl)
    try:
        call_control_from_env({"TWILIO_CALL_CONTROL": "rest"})
    except ValueError as error:
        assert "requires Twilio account SID" in str(error)
    else:
        raise AssertionError("Explicit REST call control should require credentials.")
