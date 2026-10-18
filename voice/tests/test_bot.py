import asyncio
import importlib.util
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from inbound_core.config import AppointmentType, BookingConfig, TransferTarget
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.types import NOT_GIVEN

from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools

BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"
BOT_SPEC = importlib.util.spec_from_file_location("voice_bot", BOT_PATH)
assert BOT_SPEC is not None and BOT_SPEC.loader is not None
BOT_MODULE = importlib.util.module_from_spec(BOT_SPEC)
BOT_SPEC.loader.exec_module(BOT_MODULE)


class FastBotSpeechTracker(BOT_MODULE.BotSpeechTracker):
    def __init__(
        self,
        *,
        start_timeout: float = 0.06,
        max_secs: float = 0.15,
        grace_secs: float = 0.01,
        quiet_secs: float = 0.01,
        quiet_max_secs: float = 0.2,
    ) -> None:
        super().__init__()
        self.start_timeout = start_timeout
        self.max_secs = max_secs
        self.grace_secs = grace_secs
        self.quiet_secs = quiet_secs
        self.quiet_max_secs = quiet_max_secs

    async def wait_until_quiet(self, quiet_secs=None, max_secs=None):
        return await super().wait_until_quiet(
            self.quiet_secs if quiet_secs is None else quiet_secs,
            self.quiet_max_secs if max_secs is None else max_secs,
        )

    async def wait_for_utterance(
        self,
        after_count,
        start_timeout=None,
        max_secs=None,
        grace_secs=None,
    ):
        return await super().wait_for_utterance(
            after_count,
            self.start_timeout if start_timeout is None else start_timeout,
            self.max_secs if max_secs is None else max_secs,
            self.grace_secs if grace_secs is None else grace_secs,
        )


def _transfer_handler(tmp_path, events, tracker):
    class FakeLLM:
        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM) -> None:
            events.append(("push", frame, direction))

    class FakeParams:
        arguments = {"target_key": "duty_solicitor", "reason": "Caller requests a person."}

        def __init__(self) -> None:
            self.llm = FakeLLM()

        async def result_callback(self, result, *, properties=None) -> None:
            events.append(("result", result, properties))

    class FakeTransfer:
        def prepare(self) -> None:
            events.append(("prepare",))

        async def start(self, target, reason):
            events.append(("start", time.monotonic(), target.key, reason))
            return BOT_MODULE.TransferStart("dialling")

    sink = LocalFilesCallSink(tmp_path, "transfer-tool-handler")
    config = CallTools(sink).config.model_copy(
        update={
            "live_actions": ["transfer_call"],
            "transfer_targets": [
                TransferTarget(
                    key="duty_solicitor",
                    label="Duty solicitor",
                    phone="+447700900123",
                    when_to_use="The caller explicitly asks to speak to a person now.",
                    office_hours_only=False,
                )
            ],
            "transfer_handoff_message": "A colleague will speak with you shortly.",
        }
    )
    call_tools = CallTools(sink, config)
    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(
            call_tools,
            FakeTransfer(),
            tracker,
        )
        if schema.name == "transfer_call"
    )
    return handler, FakeParams(), call_tools


def test_smart_turn_stop_secs_parses_default_and_override() -> None:
    assert BOT_MODULE.smart_turn_stop_secs({}) == 0.7
    assert BOT_MODULE.smart_turn_stop_secs({"SMART_TURN_STOP_SECS": "1.5"}) == 1.5
    with pytest.raises(ValueError, match="SMART_TURN_STOP_SECS"):
        BOT_MODULE.smart_turn_stop_secs({"SMART_TURN_STOP_SECS": "0"})


def test_idle_prompt_secs_parses_default_and_override() -> None:
    assert BOT_MODULE.idle_prompt_secs({}) == 8.0
    assert BOT_MODULE.idle_prompt_secs({"IDLE_PROMPT_SECS": "12"}) == 12.0
    with pytest.raises(ValueError, match="IDLE_PROMPT_SECS"):
        BOT_MODULE.idle_prompt_secs({"IDLE_PROMPT_SECS": "0"})


