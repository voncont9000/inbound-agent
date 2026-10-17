import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
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
        await asyncio.sleep(0.03)
        assert unresolved.stop_calls == 1
        assert metrics.holds[-1]["resumed"] is False
        assert metrics.holds[-1]["held_secs"] >= 0.01

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


def test_empty_live_actions_create_context_without_tools(tmp_path) -> None:
    context = BOT_MODULE.create_llm_context(CallTools(LocalFilesCallSink(tmp_path, "no-tools")))

    assert context.tools is NOT_GIVEN


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
