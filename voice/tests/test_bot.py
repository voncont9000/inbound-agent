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
    InputAudioRawFrame,
    InterruptionFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserMuteStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.types import NOT_GIVEN

from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools

BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"
BOT_SPEC = importlib.util.spec_from_file_location("voice_bot", BOT_PATH)
assert BOT_SPEC is not None and BOT_SPEC.loader is not None
BOT_MODULE = importlib.util.module_from_spec(BOT_SPEC)
BOT_SPEC.loader.exec_module(BOT_MODULE)


class FastBotSpeechTracker(BOT_MODULE.BotSpeechTracker):
    def __init__(self, *, marker_timeout: float = 0.15) -> None:
        super().__init__()
        self.marker_timeout = marker_timeout

    async def wait_for_marker(self, marker_id, timeout=10.0):
        return await super().wait_for_marker(
            marker_id,
            timeout=min(timeout, self.marker_timeout),
        )


def _transfer_handler(tmp_path, events, tracker):
    marker_pushed = asyncio.Event()
    start_called = asyncio.Event()
    tasks = BOT_MODULE.CallTaskSet()

    class FakeLLM:
        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM) -> None:
            events.append(("push", frame, direction))
            if isinstance(frame, BOT_MODULE.SpeechMarkerFrame):
                marker_pushed.set()

    class FakeParams:
        arguments = {"target_key": "duty_solicitor", "reason": "Caller requests a person."}

        def __init__(self) -> None:
            self.llm = FakeLLM()
            self.result_callback_complete = asyncio.Event()
            self.marker_pushed = marker_pushed
            self.start_called = start_called
            self.transfer_accepted_at_result = None
            self.transfer = transfer
            self.tasks = tasks

        async def result_callback(self, result, *, properties=None) -> None:
            events.append(("result", result, properties))
            self.transfer_accepted_at_result = getattr(call_tools, "transfer_accepted", False)
            self.result_callback_complete.set()

    class FakeTransfer:
        def prepare(self) -> None:
            events.append(("prepare",))

        async def start(self, target, reason):
            events.append(("start", time.monotonic(), target.key, reason))
            self.started_at = time.monotonic()
            start_called.set()
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
    transfer = FakeTransfer()
    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(
            call_tools,
            transfer,
            tracker,
            tasks=tasks,
        )
        if schema.name == "transfer_call"
    )
    return handler, FakeParams(), call_tools


def _booking_handler(
    tmp_path,
    events,
    tracker,
    tool_name,
    *,
    delay=0.0,
    start_speaking_on_holding=False,
):
    sink = LocalFilesCallSink(tmp_path, f"booking-handler-{tool_name}")
    message = (
        "Checking the calendar now."
        if tool_name == "check_availability"
        else "Saving your appointment now."
    )
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

    booking_records = []

    async def dispatch(name, **_arguments):
        events.append(("tool_started", name))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            events.append(("tool_cancelled", name))
            raise
        events.append(("tool_finished", name, time.monotonic()))
        return {"ok": True}

    class FakeBooking:
        async def check_availability(self, **arguments):
            return await dispatch("check_availability", **arguments)

        async def book_appointment(self, **arguments):
            result = await dispatch("book_appointment", **arguments)
            booking_records.append(arguments)
            return result

    call_tools = CallTools(
        sink,
        config,
        booking=FakeBooking(),
        booking_available=True,
    )
    tasks = BOT_MODULE.CallTaskSet()

    class FakeLLM:
        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            events.append(("frame", frame))
            if start_speaking_on_holding and isinstance(frame, TTSSpeakFrame):
                await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))

    class FakeContext:
        def __init__(self) -> None:
            self.messages = []
            self.message_added = asyncio.Event()

        def add_message(self, message) -> None:
            self.messages.append(message)
            self.message_added.set()

    result_calls = []

    class FakeParams:
        def __init__(self) -> None:
            self.arguments = {}
            self.llm = FakeLLM()
            self.context = FakeContext()
            self.tool_call_id = f"booking-{tool_name}"
            self.tasks = tasks
            self.booking_records = booking_records

        async def result_callback(self, result, *, properties=None):
            result_calls.append((result, time.monotonic()))
            events.append(("result_callback", result))

    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(
            call_tools,
            speech_tracker=tracker,
            tasks=tasks,
        )
        if schema.name == tool_name
    )
    return handler, FakeParams(), sink, result_calls, message


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