def test_number_hold_secs_parses_default_and_override() -> None:
    assert BOT_MODULE.number_hold_secs({}) == 2.0
    assert BOT_MODULE.number_hold_secs({"NUMBER_HOLD_SECS": "1.25"}) == 1.25
    with pytest.raises(ValueError, match="NUMBER_HOLD_SECS"):
        BOT_MODULE.number_hold_secs({"NUMBER_HOLD_SECS": "0"})


def test_caller_id_uses_test_override_or_twilio_start_parameters() -> None:
    assert (
        BOT_MODULE.resolve_caller_id(
            {"from": "+441234567890", "body": {}},
            {"CALLER_ID_OVERRIDE": "07700900456"},
            telephony=False,
        )
        == "07700900456"
    )
    assert (
        BOT_MODULE.resolve_caller_id(
            {"from": "+441234567890", "body": {"From": "07700111222"}},
            {"CALLER_ID_OVERRIDE": "07700900456"},
            telephony=True,
        )
        == "07700111222"
    )
    assert (
        BOT_MODULE.resolve_caller_id(
            {"start": {"customParameters": {"from_number": "07700900456"}}},
            {},
            telephony=True,
        )
        == "07700900456"
    )


def test_runner_external_call_id_uses_call_data_or_generates_uuid() -> None:
    helper = BOT_MODULE._runner_external_call_id

    assert helper({"call_id": "CA123"}) == "CA123"
    assert helper(SimpleNamespace(call_id="CA456")) == "CA456"
    fallback = helper(None)
    assert str(uuid.UUID(fallback)) == fallback


def test_number_hold_strategy_waits_only_for_incomplete_numbers() -> None:
    class Metrics:
        def __init__(self) -> None:
            self.holds = []

        def start_number_hold(self):
            record = {"number_hold_started": 0.0, "resumed": False, "held_secs": None}
            self.holds.append(record)
            return record

        def finish_number_hold(self, record, *, resumed, held_secs):
            record["resumed"] = resumed
            record["held_secs"] = held_secs

    class RecordingStrategy(BOT_MODULE.IncompletePhoneNumberTurnStopStrategy):
        def __init__(self, metrics, hold_seconds):
            super().__init__(
                turn_analyzer=object(),
                hold_seconds=hold_seconds,
                metrics_observer=metrics,
            )
            self.stop_calls = 0

        async def trigger_user_turn_stopped(self, **kwargs):
            if self._number_hold_task is not None:
                return
            self.stop_calls += 1

    async def run() -> None:
        metrics = Metrics()
        normal = RecordingStrategy(metrics, hold_seconds=0.02)
        normal._text = "hello"
        normal._turn_text = "hello"
        normal._turn_complete = True
        normal._transcript_finalized = True
        await normal._maybe_trigger_user_turn_stopped()
        assert normal.stop_calls == 1
        assert metrics.holds == []

        resumed = RecordingStrategy(metrics, hold_seconds=0.2)
        resumed._text = "07700 90045"
        resumed._turn_text = "my number is 07700 90045"
        resumed._turn_complete = True
        resumed._transcript_finalized = True
        await resumed._maybe_trigger_user_turn_stopped()
        assert resumed.stop_calls == 0
        await asyncio.sleep(0.01)
        await resumed.process_frame(UserStartedSpeakingFrame())
        resumed._append_turn_text("456.")
        resumed._text = "456."
        resumed._turn_complete = True
        resumed._transcript_finalized = True
        await resumed._maybe_trigger_user_turn_stopped()
        assert resumed.stop_calls == 1
        assert len(metrics.holds) == 1
        assert metrics.holds[0]["resumed"] is True
        assert metrics.holds[0]["held_secs"] < 0.2

        unresolved = RecordingStrategy(metrics, hold_seconds=0.01)
        unresolved._text = "07700 90045"
        unresolved._turn_text = "my number is 07700 90045"
        unresolved._turn_complete = True
        unresolved._transcript_finalized = True
        await unresolved._maybe_trigger_user_turn_stopped()
        assert unresolved.stop_calls == 0
        await unresolved._maybe_trigger_user_turn_stopped()
        await unresolved.trigger_user_turn_stopped()
        assert unresolved.stop_calls == 0
        await asyncio.sleep(0.03)
        assert unresolved.stop_calls == 1
        assert metrics.holds[-1]["resumed"] is False
        assert metrics.holds[-1]["held_secs"] >= 0.01

    asyncio.run(run())


