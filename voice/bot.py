import asyncio
import json
import math
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    FunctionCallResultProperties,
    LLMRunFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.run import main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from inbound_voice.config import load_assistant_config
from inbound_voice.extraction import (
    DEFAULT_EXTRACTION_MODEL,
    build_intake_artifacts,
    build_post_call_tasks,
    extract_intake,
)
from inbound_voice.metrics import CallMetricsObserver
from inbound_voice.prompts import render_system_prompt
from inbound_voice.providers import create_llm, create_stt, create_tts, resolve_providers
from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools, tool_specs

ROOT = Path(__file__).resolve().parent
ASSISTANT_CONFIG = ROOT / "config" / "assistants" / "demo_firm.yaml"
EXTRACTION_TIMEOUT_SECONDS = 60
GOODBYE_HANGUP_SECONDS = 1.5
IDLE_PROMPT_DEFAULT_SECONDS = 8.0
IDLE_PROMPT_TEXT = "Are you still there?"
IDLE_GOODBYE_TEXT = "I'll end the call now. Thank you for calling. Goodbye."
CLOSING_PHRASE = re.compile(
    r"\b(?:goodbye|bye|that['’]s all|that['’]s everything|thank you,\s*bye)\b",
    re.IGNORECASE,
)
ASSISTANT_GOODBYE = re.compile(r"\b(?:goodbye|bye)\b", re.IGNORECASE)


def should_goodbye_detected_end_call(
    caller_closing_seen: bool,
    assistant_text: str,
    *,
    end_call_received: bool = False,
) -> bool:
    if end_call_received:
        return False
    if caller_closing_seen and ASSISTANT_GOODBYE.search(assistant_text) is not None:
        return True
    sentences = [
        sentence.strip() for sentence in re.split(r"[.!?]", assistant_text) if sentence.strip()
    ]
    return bool(
        sentences
        and re.search(
            r"\b(?:goodbye|bye)(?:\s+for now)?\s*$",
            sentences[-1],
            re.IGNORECASE,
        )
    )


class GoodbyeDetectedHangup(BaseObserver):
    def __init__(
        self,
        sink: LocalFilesCallSink,
        call_tools: CallTools,
        push_end_worker: Any,
        delay_seconds: float = GOODBYE_HANGUP_SECONDS,
    ) -> None:
        super().__init__(observe_every_push=False)
        self.sink = sink
        self.call_tools = call_tools
        self.push_end_worker = push_end_worker
        self.delay_seconds = delay_seconds
        self.caller_closing_seen = False
        self.assistant_text: list[str] = []
        self.pending: asyncio.Task[None] | None = None
        self._generation = 0

    async def on_push_frame(self, data: Any) -> None:
        frame = data.frame
        if isinstance(frame, EndWorkerFrame):
            self._cancel_pending()
        elif isinstance(frame, TranscriptionFrame) and getattr(frame, "finalized", True):
            if CLOSING_PHRASE.search(frame.text) is not None:
                self.caller_closing_seen = True
            self.assistant_text.clear()
        elif isinstance(frame, TTSTextFrame):
            self.assistant_text.append(frame.text)
        elif isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self._cancel_pending()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            assistant_text = " ".join(self.assistant_text)
            self.assistant_text.clear()
            if should_goodbye_detected_end_call(
                self.caller_closing_seen,
                assistant_text,
                end_call_received=self.call_tools.end_call_requested,
            ):
                self._schedule()

    def close(self) -> None:
        self._cancel_pending()

    def _cancel_pending(self) -> None:
        self._generation += 1
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None

    def _schedule(self) -> None:
        self._cancel_pending()
        generation = self._generation
        self.pending = asyncio.create_task(self._finish_after_delay(generation))

    async def _finish_after_delay(self, generation: int) -> None:
        try:
            await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            return
        if generation != self._generation or self.call_tools.end_call_requested:
            return
        self.sink.append_task("end_call", {"source": "goodbye_detected"})
        await self.push_end_worker()