def test_transfer_handoff_survives_handler_interruption_and_waits_for_output_marker(
    tmp_path,
) -> None:
    events = []
    tracker = FastBotSpeechTracker(marker_timeout=0.5)
    handler, params, call_tools = _transfer_handler(tmp_path, events, tracker)

    async def run() -> float:
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        handler_task = asyncio.create_task(handler(params))
        await params.result_callback_complete.wait()
        handler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler_task
        await asyncio.wait_for(params.marker_pushed.wait(), timeout=0.5)
        marker_frame = next(
            event[1]
            for event in events
            if event[0] == "push" and isinstance(event[1], BOT_MODULE.SpeechMarkerFrame)
        )
        await tracker.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        await tracker.on_push_frame(SimpleNamespace(frame=marker_frame, source=SimpleNamespace()))
        await asyncio.sleep(0.01)
        assert not params.start_called.is_set()
        marker_arrived_at = time.monotonic()
        events.append(("output_marker",))
        await tracker.on_push_frame(
            SimpleNamespace(
                frame=marker_frame,
                source=object.__new__(BaseOutputTransport),
            )
        )
        await asyncio.wait_for(params.start_called.wait(), timeout=0.5)
        await asyncio.sleep(0)
        await params.tasks.cancel_all()
        return marker_arrived_at

    marker_arrived_at = asyncio.run(run())
    handoff_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], TTSSpeakFrame)
    )
    marker_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], BOT_MODULE.SpeechMarkerFrame)
    )
    output_marker_index = events.index(("output_marker",))
    start_index = next(index for index, event in enumerate(events) if event[0] == "start")
    end_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "push" and isinstance(event[1], EndWorkerFrame)
    )

    assert events[0][0:2] == ("result", {"ok": True, "status": "Transferring the caller."})
    assert events[0][2].run_llm is False
    assert params.transfer_accepted_at_result is True
    assert events[1] == ("prepare",)
    assert events[handoff_index][1].text == "A colleague will speak with you shortly."
    assert marker_index == handoff_index + 1
    assert output_marker_index < start_index < end_index
    assert 0.19 <= events[start_index][1] - marker_arrived_at < 0.4
    assert events[start_index][2:] == ("duty_solicitor", "Caller requests a person.")
    assert events[end_index][2] is FrameDirection.UPSTREAM
    assert call_tools.transfer_start == BOT_MODULE.TransferStart("dialling")
    assert call_tools.transfer_started_at is not None
    assert tracker._markers == {}


def test_transfer_without_tracker_still_speaks_handoff(tmp_path) -> None:
    events = []
    handler, params, _ = _transfer_handler(tmp_path, events, None)

    async def run() -> None:
        await handler(params)
        await asyncio.wait_for(params.start_called.wait(), timeout=0.5)
        await params.tasks.cancel_all()

    asyncio.run(run())
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