def test_number_hold_starts_when_final_transcript_arrives_after_stop_decision() -> None:
    class Metrics:
        def __init__(self) -> None:
            self.holds = []

        def start_number_hold(self):
            record = {"number_hold_started": 0.0, "resumed": False, "held_secs": None}
            self.holds.append(record)
            return record

        def finish_number_hold(self, record, *, resumed, held_secs):
            record["resumed"] = resumed
            record["held_secs"] = held_secs

    class RecordingStrategy(BOT_MODULE.IncompletePhoneNumberTurnStopStrategy):
        def __init__(self, metrics):
            super().__init__(
                turn_analyzer=object(),
                hold_seconds=0.02,
                metrics_observer=metrics,
            )
            self.stop_calls = 0

        async def trigger_user_turn_stopped(self, **kwargs):
            if self._number_hold_task is None:
                self.stop_calls += 1

    async def run() -> None:
        metrics = Metrics()
        strategy = RecordingStrategy(metrics)
        strategy._turn_complete = True
        strategy._vad_stopped = True

        await strategy._maybe_trigger_user_turn_stopped()
        assert strategy.stop_calls == 0
        assert metrics.holds == []

        await strategy.process_frame(
            TranscriptionFrame(
                text="My number is 07700 90045",
                user_id="caller",
                timestamp="2026-10-17T00:00:00Z",
                finalized=True,
            )
        )
        assert strategy.stop_calls == 0
        assert len(metrics.holds) == 1
        assert strategy._number_hold_task is not None

        await asyncio.sleep(0.03)
        assert strategy.stop_calls == 1
        assert metrics.holds[0]["resumed"] is False
        assert metrics.holds[0]["held_secs"] >= 0.02

    asyncio.run(run())


def test_goodbye_fallback_decision_is_pure() -> None:
    decide = BOT_MODULE.should_goodbye_detected_end_call

    assert decide(True, "Goodbye.")
    assert decide(True, "Thanks for confirming. Bye for now.")
    assert decide(False, "Thanks, goodbye.")
    assert not decide(False, "Before we say goodbye, can I take your email?")
    assert not decide(True, "I can help with anything else.")
    assert not decide(True, "Goodbye.", end_call_received=True)


def test_goodbye_hangup_remembers_caller_closing_through_confirmation(tmp_path) -> None:
    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "goodbye-sticky")
        end_worker_calls = []

        async def push_end_worker() -> None:
            end_worker_calls.append(True)

        observer = BOT_MODULE.GoodbyeDetectedHangup(
            sink,
            CallTools(sink),
            push_end_worker,
            delay_seconds=0.01,
        )

        async def push(frame) -> None:
            await observer.on_push_frame(SimpleNamespace(frame=frame))

        await push(
            TranscriptionFrame(
                text="That's everything. Bye.",
                user_id="caller",
                timestamp="2026-10-17T00:00:00Z",
            )
        )
        await push(UserStartedSpeakingFrame())
        await push(
            TranscriptionFrame(
                text="Yes, that's right.",
                user_id="caller",
                timestamp="2026-10-17T00:00:01Z",
            )
        )
        await push(TTSTextFrame(text="Thank you for confirming.", aggregated_by="test"))
        await push(TTSTextFrame(text="Goodbye.", aggregated_by="test"))
        await push(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.03)

        tasks = json.loads((sink.directory / "tasks.json").read_text())
        assert end_worker_calls == [True]
        assert tasks[0]["type"] == "end_call"
        assert tasks[0]["source"] == "goodbye_detected"
        observer.close()

    asyncio.run(run())


def test_goodbye_hangup_does_not_duplicate_an_existing_end_worker(tmp_path) -> None:
    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "goodbye-end-worker")
        end_worker_calls = []

        async def push_end_worker() -> None:
            end_worker_calls.append(True)

        observer = BOT_MODULE.GoodbyeDetectedHangup(
            sink,
            CallTools(sink),
            push_end_worker,
            delay_seconds=0.01,
        )

        async def push(frame) -> None:
            await observer.on_push_frame(SimpleNamespace(frame=frame))

        await push(TTSTextFrame(text="Goodbye.", aggregated_by="test"))
        await push(BotStoppedSpeakingFrame())
        await push(EndWorkerFrame())
        await asyncio.sleep(0.03)

        assert end_worker_calls == []
        assert json.loads((sink.directory / "tasks.json").read_text()) == []
        observer.close()

    asyncio.run(run())