class IdleHangupObserver(BaseObserver):
    def __init__(
        self,
        sink: LocalFilesCallSink,
        queue_frame: Any,
        push_end_worker: Any,
        idle_seconds: float = IDLE_PROMPT_DEFAULT_SECONDS,
    ) -> None:
        super().__init__(observe_every_push=False)
        self.sink = sink
        self.queue_frame = queue_frame
        self.push_end_worker = push_end_worker
        self.idle_seconds = idle_seconds
        self.pending: asyncio.Task[None] | None = None
        self._generation = 0
        self._phase = "conversation"
        self._bot_speaking = False
        self._caller_speaking = False
        self._seen_speaking_frame_siblings: set[frozenset[Any]] = set()

    async def on_push_frame(self, data: Any) -> None:
        frame = data.frame
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            sibling_ids = frozenset((frame.id, getattr(frame, "broadcast_sibling_id", None)))
            if sibling_ids in self._seen_speaking_frame_siblings:
                return
            self._seen_speaking_frame_siblings.add(sibling_ids)
        if isinstance(frame, EndWorkerFrame):
            self._cancel_timer()
            self._phase = "done"
        elif isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self._caller_speaking = True
            self._cancel_timer()
            if self._phase != "done":
                self._phase = "conversation"
        elif isinstance(frame, (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            self._caller_speaking = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._phase == "prompt_queued":
                self._phase = "prompt_speaking"
            elif self._phase == "goodbye_queued":
                self._phase = "goodbye_speaking"
            elif self._phase != "done":
                self._cancel_timer()
                self._phase = "conversation"
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._phase == "prompt_speaking":
                self._schedule("waiting_goodbye", "goodbye_queued", IDLE_GOODBYE_TEXT)
            elif self._phase == "goodbye_speaking":
                self._phase = "done"
                self.sink.append_task("end_call", {"source": "idle"})
                await self.push_end_worker()
            elif self._phase not in {"prompt_queued", "goodbye_queued", "done"}:
                if not self._caller_speaking:
                    self._schedule("waiting_prompt", "prompt_queued", IDLE_PROMPT_TEXT)

    def close(self) -> None:
        self._cancel_timer()
        self._phase = "done"

    def _cancel_timer(self) -> None:
        self._generation += 1
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None

    def _schedule(self, waiting_phase: str, queued_phase: str, text: str) -> None:
        self._cancel_timer()
        self._phase = waiting_phase
        generation = self._generation
        self.pending = asyncio.create_task(
            self._speak_after_idle(generation, waiting_phase, queued_phase, text)
        )

    async def _speak_after_idle(
        self,
        generation: int,
        waiting_phase: str,
        queued_phase: str,
        text: str,
    ) -> None:
        try:
            await asyncio.sleep(self.idle_seconds)
        except asyncio.CancelledError:
            return
        if (
            generation != self._generation
            or self._phase != waiting_phase
            or self._bot_speaking
            or self._caller_speaking
        ):
            return
        self.pending = None
        self._phase = queued_phase
        await self.queue_frame(TTSSpeakFrame(text))


def smart_turn_stop_secs(env: Mapping[str, str]) -> float:
    try:
        stop_secs = float(env.get("SMART_TURN_STOP_SECS", "0.7").strip())
    except ValueError as error:
        raise ValueError("SMART_TURN_STOP_SECS must be a positive number.") from error
    if not math.isfinite(stop_secs) or stop_secs <= 0:
        raise ValueError("SMART_TURN_STOP_SECS must be a positive number.")
    return stop_secs


def idle_prompt_secs(env: Mapping[str, str]) -> float:
    try:
        idle_seconds = float(env.get("IDLE_PROMPT_SECS", str(IDLE_PROMPT_DEFAULT_SECONDS)).strip())
    except ValueError as error:
        raise ValueError("IDLE_PROMPT_SECS must be a positive number.") from error
    if not math.isfinite(idle_seconds) or idle_seconds <= 0:
        raise ValueError("IDLE_PROMPT_SECS must be a positive number.")
    return idle_seconds


def create_function_schemas(call_tools: CallTools) -> list[FunctionSchema]:
    schemas = []
    for spec in tool_specs(call_tools.config):

        async def handler(params: FunctionCallParams, tool_name: str = spec["name"]) -> None:
            result = call_tools.dispatch(tool_name, params.arguments)
            if tool_name == "end_call":
                await params.result_callback(
                    result,
                    properties=FunctionCallResultProperties(run_llm=False),
                )
            else:
                await params.result_callback(result)
            if tool_name == "end_call":
                await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)

        schemas.append(
            FunctionSchema(
                name=spec["name"],
                description=spec["description"],
                properties=spec["properties"],
                required=spec["required"],
                handler=handler,
            )
        )
    return schemas


def create_llm_context(call_tools: CallTools) -> LLMContext:
    schemas = create_function_schemas(call_tools)
    return LLMContext(tools=schemas) if schemas else LLMContext()


async def run_bot(transport: object, telephony: bool = False) -> None:
    providers = resolve_providers(os.environ)
    call_id = str(uuid.uuid4())
    sink = LocalFilesCallSink(ROOT / "runs", call_id)
    assistant_path = Path(os.environ.get("ASSISTANT_CONFIG", str(ASSISTANT_CONFIG)))
    assistant = load_assistant_config(assistant_path)
    call_tools = CallTools(sink, assistant)
    metrics_observer = CallMetricsObserver(
        sink,
        telephony=telephony,
        caller_text_callback=call_tools.check_caller_text,
        providers={
            "stt": providers.stt,
            "tts": providers.tts,
            "tts_voice": providers.tts_voice,
            "llm": providers.llm,
            "llm_model": providers.llm_model,
        },
    )

    async with aiohttp.ClientSession() as session:
        stt = create_stt(providers, os.environ)
        llm = create_llm(providers, os.environ, render_system_prompt(assistant))
        tts = create_tts(providers, os.environ, session)
        context = create_llm_context(call_tools)
        context_aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(),
                user_turn_strategies=UserTurnStrategies(
                    start=[VADUserTurnStartStrategy(enable_interruptions=True)],
                    stop=[
                        TurnAnalyzerUserTurnStopStrategy(
                            turn_analyzer=LocalSmartTurnAnalyzerV3(
                                params=SmartTurnParams(stop_secs=smart_turn_stop_secs(os.environ))
                            )
                        )
                    ],
                ),
            ),
        )
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregators.user(),
                llm,
                tts,
                transport.output(),
                context_aggregators.assistant(),
            ]
        )
        task: PipelineTask

        async def push_end_worker() -> None:
            await task.queue_frame(EndWorkerFrame())

        async def queue_frame(frame: Any) -> None:
            await task.queue_frame(frame)

        goodbye_hangup = GoodbyeDetectedHangup(sink, call_tools, push_end_worker)
        idle_hangup = IdleHangupObserver(
            sink,
            queue_frame,
            push_end_worker,
            idle_seconds=idle_prompt_secs(os.environ),
        )
        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[metrics_observer, goodbye_hangup, idle_hangup],
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport: object, _client: object) -> None:
            await task.queue_frame(LLMRunFrame())

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport: object, _client: object) -> None:
            await task.cancel()

        try:
            await PipelineRunner().run(task)
        finally:
            goodbye_hangup.close()
            idle_hangup.close()
            await finalize_call_intake(sink, assistant, metrics_observer, os.environ)
            metrics_observer.persist()


