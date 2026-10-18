import asyncio
import json
import math
import os
import re
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger
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

from inbound_voice.booking import CallBooking
from inbound_voice.call_report import build_call_report
from inbound_voice.call_setup import PreparedCall, prepare_call
from inbound_voice.config import load_assistant_config
from inbound_voice.extraction import (
    DEFAULT_EXTRACTION_MODEL,
    build_intake_artifacts,
    build_knowledge_gap_tasks,
    build_post_call_tasks,
    extract_intake,
)
from inbound_voice.metrics import CallMetricsObserver
from inbound_voice.prompts import render_system_prompt
from inbound_voice.providers import create_llm, create_stt, create_tts, resolve_providers
from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools, tool_specs
from inbound_voice.turns import incomplete_phone_number

ROOT = Path(__file__).resolve().parent
ASSISTANT_CONFIG = ROOT / "config" / "assistants" / "demo_firm.yaml"
PHONE_SAMPLE_RATE = 8000
WEB_SAMPLE_RATE = 16000
EXTRACTION_TIMEOUT_SECONDS = 60
GOODBYE_HANGUP_SECONDS = 1.5
IDLE_PROMPT_DEFAULT_SECONDS = 8.0
NUMBER_HOLD_DEFAULT_SECS = 2.0
SMART_TURN_STOP_DEFAULT_SECS = 0.7
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
        stop_secs = float(
            env.get("SMART_TURN_STOP_SECS", str(SMART_TURN_STOP_DEFAULT_SECS)).strip()
        )
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


def number_hold_secs(env: Mapping[str, str]) -> float:
    try:
        hold_secs = float(env.get("NUMBER_HOLD_SECS", str(NUMBER_HOLD_DEFAULT_SECS)).strip())
    except ValueError as error:
        raise ValueError("NUMBER_HOLD_SECS must be a positive number.") from error
    if not math.isfinite(hold_secs) or hold_secs <= 0:
        raise ValueError("NUMBER_HOLD_SECS must be a positive number.")
    return hold_secs


class IncompletePhoneNumberTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    def __init__(
        self,
        *,
        turn_analyzer: LocalSmartTurnAnalyzerV3,
        hold_seconds: float,
        metrics_observer: CallMetricsObserver,
    ) -> None:
        super().__init__(turn_analyzer=turn_analyzer)
        self._hold_seconds = hold_seconds
        self._metrics_observer = metrics_observer
        self._turn_text = ""
        self._number_hold_task: asyncio.Task[None] | None = None
        self._number_hold_record: dict[str, Any] | None = None
        self._number_hold_started_at: float | None = None

    async def process_frame(self, frame: Any) -> Any:
        if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            await self._finish_number_hold(resumed=True)
        if isinstance(frame, TranscriptionFrame) and getattr(frame, "finalized", True):
            self._append_turn_text(frame.text)
        return await super().process_frame(frame)

    async def handle_user_turn_started(self) -> None:
        self._turn_text = ""
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self) -> None:
        await self._finish_number_hold(resumed=False)
        self._turn_text = ""
        await super().handle_user_turn_stopped()

    async def cleanup(self) -> None:
        await self._finish_number_hold(resumed=False)
        await super().cleanup()

    async def trigger_user_turn_stopped(self, *args: Any, **kwargs: Any) -> None:
        if self._number_hold_task is not None:
            logger.info(
                "{} blocked user-turn finalization while holding phone text: turn_text={!r}, "
                "latest_text={!r}",
                self,
                self._turn_text,
                self._text,
            )
            return
        await super().trigger_user_turn_stopped(*args, **kwargs)

    async def _maybe_trigger_user_turn_stopped(self) -> None:
        has_transcript = bool(self._text) and (
            self._transcript_finalized or self._timeout_task is None
        )
        ready_to_stop = self._turn_complete and (not self._wait_for_transcript or has_transcript)
        if ready_to_stop:
            is_incomplete_number = incomplete_phone_number(self._turn_text)
            logger.info(
                "{} phone-number stop decision: turn_text={!r}, latest_text={!r}, incomplete={}",
                self,
                self._turn_text,
                self._text,
                is_incomplete_number,
            )
            if self._number_hold_task is not None:
                return
        else:
            is_incomplete_number = False
        if ready_to_stop and is_incomplete_number:
            self._number_hold_record = self._metrics_observer.start_number_hold()
            self._number_hold_started_at = time.monotonic()
            self._number_hold_task = asyncio.create_task(self._wait_for_number_hold())
            return
        await super()._maybe_trigger_user_turn_stopped()

    def _append_turn_text(self, text: str) -> None:
        words = text.split()
        if not words:
            return
        previous_words = self._turn_text.split()
        for overlap in range(min(len(previous_words), len(words)), 0, -1):
            if previous_words[-overlap:] == words[:overlap]:
                self._turn_text = " ".join([*previous_words, *words[overlap:]])
                return
        self._turn_text = " ".join([*previous_words, *words])

    async def _wait_for_number_hold(self) -> None:
        try:
            await asyncio.sleep(self._hold_seconds)
        except asyncio.CancelledError:
            return
        if self._number_hold_task is not asyncio.current_task():
            return
        record = self._number_hold_record
        started_at = self._number_hold_started_at
        self._number_hold_task = None
        self._number_hold_record = None
        self._number_hold_started_at = None
        if record is not None and started_at is not None:
            self._metrics_observer.finish_number_hold(
                record,
                resumed=False,
                held_secs=time.monotonic() - started_at,
            )
        await super()._maybe_trigger_user_turn_stopped()

    async def _finish_number_hold(self, *, resumed: bool) -> None:
        task = self._number_hold_task
        record = self._number_hold_record
        started_at = self._number_hold_started_at
        if task is None or record is None or started_at is None:
            return
        self._number_hold_task = None
        self._number_hold_record = None
        self._number_hold_started_at = None
        if task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._metrics_observer.finish_number_hold(
            record,
            resumed=resumed,
            held_secs=time.monotonic() - started_at,
        )