def test_end_call_sends_end_worker_after_tool_result(tmp_path) -> None:
    events = []

    class FakeLLM:
        async def push_frame(self, frame, direction) -> None:
            events.append(("frame", frame, direction))

    class FakeParams:
        arguments = {}
        llm = FakeLLM()

        async def result_callback(self, result, *, properties=None) -> None:
            events.append(("result", result, properties))

    tools = CallTools(LocalFilesCallSink(tmp_path, "end-call"))
    tools.config = tools.config.model_copy(update={"live_actions": ["end_call"]})
    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(tools)
        if schema.name == "end_call"
    )

    asyncio.run(handler(FakeParams()))

    assert events[0][0:2] == ("result", {"ok": True, "status": "The call may now end."})
    assert events[0][2].run_llm is False
    assert isinstance(events[1][1], EndWorkerFrame)
    assert events[1][2] is FrameDirection.UPSTREAM


def test_transfer_handoff_speaks_then_starts_transfer_and_ends_worker(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(max_secs=0.3, grace_secs=0.01)
    handler, params, call_tools = _transfer_handler(tmp_path, events, tracker)

    async def feed_handoff_speech() -> None:
        while not any(
            event[0] == "push" and isinstance(event[1], TTSSpeakFrame) for event in events
        ):
            await asyncio.sleep(0.005)
        events.append(("speech_started",))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        await asyncio.sleep(0.01)
        events.append(("speech_stopped",))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))

    async def run() -> float:
        feeder = asyncio.create_task(feed_handoff_speech())
        started_at = time.monotonic()
        await handler(params)
        await feeder
        return time.monotonic() - started_at

    elapsed = asyncio.run(run())
    handoff_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], TTSSpeakFrame)
    )
    start_index = next(index for index, event in enumerate(events) if event[0] == "start")
    end_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], EndWorkerFrame)
    )

    assert events[0][0:2] == ("result", {"ok": True, "status": "Transferring the caller."})
    assert events[0][2].run_llm is False
    assert events[1] == ("prepare",)
    assert events[handoff_index][1].text == "A colleague will speak with you shortly."
    assert events[handoff_index][2] is FrameDirection.DOWNSTREAM
    assert handoff_index < events.index(("speech_started",)) < events.index(("speech_stopped",))
    assert events.index(("speech_stopped",)) < start_index < end_index
    assert events[start_index][2:] == ("duty_solicitor", "Caller requests a person.")
    assert events[end_index][2] is FrameDirection.UPSTREAM
    assert call_tools.transfer_start == BOT_MODULE.TransferStart("dialling")
    assert call_tools.transfer_started_at is not None
    assert elapsed < 0.3


def test_transfer_without_tracker_still_speaks_handoff(tmp_path) -> None:
    events = []
    handler, params, _ = _transfer_handler(tmp_path, events, None)

    asyncio.run(handler(params))
    handoff_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], TTSSpeakFrame)
    )
    start_index = next(index for index, event in enumerate(events) if event[0] == "start")
    end_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], EndWorkerFrame)
    )

    assert events[handoff_index][1].text == "A colleague will speak with you shortly."
    assert handoff_index < start_index < end_index


