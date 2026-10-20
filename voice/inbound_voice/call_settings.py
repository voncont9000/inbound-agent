from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any

from pipecat.frames.frames import EndWorkerFrame, Frame, TTSSpeakFrame
from pipecat.observers.base_observer import BaseObserver
from pipecat.turns.user_mute import (
    BaseUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)

IDLE_PROMPT_DEFAULT_SECONDS = 8.0
NUMBER_HOLD_DEFAULT_SECS = 2.0
SMART_TURN_STOP_DEFAULT_SECS = 1.5


class TransferUserMuteStrategy(BaseUserMuteStrategy):
    def __init__(self, call_tools: Any) -> None:
        super().__init__()
        self._call_tools = call_tools

    async def process_frame(self, _frame: Frame) -> bool:
        return self._call_tools.transfer_accepted


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


def build_user_mute_strategies(
    call_tools: Any,
    *,
    uninterruptible_greeting: bool,
    recording_enabled: bool,
) -> list[BaseUserMuteStrategy]:
    strategies: list[BaseUserMuteStrategy] = [TransferUserMuteStrategy(call_tools)]
    if uninterruptible_greeting or recording_enabled:
        strategies.insert(0, MuteUntilFirstBotCompleteUserMuteStrategy())
    return strategies


async def wait_for_max_call_duration(
    seconds: float,
    message: str,
    *,
    queue_frame: Any,
    new_marker: Any,
    marker_frame: Any,
    wait_for_marker: Any,
    call_ended: Any,
    transfer_in_progress: Any,
    append_task: Any,
) -> None:
    await asyncio.sleep(seconds)
    if call_ended() or transfer_in_progress():
        return
    marker_id = new_marker()
    await queue_frame(TTSSpeakFrame(message))
    await queue_frame(marker_frame(marker_id))
    await wait_for_marker(marker_id, timeout=15.0)
    append_task("end_call", {"source": "max_duration"})
    await queue_frame(EndWorkerFrame())


class MaxCallDurationObserver(BaseObserver):
    def __init__(
        self,
        seconds: float | None,
        message: str,
        *,
        queue_frame: Any,
        speech_tracker: Any,
        marker_frame: Any,
        call_tools: Any,
        sink: Any,
    ) -> None:
        super().__init__(observe_every_push=False)
        self.seconds = seconds
        self.message = message
        self.queue_frame = queue_frame
        self.speech_tracker = speech_tracker
        self.marker_frame = marker_frame
        self.call_tools = call_tools
        self.sink = sink
        self.pending: asyncio.Task[None] | None = None
        self.ended = False

    def start(self) -> None:
        if self.seconds is None or self.pending is not None:
            return
        self.pending = asyncio.create_task(
            wait_for_max_call_duration(
                self.seconds,
                self.message,
                queue_frame=self.queue_frame,
                new_marker=self.speech_tracker.new_marker,
                marker_frame=self.marker_frame,
                wait_for_marker=self.speech_tracker.wait_for_marker,
                call_ended=lambda: self.ended or self.call_tools.end_call_requested,
                transfer_in_progress=lambda: self.call_tools.transfer_request is not None,
                append_task=self.sink.append_task,
            )
        )

    async def on_push_frame(self, data: Any) -> None:
        if isinstance(data.frame, EndWorkerFrame):
            self.ended = True
            self.cancel()

    def cancel(self) -> None:
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None

    def close(self) -> None:
        self.ended = True
        self.cancel()


__all__ = [
    "IDLE_PROMPT_DEFAULT_SECONDS",
    "MaxCallDurationObserver",
    "NUMBER_HOLD_DEFAULT_SECS",
    "SMART_TURN_STOP_DEFAULT_SECS",
    "TransferUserMuteStrategy",
    "build_user_mute_strategies",
    "idle_prompt_secs",
    "number_hold_secs",
    "smart_turn_stop_secs",
    "wait_for_max_call_duration",
]
