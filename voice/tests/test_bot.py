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
    TTSSpeakFrame,
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


def test_goodbye_fallback_decision_is_pure() -> None:
    decide = BOT_MODULE.should_goodbye_detected_end_call

    assert decide("That's everything, thank you.", "Goodbye.")
    assert decide("Thank you, bye.", "Bye for now.")
    assert not decide("I have another question.", "Goodbye.")
    assert not decide("That's all.", "I can help with anything else.")
    assert not decide("Goodbye.", "Bye.", end_call_received=True)


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

        await push(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.03)
        assert isinstance(queued_frames[0], TTSSpeakFrame)
        assert queued_frames[0].text == "Are you still there?"

        await push(BotStartedSpeakingFrame())
        await push(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.03)
        assert queued_frames[1].text == ("I'll end the call now. Thank you for calling. Goodbye.")

        await push(BotStartedSpeakingFrame())
        await push(BotStoppedSpeakingFrame())
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