async def finalize_call_intake(
    sink: LocalFilesCallSink,
    assistant: Any,
    metrics_observer: CallMetricsObserver,
    env: Mapping[str, str],
) -> dict[str, Any]:
    model = env.get("EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL).strip()
    key = env.get("GOOGLE_API_KEY", "").strip()
    live_path = sink.directory / "intake.live.json"
    live = (
        json.loads(live_path.read_text(encoding="utf-8"))
        if live_path.exists()
        else {
            "service": None,
            "fields": {},
        }
    )
    transcript_path = sink.directory / "transcript.jsonl"
    transcript_rows = [
        json.loads(line)
        for line in transcript_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    try:
        client = None
        if model and key:
            from google import genai

            client = genai.Client(api_key=key)
        result = await asyncio.wait_for(
            extract_intake(transcript_rows, live, assistant, client, model),
            timeout=EXTRACTION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        result = {
            "status": "failed",
            "service": live.get("service"),
            "model": model,
            "fields": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": "Extraction exceeded the 60 second timeout.",
        }
    except Exception as error:
        result = {
            "status": "failed",
            "service": live.get("service"),
            "model": model,
            "fields": {},
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": f"{type(error).__name__}: {error}",
        }

    post_call, merged = build_intake_artifacts(live, result, assistant)
    tasks_path = sink.directory / "tasks.json"
    existing_tasks = (
        json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else []
    )
    for task in build_post_call_tasks(post_call, merged, assistant, existing_tasks):
        task_details = {key: value for key, value in task.items() if key != "type"}
        sink.append_task(task["type"], task_details)
    sink.save_post_call_intake(post_call)
    sink.save_intake(merged)
    metrics_observer.record_extraction(model, result["status"], result.get("usage"))
    return merged


async def bot(runner_args: RunnerArguments) -> None:
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
            "twilio": lambda: FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        },
    )
    await run_bot(transport, telephony=getattr(runner_args, "transport_type", None) == "twilio")


if __name__ == "__main__":
    main()