def test_transfer_starts_when_handoff_never_starts_speaking(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(start_timeout=0.06, max_secs=0.15)
    handler, params, _ = _transfer_handler(tmp_path, events, tracker)

    async def run() -> float:
        started_at = time.monotonic()
        await handler(params)
        return time.monotonic() - started_at

    elapsed = asyncio.run(run())

    assert any(event[0] == "start" for event in events)
    assert elapsed < 0.2


def test_transfer_waits_for_prior_speech_then_speaks_handoff(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(max_secs=0.3, grace_secs=0.01)
    asyncio.run(
        tracker.on_push_frame(SimpleNamespace(frame=LLMTextFrame(text="I am transferring you.")))
    )
    asyncio.run(tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame())))
    events.append(("prior_speech_started",))
    handler, params, _ = _transfer_handler(tmp_path, events, tracker)

    async def feed_speech() -> None:
        while ("prepare",) not in events:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        events.append(("prior_speech_stopped",))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))
        while not any(
            event[0] == "push" and isinstance(event[1], TTSSpeakFrame) for event in events
        ):
            await asyncio.sleep(0.005)
        events.append(("handoff_speech_started",))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        await asyncio.sleep(0.01)
        events.append(("handoff_speech_stopped",))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))

    async def run() -> None:
        feeder = asyncio.create_task(feed_speech())
        await handler(params)
        await feeder

    asyncio.run(run())
    handoff_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], TTSSpeakFrame)
    )
    start_index = next(index for index, event in enumerate(events) if event[0] == "start")

    assert events.index(("prior_speech_stopped",)) < handoff_index
    assert handoff_index < events.index(("handoff_speech_started",))
    assert events.index(("handoff_speech_stopped",)) < start_index


def test_transfer_starts_at_handoff_utterance_timeout_without_stop(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(start_timeout=0.06, max_secs=0.12)
    handler, params, _ = _transfer_handler(tmp_path, events, tracker)

    async def feed_started_only() -> None:
        while not any(
            event[0] == "push" and isinstance(event[1], TTSSpeakFrame) for event in events
        ):
            await asyncio.sleep(0.005)
        events.append(("handoff_speech_started", time.monotonic()))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))

    async def run() -> None:
        feeder = asyncio.create_task(feed_started_only())
        await handler(params)
        await feeder

    asyncio.run(run())
    started_at = next(event[1] for event in events if event[0] == "handoff_speech_started")
    start_at = next(event[1] for event in events if event[0] == "start")

    assert 0.1 <= start_at - started_at < 0.3


def test_bot_speech_tracker_ignores_text_deduplicates_siblings_and_caps_quiet_wait() -> None:
    async def run() -> None:
        tracker = BOT_MODULE.BotSpeechTracker()
        await tracker.on_push_frame(SimpleNamespace(frame=LLMTextFrame(text="Not speaking yet.")))
        await tracker.on_push_frame(
            SimpleNamespace(frame=TTSTextFrame(text="Not speaking yet.", aggregated_by="test"))
        )
        assert tracker.speaking is False
        assert tracker.started_count == 0

        started = BotStartedSpeakingFrame()
        start_sibling = BotStartedSpeakingFrame()
        started.broadcast_sibling_id = start_sibling.id
        start_sibling.broadcast_sibling_id = started.id
        await tracker.on_push_frame(SimpleNamespace(frame=started))
        await tracker.on_push_frame(SimpleNamespace(frame=start_sibling))
        assert tracker.started_count == 1

        stopped = BotStoppedSpeakingFrame()
        stop_sibling = BotStoppedSpeakingFrame()
        stopped.broadcast_sibling_id = stop_sibling.id
        stop_sibling.broadcast_sibling_id = stopped.id
        await tracker.on_push_frame(SimpleNamespace(frame=stopped))
        stopped_at = tracker.last_stopped_at
        await tracker.on_push_frame(SimpleNamespace(frame=stop_sibling))
        assert tracker.last_stopped_at == stopped_at

        started_at = time.monotonic()
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        await tracker.wait_until_quiet(quiet_secs=0.5, max_secs=0.08)
        assert 0.06 <= time.monotonic() - started_at < 0.3

    asyncio.run(run())


def test_failed_transfer_tool_result_continues_llm(tmp_path) -> None:
    events = []

    class FakeLLM:
        async def push_frame(self, frame, direction) -> None:
            events.append(("frame", frame, direction))

    class FakeParams:
        arguments = {"target_key": "unknown", "reason": "Caller requests a person."}
        llm = FakeLLM()

        async def result_callback(self, result, *, properties=None) -> None:
            events.append(("result", result, properties))

    sink = LocalFilesCallSink(tmp_path, "invalid-transfer-tool")
    config = CallTools(sink).config.model_copy(
        update={
            "live_actions": ["transfer_call"],
            "transfer_targets": [
                TransferTarget(
                    key="duty_solicitor",
                    label="Duty solicitor",
                    phone="+447700900123",
                    when_to_use="The caller explicitly asks to speak to a person now.",
                    office_hours_only=False,
                )
            ],
        }
    )
    call_tools = CallTools(sink, config)
    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(call_tools)
        if schema.name == "transfer_call"
    )

    asyncio.run(handler(FakeParams()))

    assert events == [
        ("result", {"ok": False, "error": "Unknown transfer target."}, None),
    ]


