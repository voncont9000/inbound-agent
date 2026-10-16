import asyncio
import importlib.util
from pathlib import Path

from pipecat.frames.frames import EndWorkerFrame
from pipecat.processors.frame_processor import FrameDirection

from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools

BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"
BOT_SPEC = importlib.util.spec_from_file_location("voice_bot", BOT_PATH)
assert BOT_SPEC is not None and BOT_SPEC.loader is not None
BOT_MODULE = importlib.util.module_from_spec(BOT_SPEC)
BOT_SPEC.loader.exec_module(BOT_MODULE)


def test_end_call_sends_end_worker_after_tool_result(tmp_path) -> None:
    events = []

    class FakeLLM:
        async def push_frame(self, frame, direction) -> None:
            events.append(("frame", frame, direction))

    class FakeParams:
        arguments = {}
        llm = FakeLLM()

        async def result_callback(self, result) -> None:
            events.append(("result", result))

    handler = next(
        schema.handler
        for schema in BOT_MODULE.create_function_schemas(
            CallTools(LocalFilesCallSink(tmp_path, "end-call"))
        )
        if schema.name == "end_call"
    )

    asyncio.run(handler(FakeParams()))

    assert events[0] == ("result", {"ok": True, "status": "The call may now end."})
    assert isinstance(events[1][1], EndWorkerFrame)
    assert events[1][2] is FrameDirection.UPSTREAM