def create_function_schemas(
    call_tools: CallTools,
    metrics_observer: CallMetricsObserver | None = None,
) -> list[FunctionSchema]:
    schemas = []
    for spec in tool_specs(call_tools.config, booking_available=call_tools.booking_available):

        async def handler(params: FunctionCallParams, tool_name: str = spec["name"]) -> None:
            started = time.perf_counter()
            result = await call_tools.dispatch_async(tool_name, params.arguments)
            if metrics_observer is not None and tool_name in {
                "check_availability",
                "book_appointment",
            }:
                metrics_observer.record_tool_round_trip(
                    tool_name, (time.perf_counter() - started) * 1000
                )
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


def create_llm_context(
    call_tools: CallTools,
    metrics_observer: CallMetricsObserver | None = None,
) -> LLMContext:
    schemas = create_function_schemas(call_tools, metrics_observer)
    return LLMContext(tools=schemas) if schemas else LLMContext()


async def run_bot(
    transport: object,
    telephony: bool = False,
    caller_id: str | None = None,
    prepared: PreparedCall | None = None,
    call_id: str | None = None,
) -> None:
    if prepared is None:
        prepared = PreparedCall(
            assistant=load_assistant_config(
                Path(os.environ.get("ASSISTANT_CONFIG", str(ASSISTANT_CONFIG)))
            ),
            context_card=None,
            api=None,
            call_start=None,
            route=None,
        )
    providers = resolve_providers(os.environ)
    run_id = str(uuid.uuid4())
    call_started_at = (
        prepared.call_start.started_at if prepared.call_start is not None else datetime.now(UTC)
    )
    sink = LocalFilesCallSink(ROOT / "runs", run_id, started_at=call_started_at)
    assistant = prepared.assistant
    external_call_id = (
        prepared.call_start.external_call_id
        if prepared.call_start is not None
        else (call_id or run_id)
    )
    input_sample_rate = PHONE_SAMPLE_RATE if telephony else WEB_SAMPLE_RATE
    output_sample_rate = PHONE_SAMPLE_RATE if telephony else 24000
    booking_available = bool(
        prepared.route is not None
        and prepared.route.booking_available
        and assistant.booking is not None
        and prepared.api is not None
        and prepared.call_start is not None
    )
    call_tools = CallTools(sink, assistant, booking_available=booking_available)
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
    start_call_task: asyncio.Task[uuid.UUID | None] | None = None

    async def conversation_id_getter() -> uuid.UUID | None:
        return await start_call_task if start_call_task is not None else None

    if booking_available:
        assert prepared.api is not None and prepared.call_start is not None
        call_tools.booking = CallBooking(
            assistant,
            prepared.api,
            prepared.call_start,
            prepared.availability_task,
            conversation_id_getter,
            prepared.call_start.from_number,
            metrics_observer,
        )

    async with aiohttp.ClientSession() as session:
        stt = create_stt(providers, os.environ, sample_rate=input_sample_rate)
        llm = create_llm(
            providers,
            os.environ,
            render_system_prompt(
                assistant,
                caller_id=caller_id,
                context_card=prepared.context_card,
                booking_enabled=booking_available,
            ),
        )
        tts = create_tts(providers, os.environ, session, sample_rate=output_sample_rate)
        context = create_llm_context(call_tools, metrics_observer)
        context_aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(sample_rate=input_sample_rate),
                user_turn_strategies=UserTurnStrategies(
                    start=[VADUserTurnStartStrategy(enable_interruptions=True)],
                    stop=[
                        IncompletePhoneNumberTurnStopStrategy(
                            turn_analyzer=LocalSmartTurnAnalyzerV3(
                                sample_rate=input_sample_rate,
                                params=SmartTurnParams(stop_secs=smart_turn_stop_secs(os.environ)),
                            ),
                            hold_seconds=number_hold_secs(os.environ),
                            metrics_observer=metrics_observer,
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
                audio_in_sample_rate=input_sample_rate,
                audio_out_sample_rate=output_sample_rate,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[metrics_observer, goodbye_hangup, idle_hangup],
        )
        if prepared.api is not None and prepared.call_start is not None:

            async def start_call_safely() -> uuid.UUID | None:
                try:
                    return await prepared.api.start_call(prepared.call_start)
                except Exception as error:
                    logger.warning("Could not start voice API call: {}", error)
                    return None

            start_call_task = asyncio.create_task(start_call_safely())

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
            call_ended_at = datetime.now(UTC)
            sink.save_call_times(call_started_at, call_ended_at)
            await finalize_call_intake(
                sink,
                assistant,
                metrics_observer,
                os.environ,
                caller_id=caller_id,
                call_started_at=call_started_at,
                call_ended_at=call_ended_at,
            )
            metrics_observer.persist()
            if prepared.api is not None and prepared.call_start is not None:
                conversation_id = None
                if start_call_task is not None:
                    try:
                        conversation_id = await start_call_task
                    except Exception as error:
                        logger.warning("Could not complete voice API call start: {}", error)
                try:
                    report = build_call_report(
                        sink.directory,
                        external_call_id,
                        call_ended_at,
                    )
                    await prepared.api.post_report(
                        conversation_id,
                        prepared.call_start,
                        report,
                    )
                except Exception as error:
                    logger.warning("Could not post voice API call report: {}", error)


async def finalize_call_intake(
    sink: LocalFilesCallSink,
    assistant: Any,
    metrics_observer: CallMetricsObserver,
    env: Mapping[str, str],
    caller_id: str | None = None,
    *,
    call_started_at: datetime | None = None,
    call_ended_at: datetime | None = None,
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
            extract_intake(
                transcript_rows,
                live,
                assistant,
                client,
                model,
                call_started_at=call_started_at,
            ),
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
            "unanswered_questions": [],
            "error": f"{type(error).__name__}: {error}",
        }

    post_call, merged = build_intake_artifacts(
        live,
        result,
        assistant,
        caller_id=caller_id,
    )
    tasks_path = sink.directory / "tasks.json"
    existing_tasks = (
        json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else []
    )
    gaps = {"unanswered_questions": result.get("unanswered_questions", [])}
    sink.save_knowledge_gaps(gaps)
    for task in [
        *build_post_call_tasks(
            post_call,
            merged,
            assistant,
            existing_tasks,
            call_ended_at=call_ended_at,
        ),
        *build_knowledge_gap_tasks(gaps["unanswered_questions"]),
    ]:
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
    telephony = getattr(runner_args, "transport_type", None) == "twilio"
    call_data = getattr(runner_args, "call_data", None)
    caller_id = resolve_caller_id(call_data, os.environ, telephony=telephony)
    call_id = (
        _runner_external_call_id(call_data)
        if telephony
        else str(getattr(runner_args, "call_id", None) or uuid.uuid4())
    )
    to_number, from_number = _runner_call_numbers(call_data)
    prepared = await prepare_call(
        os.environ,
        channel="phone" if telephony else "web",
        to_number=to_number if telephony else os.environ.get("WEB_CALL_TO_NUMBER"),
        from_number=from_number or caller_id,
        external_call_id=call_id,
    )
    await run_bot(
        transport,
        telephony=telephony,
        caller_id=caller_id,
        prepared=prepared,
        call_id=call_id,
    )


def _runner_external_call_id(call_data: Any) -> str:
    call_id = (
        call_data.get("call_id")
        if isinstance(call_data, Mapping)
        else getattr(call_data, "call_id", None)
    )
    return str(call_id or uuid.uuid4())


def _runner_call_numbers(call_data: Any) -> tuple[str | None, str | None]:
    if isinstance(call_data, Mapping):
        body = call_data.get("body", {})
    else:
        body = getattr(call_data, "body", {})
    body = body if isinstance(body, Mapping) else {}
    return (
        body.get("To") or body.get("to") or body.get("to_number"),
        body.get("From") or body.get("from") or body.get("from_number"),
    )


def resolve_caller_id(
    call_data: Any,
    env: Mapping[str, str],
    *,
    telephony: bool,
) -> str | None:
    if not telephony:
        override = env.get("CALLER_ID_OVERRIDE", "").strip()
        if override:
            return override
    if call_data is None:
        return None
    body = (
        call_data.get("body", {})
        if isinstance(call_data, Mapping)
        else getattr(call_data, "body", {})
    )
    body = body if isinstance(body, Mapping) else {}
    start = call_data.get("start", {}) if isinstance(call_data, Mapping) else {}
    start = start if isinstance(start, Mapping) else {}
    custom_parameters = start.get("customParameters", {})
    custom_parameters = custom_parameters if isinstance(custom_parameters, Mapping) else {}
    values = [
        body.get("from_number"),
        body.get("From"),
        body.get("from"),
        custom_parameters.get("from_number"),
        custom_parameters.get("From"),
        start.get("From"),
        start.get("from"),
        (
            call_data.get("from")
            if isinstance(call_data, Mapping)
            else getattr(call_data, "from_number", None)
        ),
    ]
    return next((str(value).strip() for value in values if value), None)


if __name__ == "__main__":
    main()