def test_transfer_starts_at_marker_timeout_when_marker_never_arrives(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(marker_timeout=0.06)
    handler, params, _ = _transfer_handler(tmp_path, events, tracker)

    async def run() -> float:
        await handler(params)
        await asyncio.wait_for(params.marker_pushed.wait(), timeout=0.5)
        marker_pushed_at = time.monotonic()
        await asyncio.wait_for(params.start_called.wait(), timeout=0.5)
        elapsed = params.transfer.started_at - marker_pushed_at
        await params.tasks.cancel_all()
        return elapsed

    elapsed = asyncio.run(run())

    assert 0.05 <= elapsed < 0.5
    assert any(event[0] == "start" for event in events)
    assert tracker._markers == {}


def test_transfer_interruption_releases_marker_without_grace_period(tmp_path) -> None:
    events = []
    tracker = FastBotSpeechTracker(marker_timeout=0.5)
    handler, params, _ = _transfer_handler(tmp_path, events, tracker)

    async def run() -> float:
        await handler(params)
        await asyncio.wait_for(params.marker_pushed.wait(), timeout=0.5)
        interrupted_at = time.monotonic()
        await tracker.on_push_frame(
            SimpleNamespace(
                frame=InterruptionFrame(),
                source=object.__new__(BaseOutputTransport),
            )
        )
        await asyncio.wait_for(params.start_called.wait(), timeout=0.5)
        elapsed = params.transfer.started_at - interrupted_at
        await params.tasks.cancel_all()
        return elapsed

    elapsed = asyncio.run(run())

    assert elapsed < 0.1


def test_bot_speech_tracker_deduplicates_siblings_and_filters_marker_sources() -> None:
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

        marker_id = tracker.new_marker()
        marker_frame = BOT_MODULE.SpeechMarkerFrame(marker_id=marker_id)
        assert marker_frame.interruptible is False
        await tracker.on_push_frame(SimpleNamespace(frame=marker_frame, source=SimpleNamespace()))
        assert not tracker._markers[marker_id].event.is_set()
        await tracker.on_push_frame(
            SimpleNamespace(
                frame=marker_frame,
                source=object.__new__(BaseOutputTransport),
            )
        )
        assert await tracker.wait_for_marker(marker_id, timeout=0.01) == "played"
        assert marker_id not in tracker._markers

        timed_out_marker = tracker.new_marker()
        assert await tracker.wait_for_marker(timed_out_marker, timeout=0.01) == "timeout"
        assert timed_out_marker not in tracker._markers

    asyncio.run(run())


def test_speech_marker_interruption_is_output_only_and_first_status_wins() -> None:
    async def run() -> None:
        tracker = BOT_MODULE.BotSpeechTracker()
        marker_id = tracker.new_marker()
        marker = BOT_MODULE.SpeechMarkerFrame(marker_id=marker_id)
        output_transport = object.__new__(BaseOutputTransport)

        await tracker.on_push_frame(
            SimpleNamespace(frame=InterruptionFrame(), source=SimpleNamespace())
        )
        assert tracker._markers[marker_id].result is None
        await tracker.on_push_frame(
            SimpleNamespace(frame=InterruptionFrame(), source=output_transport)
        )
        await tracker.on_push_frame(SimpleNamespace(frame=marker, source=output_transport))

        assert await tracker.wait_for_marker(marker_id, timeout=0.1) == "interrupted"
        assert marker_id not in tracker._markers

    asyncio.run(run())


def test_transfer_user_mute_suppresses_interruption_and_new_user_turn_frames(tmp_path) -> None:
    async def run() -> None:
        call_tools = CallTools(LocalFilesCallSink(tmp_path, "transfer-user-mute"))
        call_tools.transfer_accepted = True
        context = LLMContext()
        aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_mute_strategies=[BOT_MODULE.TransferUserMuteStrategy(call_tools)]
            ),
        )
        user_aggregator = aggregators.user()
        down_frames, _ = await run_test(
            user_aggregator,
            frames_to_send=[
                TTSTextFrame(text="The transfer was accepted.", aggregated_by="test"),
                InputAudioRawFrame(audio=b"\x01" * 160, sample_rate=8000, num_channels=1),
                InterruptionFrame(),
                VADUserStartedSpeakingFrame(start_secs=0.2),
                VADUserStoppedSpeakingFrame(stop_secs=0.2),
                UserStartedSpeakingFrame(),
                TranscriptionFrame(
                    text="Please wait",
                    user_id="caller",
                    timestamp="2026-10-17T00:00:00Z",
                    finalized=True,
                ),
                UserStoppedSpeakingFrame(),
            ],
        )

        assert user_aggregator._user_is_muted
        assert any(isinstance(frame, UserMuteStartedFrame) for frame in down_frames)
        assert context.messages == []
        assert not any(
            isinstance(
                frame,
                (
                    InputAudioRawFrame,
                    InterruptionFrame,
                    VADUserStartedSpeakingFrame,
                    VADUserStoppedSpeakingFrame,
                    UserStartedSpeakingFrame,
                    UserStoppedSpeakingFrame,
                    TranscriptionFrame,
                ),
            )
            for frame in down_frames
        )

    asyncio.run(run())