def test_goodbye_hangup_does_not_schedule_during_transfer(tmp_path) -> None:
    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "goodbye-during-transfer")
        config = CallTools(sink).config.model_copy(
            update={
                "live_actions": ["transfer_call"],
                "transfer_targets": [
                    TransferTarget(
                        key="duty_solicitor",
                        label="Duty solicitor",
                        phone="+447700900123",
                        when_to_use="The caller explicitly asks to speak to a person now.",
                        office_hours_only=False,
                    )
                ],
            }
        )
        call_tools = CallTools(sink, config)
        call_tools.transfer_call("duty_solicitor", "Caller requests a person.")
        end_worker_calls = []

        async def push_end_worker() -> None:
            end_worker_calls.append(True)

        observer = BOT_MODULE.GoodbyeDetectedHangup(
            sink,
            call_tools,
            push_end_worker,
            delay_seconds=0.01,
        )
        await observer.on_push_frame(
            SimpleNamespace(frame=TTSTextFrame(text="Goodbye.", aggregated_by="test"))
        )
        await observer.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))
        await asyncio.sleep(0.03)

        assert end_worker_calls == []
        assert json.loads((sink.directory / "tasks.json").read_text()) == []
        observer.close()

    asyncio.run(run())


def test_empty_live_actions_create_context_without_tools(tmp_path) -> None:
    context = BOT_MODULE.create_llm_context(CallTools(LocalFilesCallSink(tmp_path, "no-tools")))

    assert context.tools is NOT_GIVEN


@pytest.mark.parametrize(
    ("tool_name", "message"),
    [
        ("check_availability", "Checking the calendar now."),
        ("book_appointment", "Saving your appointment now."),
    ],
)
def test_booking_tool_speaks_configured_filler_concurrently(tmp_path, tool_name, message) -> None:
    async def run() -> list:
        events = []
        tracker = FastBotSpeechTracker()
        sink = LocalFilesCallSink(tmp_path, f"booking-filler-{tool_name}")
        config = CallTools(sink).config.model_copy(
            update={
                "live_actions": ["check_availability", "book_appointment"],
                "booking": BookingConfig(
                    appointment_types=[
                        AppointmentType(
                            key="consultation",
                            label="Consultation",
                            duration_minutes=30,
                            when_to_use="New callers",
                        )
                    ],
                    check_message="Checking the calendar now.",
                    book_message="Saving your appointment now.",
                ),
            }
        )

        async def dispatch(name, **_arguments):
            events.append(("tool_started", name))
            await asyncio.sleep(0.02)
            events.append(("tool_finished", name))
            return {"ok": True}

        class FakeBooking:
            async def check_availability(self, **arguments):
                return await dispatch("check_availability", **arguments)

            async def book_appointment(self, **arguments):
                return await dispatch("book_appointment", **arguments)

        call_tools = CallTools(
            sink,
            config,
            booking=FakeBooking(),
            booking_available=True,
        )

        class FakeLLM:
            async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
                events.append(("frame", frame))
                await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))

                async def finish_speech():
                    await asyncio.sleep(0.04)
                    events.append(("speech_finished",))
                    await tracker.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))

                asyncio.create_task(finish_speech())

        class FakeParams:
            arguments = {}
            llm = FakeLLM()

            async def result_callback(self, result, *, properties=None):
                events.append(("result_callback", result))

        handler = next(
            schema.handler
            for schema in BOT_MODULE.create_function_schemas(call_tools, speech_tracker=tracker)
            if schema.name == tool_name
        )
        await handler(FakeParams())
        tool_calls = [
            json.loads(line)
            for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
        ]
        assert [entry["name"] for entry in tool_calls] == [tool_name]
        return events

    events = asyncio.run(run())
    filler = next(event[1] for event in events if event[0] == "frame")

    assert isinstance(filler, TTSSpeakFrame)
    assert filler.text == message
    assert events.index(("tool_started", tool_name)) < events.index(("speech_finished",))
    assert events.index(("tool_finished", tool_name)) < events.index(
        ("result_callback", {"ok": True})
    )
    assert events.index(("speech_finished",)) < events.index(("result_callback", {"ok": True}))


