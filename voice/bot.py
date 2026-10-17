import math
import os
import uuid
from collections.abc import Mapping
from pathlib import Path

import aiohttp
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, LLMRunFrame
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

from inbound_voice.config import load_firm_config
from inbound_voice.metrics import CallMetricsObserver
from inbound_voice.prompts import render_system_prompt
from inbound_voice.providers import create_llm, create_stt, create_tts, resolve_providers
from inbound_voice.sinks import LocalFilesCallSink
from inbound_voice.tools import CallTools, tool_specs

ROOT = Path(__file__).resolve().parent
FIRM_CONFIG = ROOT / "config" / "demo_firm.yaml"


def smart_turn_stop_secs(env: Mapping[str, str]) -> float:
    try:
        stop_secs = float(env.get("SMART_TURN_STOP_SECS", "2.0").strip())
    except ValueError as error:
        raise ValueError("SMART_TURN_STOP_SECS must be a positive number.") from error
    if not math.isfinite(stop_secs) or stop_secs <= 0:
        raise ValueError("SMART_TURN_STOP_SECS must be a positive number.")
    return stop_secs


def create_function_schemas(call_tools: CallTools) -> list[FunctionSchema]:
    schemas = []
    for spec in tool_specs():

        async def handler(params: FunctionCallParams, tool_name: str = spec["name"]) -> None:
            result = call_tools.dispatch(tool_name, params.arguments)
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


async def run_bot(transport: object, telephony: bool = False) -> None:
    providers = resolve_providers(os.environ)
    call_id = str(uuid.uuid4())
    sink = LocalFilesCallSink(ROOT / "runs", call_id)
    metrics_observer = CallMetricsObserver(
        sink,
        telephony=telephony,
        providers={
            "stt": providers.stt,
            "tts": providers.tts,
            "tts_voice": providers.tts_voice,
            "llm": providers.llm,
            "llm_model": providers.llm_model,
        },
    )
    call_tools = CallTools(sink)
    firm = load_firm_config(FIRM_CONFIG)

    async with aiohttp.ClientSession() as session:
        stt = create_stt(providers, os.environ)
        llm = create_llm(providers, os.environ, render_system_prompt(firm))
        tts = create_tts(providers, os.environ, session)
        context = LLMContext(tools=create_function_schemas(call_tools))
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
        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[metrics_observer],
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
            metrics_observer.persist()


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