def test_output_transport_writes_audio_before_speech_marker() -> None:
    async def run() -> None:
        events = []

        class StubOutputTransport(BaseOutputTransport):
            def __init__(self) -> None:
                super().__init__(
                    TransportParams(
                        audio_out_enabled=True,
                        audio_out_sample_rate=8000,
                        audio_out_10ms_chunks=1,
                        audio_out_end_silence_secs=0,
                    )
                )

            async def start(self, frame) -> None:
                await self.set_transport_ready(frame)

            async def write_audio_frame(self, frame) -> bool:
                await asyncio.sleep(0.01)
                events.append(("audio_written",))
                return True

            async def write_transport_frame(self, frame) -> None:
                if isinstance(frame, BOT_MODULE.SpeechMarkerFrame):
                    events.append(("marker_written",))
                    assert any(event[0] == "audio_written" for event in events)

        class RecordingTracker(BOT_MODULE.BotSpeechTracker):
            async def on_push_frame(self, data) -> None:
                if isinstance(data.frame, BOT_MODULE.SpeechMarkerFrame) and isinstance(
                    data.source, BaseOutputTransport
                ):
                    events.append(("marker_left_output",))
                await super().on_push_frame(data)

        tracker = RecordingTracker()
        marker_id = tracker.new_marker()
        await run_test(
            StubOutputTransport(),
            frames_to_send=[
                OutputAudioRawFrame(
                    audio=b"\x01" * 640,
                    sample_rate=8000,
                    num_channels=1,
                ),
                BOT_MODULE.SpeechMarkerFrame(marker_id=marker_id),
            ],
            observers=[tracker],
        )
        assert await tracker.wait_for_marker(marker_id, timeout=0.1) == "played"

        audio_indices = [index for index, event in enumerate(events) if event[0] == "audio_written"]
        marker_index = events.index(("marker_written",))
        marker_left_index = events.index(("marker_left_output",))
        assert audio_indices
        assert max(audio_indices) < marker_index < marker_left_index

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
    "tool_name",
    ["check_availability", "book_appointment"],
)
def test_booking_holding_line_precedes_result_for_slow_tool(tmp_path, tool_name) -> None:
    async def run():
        events = []
        tracker = FastBotSpeechTracker(marker_timeout=0.5)
        handler, params, sink, result_calls, message = _booking_handler(
            tmp_path,
            events,
            tracker,
            tool_name,
            delay=0.1,
        )
        await handler(params)
        tool_calls = [
            json.loads(line)
            for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
        ]
        tool_finished = next(
            event[2] for event in events if event[0] == "tool_finished" and event[1] == tool_name
        )
        result_index = next(
            index for index, event in enumerate(events) if event[0] == "result_callback"
        )
        result = result_calls[0][0]
        await params.tasks.cancel_all()
        return events, tool_calls, result_index, result, tool_finished, result_calls, message

    events, tool_calls, result_index, result, tool_finished, result_calls, message = asyncio.run(
        run()
    )
    frame_index = next(
        index for index, event in enumerate(events) if isinstance(event[1], TTSSpeakFrame)
    )
    marker_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event[1], BOT_MODULE.SpeechMarkerFrame)
    )

    assert events[frame_index][1].text == message
    assert marker_index == frame_index + 1 < result_index
    assert [entry["name"] for entry in tool_calls] == [tool_name]
    assert len(result_calls) == 1
    assert result == {"ok": True}
    assert 0 <= result_calls[0][1] - tool_finished < 0.05


def test_booking_with_bot_already_speaking_returns_before_marker(tmp_path) -> None:
    async def run():
        events = []
        tracker = FastBotSpeechTracker(marker_timeout=0.5)
        await tracker.on_push_frame(SimpleNamespace(frame=BotStartedSpeakingFrame()))
        handler, params, sink, result_calls, message = _booking_handler(
            tmp_path,
            events,
            tracker,
            "book_appointment",
        )
        started_at = time.monotonic()
        await handler(params)
        elapsed = time.monotonic() - started_at
        pending_marker_ids = set(tracker._markers)
        tool_calls = [
            json.loads(line)
            for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
        ]
        await params.tasks.cancel_all()
        return events, result_calls, elapsed, pending_marker_ids, tool_calls, message

    events, result_calls, elapsed, pending_marker_ids, tool_calls, message = asyncio.run(run())
    frame_index = next(
        index for index, event in enumerate(events) if isinstance(event[1], TTSSpeakFrame)
    )
    marker_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event[1], BOT_MODULE.SpeechMarkerFrame)
    )
    result_index = next(
        index for index, event in enumerate(events) if event[0] == "result_callback"
    )

    assert elapsed < 0.1
    assert len(result_calls) == 1
    assert marker_index == frame_index + 1 < result_index
    assert events[frame_index][1].text == message
    assert pending_marker_ids
    assert [entry["name"] for entry in tool_calls] == ["book_appointment"]