def test_non_booking_tool_does_not_speak_filler(tmp_path) -> None:
    sink = LocalFilesCallSink(tmp_path, "no-booking-filler")
    config = CallTools(sink).config.model_copy(update={"live_actions": ["flag_urgent"]})
    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(CallTools(sink, config))
        if schema.name == "flag_urgent"
    )
    frames = []

    class FakeLLM:
        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            frames.append(frame)

    class FakeParams:
        arguments = {"reason": "Test"}
        llm = FakeLLM()

        async def result_callback(self, result, *, properties=None):
            return None

    asyncio.run(handler(FakeParams()))

    assert not any(isinstance(frame, TTSSpeakFrame) for frame in frames)


def test_idle_hangup_prompts_then_ends_after_bot_stops(tmp_path) -> None:
    async def run() -> None:
        queued_frames = []
        end_worker_calls = []
        sink = LocalFilesCallSink(tmp_path, "idle")

        async def queue_frame(frame) -> None:
            queued_frames.append(frame)

        async def push_end_worker() -> None:
            end_worker_calls.append(True)

        observer = BOT_MODULE.IdleHangupObserver(
            sink,
            queue_frame,
            push_end_worker,
            idle_seconds=0.01,
        )

        async def push(frame) -> None:
            await observer.on_push_frame(SimpleNamespace(frame=frame))

        async def push_speaking_pair(frame_type) -> None:
            frame, sibling = frame_type(), frame_type()
            frame.broadcast_sibling_id = sibling.id
            sibling.broadcast_sibling_id = frame.id
            await push(frame)
            await push(sibling)

        await push_speaking_pair(BotStoppedSpeakingFrame)
        await asyncio.sleep(0.03)
        assert isinstance(queued_frames[0], TTSSpeakFrame)
        assert queued_frames[0].text == "Are you still there?"

        await push_speaking_pair(BotStartedSpeakingFrame)
        await push_speaking_pair(BotStoppedSpeakingFrame)
        await asyncio.sleep(0.03)
        assert queued_frames[1].text == ("I'll end the call now. Thank you for calling. Goodbye.")

        await push_speaking_pair(BotStartedSpeakingFrame)
        await push_speaking_pair(BotStoppedSpeakingFrame)
        assert end_worker_calls == [True]
        tasks = json.loads((sink.directory / "tasks.json").read_text())
        assert len(tasks) == 1
        assert tasks[0]["type"] == "end_call"
        assert tasks[0]["source"] == "idle"
        observer.close()

    asyncio.run(run())


def test_idle_hangup_timer_resets_for_caller_and_bot_speech(tmp_path) -> None:
    async def run() -> None:
        queued_frames = []
        sink = LocalFilesCallSink(tmp_path, "idle-reset")

        async def queue_frame(frame) -> None:
            queued_frames.append(frame)

        async def push_end_worker() -> None:
            pass

        observer = BOT_MODULE.IdleHangupObserver(
            sink,
            queue_frame,
            push_end_worker,
            idle_seconds=0.02,
        )

        async def push(frame) -> None:
            await observer.on_push_frame(SimpleNamespace(frame=frame))

        await push(BotStoppedSpeakingFrame())
        await push(UserStartedSpeakingFrame())
        await asyncio.sleep(0.03)
        assert queued_frames == []

        await push(UserStoppedSpeakingFrame())
        await push(BotStoppedSpeakingFrame())
        await push(BotStartedSpeakingFrame())
        await asyncio.sleep(0.03)
        assert queued_frames == []

        await push(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.04)
        assert [frame.text for frame in queued_frames] == ["Are you still there?"]
        observer.close()

    asyncio.run(run())