def test_booking_does_not_wait_for_speech_that_starts_after_call(tmp_path) -> None:
    async def run():
        events = []
        tracker = FastBotSpeechTracker(marker_timeout=0.5)
        assert not tracker.speaking
        handler, params, sink, result_calls, message = _booking_handler(
            tmp_path,
            events,
            tracker,
            "book_appointment",
            start_speaking_on_holding=True,
        )
        started_at = time.monotonic()
        await handler(params)
        elapsed = time.monotonic() - started_at
        pending_marker_ids = set(tracker._markers)
        tool_calls = [
            json.loads(line)
            for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
        ]
        await params.tasks.cancel_all()
        return (
            events,
            result_calls,
            elapsed,
            tracker.speaking,
            pending_marker_ids,
            tool_calls,
            message,
        )

    events, result_calls, elapsed, speaking, pending_marker_ids, tool_calls, message = asyncio.run(
        run()
    )
    frame_index = next(
        index for index, event in enumerate(events) if isinstance(event[1], TTSSpeakFrame)
    )
    marker_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event[1], BOT_MODULE.SpeechMarkerFrame)
    )
    result_index = next(
        index for index, event in enumerate(events) if event[0] == "result_callback"
    )

    assert elapsed < 0.1
    assert speaking
    assert len(result_calls) == 1
    assert marker_index == frame_index + 1 < result_index
    assert events[frame_index][1].text == message
    assert pending_marker_ids
    assert [entry["name"] for entry in tool_calls] == ["book_appointment"]


def test_cancelled_booking_dispatch_records_late_context_result(tmp_path) -> None:
    async def run():
        events = []
        tracker = FastBotSpeechTracker(marker_timeout=0.5)
        handler, params, sink, result_calls, _message = _booking_handler(
            tmp_path,
            events,
            tracker,
            "book_appointment",
            delay=0.1,
        )
        context = params.context
        handler_task = asyncio.create_task(handler(params))
        while not any(event[0] == "tool_started" for event in events):
            await asyncio.sleep(0)
        handler_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler_task
        await asyncio.wait_for(context.message_added.wait(), timeout=1.0)
        tool_calls = [
            json.loads(line)
            for line in (sink.directory / "tool_calls.jsonl").read_text().splitlines()
        ]
        await params.tasks.cancel_all()
        return (
            events,
            context.messages,
            result_calls,
            tool_calls,
            params.tool_call_id,
            params.booking_records,
        )

    events, messages, result_calls, tool_calls, tool_call_id, booking_records = asyncio.run(run())

    assert not any(event[0] == "tool_cancelled" for event in events)
    assert sum(event[0] == "tool_started" for event in events) == 1
    assert sum(event[0] == "tool_finished" for event in events) == 1
    assert len(tool_calls) == 1
    assert len(booking_records) == 1
    assert result_calls == []
    assert len(messages) == 1
    assert messages[0]["role"] == "developer"
    payload = json.loads(messages[0]["content"])
    assert payload["type"] == "async_tool"
    assert payload["tool_call_id"] == tool_call_id
    late_result = json.loads(payload["result"])
    assert late_result["result"] == {"ok": True}
    assert late_result["note"] == "Booking dispatch completed after the caller interrupted."


def test_second_booking_dispatch_waits_for_cancelled_first_call(tmp_path) -> None:
    async def run():
        events = []
        tracker = FastBotSpeechTracker(marker_timeout=0.5)
        handler, params, _sink, result_calls, _message = _booking_handler(
            tmp_path,
            events,
            tracker,
            "book_appointment",
            delay=0.1,
        )
        first_handler = asyncio.create_task(handler(params))
        while not any(event[0] == "tool_started" for event in events):
            await asyncio.sleep(0)
        second_handler = asyncio.create_task(handler(params))
        await asyncio.sleep(0.01)
        assert sum(event[0] == "tool_started" for event in events) == 1
        first_handler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_handler
        await second_handler
        await asyncio.wait_for(params.context.message_added.wait(), timeout=1.0)
        starts = [index for index, event in enumerate(events) if event[0] == "tool_started"]
        finishes = [index for index, event in enumerate(events) if event[0] == "tool_finished"]
        await params.tasks.cancel_all()
        return events, starts, finishes, result_calls

    events, starts, finishes, result_calls = asyncio.run(run())

    assert len(starts) == len(finishes) == 2
    assert starts[0] < finishes[0] < starts[1] < finishes[1]
    assert len(result_calls) == 1
    assert not any(event[0] == "tool_cancelled" for event in events)


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
