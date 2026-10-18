import math
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from inbound_core.pricing import calculate_cost, load_price_table
from pipecat.observers.base_observer import BaseObserver

from inbound_voice.sinks import CallSink


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def summarize_turn_latencies(turns: list[dict[str, Any]]) -> dict[str, float | int | None]:
    return _summarize_latencies(turns, "seconds")


def summarize_turn_end_latencies(turns: list[dict[str, Any]]) -> dict[str, float | int | None]:
    return _summarize_latencies(turns, "turn_end_to_audio_seconds")


def _summarize_latencies(turns: list[dict[str, Any]], field: str) -> dict[str, float | int | None]:
    values = [float(turn[field]) for turn in turns if turn.get(field) is not None]
    return {
        "turn_count": len(values),
        "p50_seconds": percentile(values, 50),
        "p95_seconds": percentile(values, 95),
        "max_seconds": max(values) if values else None,
    }


class TurnLatencyTracker:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self._pending: dict[str, Any] | None = None
        self._next_turn_id = 1

    def user_started_speaking(self) -> None:
        self._pending = None

    def vad_user_stopped_speaking(self, timestamp: float | None = None) -> None:
        if self._pending is None:
            self._pending = self._new_pending_turn()
        if self._pending["vad_user_stopped_speaking_at"] is None:
            self._pending["vad_user_stopped_speaking_at"] = (
                timestamp if timestamp is not None else time.monotonic()
            )

    def number_hold_started(self, timestamp: float | None = None) -> None:
        stopped_at = timestamp if timestamp is not None else time.monotonic()
        if self._pending is None:
            self._pending = self._new_pending_turn()
        vad_stopped_at = self._pending["vad_user_stopped_speaking_at"]
        if vad_stopped_at is None or vad_stopped_at > stopped_at:
            self._pending["vad_user_stopped_speaking_at"] = stopped_at

    def user_stopped_speaking(self, timestamp: float | None = None) -> None:
        if self._pending is None or self._pending["user_stopped_speaking_at"] is not None:
            self._pending = self._new_pending_turn()
        self._pending["user_stopped_speaking_at"] = (
            timestamp if timestamp is not None else time.monotonic()
        )

    def interrupted(self) -> None:
        self._pending = None

    def first_bot_audio(self, timestamp: float | None = None) -> dict[str, Any] | None:
        pending = self._pending
        self._pending = None
        if pending is None or (
            pending["vad_user_stopped_speaking_at"] is None
            and pending["user_stopped_speaking_at"] is None
        ):
            return None

        audio_at = timestamp if timestamp is not None else time.monotonic()
        vad_stopped_at = pending["vad_user_stopped_speaking_at"]
        user_stopped_at = pending["user_stopped_speaking_at"]
        turn = {
            "turn_id": pending["turn_id"],
            "vad_user_stopped_speaking_at": vad_stopped_at,
            "user_stopped_speaking_at": user_stopped_at,
            "first_bot_audio_at": audio_at,
            "seconds": max(0.0, audio_at - vad_stopped_at) if vad_stopped_at is not None else None,
            "turn_end_to_audio_seconds": (
                max(0.0, audio_at - user_stopped_at) if user_stopped_at is not None else None
            ),
        }
        self.turns.append(turn)
        return turn

    def _new_pending_turn(self) -> dict[str, Any]:
        turn = {
            "turn_id": str(self._next_turn_id),
            "vad_user_stopped_speaking_at": None,
            "user_stopped_speaking_at": None,
        }
        self._next_turn_id += 1
        return turn

    def summary(self) -> dict[str, float | int | None]:
        return summarize_turn_latencies(self.turns)

    def turn_end_summary(self) -> dict[str, float | int | None]:
        return summarize_turn_end_latencies(self.turns)


class CallMetricsObserver(BaseObserver):
    """Observe Pipecat frame flow and write call-level latency and usage metrics."""

    def __init__(
        self,
        sink: CallSink,
        telephony: bool = False,
        providers: Mapping[str, str | None] | None = None,
        caller_text_callback: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(observe_every_push=False)
        self.sink = sink
        self.telephony = telephony
        self.providers = dict(providers or {})
        self.caller_text_callback = caller_text_callback
        self.latency = TurnLatencyTracker()
        self._seen_broadcast_frame_ids: set[int] = set()
        self.service_metrics: list[dict[str, Any]] = []
        self.number_holds: list[dict[str, Any]] = []
        self.transfer: dict[str, Any] | None = None
        self.usage: dict[str, float | int] = {
            "stt_audio_seconds": 0,
            "tts_characters": 0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
            "extraction_input_tokens": 0,
            "extraction_output_tokens": 0,
        }
        self.price_table = load_price_table(
            Path(__file__).resolve().parents[1] / "config" / "prices.yaml"
        )
        self.started_at = time.monotonic()

    async def on_push_frame(self, data: Any) -> None:
        from pipecat.frames.frames import (
            BotStartedSpeakingFrame,
            InterruptionFrame,
            MetricsFrame,
            TranscriptionFrame,
            TTSTextFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
            VADUserStartedSpeakingFrame,
            VADUserStoppedSpeakingFrame,
        )

        frame = data.frame
        timestamp = time.monotonic()
        if isinstance(frame, TranscriptionFrame):
            if getattr(frame, "finalized", True):
                self.sink.append_transcript("caller", frame.text, frame.timestamp)
                if self.caller_text_callback is not None:
                    self.caller_text_callback(frame.text)
        elif isinstance(frame, TTSTextFrame):
            self.sink.append_transcript("assistant", frame.text)
        elif isinstance(frame, InterruptionFrame):
            self.latency.interrupted()
        elif isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            self.latency.user_started_speaking()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            wall_clock_offset = timestamp - time.time()
            # VAD reports after stop_secs of silence; subtract it to get when speech ended.
            speech_ended_at = frame.timestamp - (frame.stop_secs or 0.0)
            self.latency.vad_user_stopped_speaking(speech_ended_at + wall_clock_offset)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            if not self._is_duplicate_broadcast_frame(frame):
                self.latency.user_stopped_speaking(timestamp)
        elif isinstance(frame, BotStartedSpeakingFrame):
            if not self._is_duplicate_broadcast_frame(frame):
                turn = self.latency.first_bot_audio(timestamp)
                if turn is not None:
                    self._save()
        elif isinstance(frame, MetricsFrame):
            self._record_service_metrics(frame)

    def _is_duplicate_broadcast_frame(self, frame: Any) -> bool:
        frame_id = frame.id
        sibling_id = getattr(frame, "broadcast_sibling_id", None)
        if sibling_id is None:
            return False
        if (
            frame_id in self._seen_broadcast_frame_ids
            or sibling_id in self._seen_broadcast_frame_ids
        ):
            return True
        self._seen_broadcast_frame_ids.update((frame_id, sibling_id))
        return False

    def _record_service_metrics(self, frame: Any) -> None:
        from pipecat.metrics.metrics import (
            LLMUsageMetricsData,
            STTUsageMetricsData,
            TTFAMetricsData,
            TTFATMetricsData,
            TTFBMetricsData,
            TTSUsageMetricsData,
        )

        latency_types = (TTFBMetricsData, TTFAMetricsData, TTFATMetricsData)
        for metric in frame.data:
            if isinstance(metric, latency_types):
                if isinstance(metric, TTFAMetricsData):
                    seconds = metric.ttfa
                elif isinstance(metric, TTFATMetricsData):
                    seconds = metric.ttfat
                else:
                    seconds = metric.value
                record = {
                    "kind": type(metric).__name__.removesuffix("MetricsData").lower(),
                    "processor": getattr(metric, "processor", None),
                    "model": getattr(metric, "model", None),
                    "seconds": seconds,
                    "timestamp": time.time(),
                    "monotonic_timestamp": time.monotonic(),
                }
                if isinstance(metric, TTFAMetricsData):
                    record["ttfb_seconds"] = metric.ttfb
                    record["leading_silence_seconds"] = metric.leading_silence
                elif isinstance(metric, TTFATMetricsData):
                    record["ttfb_seconds"] = metric.ttfb
                    record["thinking_time_seconds"] = metric.thinking_time
                self.service_metrics.append(record)
            elif isinstance(metric, STTUsageMetricsData):
                self.usage["stt_audio_seconds"] += getattr(metric.value, "audio_seconds", 0) or 0
            elif isinstance(metric, TTSUsageMetricsData):
                self.usage["tts_characters"] += metric.value or 0
            elif isinstance(metric, LLMUsageMetricsData):
                self.usage["llm_input_tokens"] += getattr(metric.value, "prompt_tokens", 0) or 0
                self.usage["llm_output_tokens"] += (
                    getattr(metric.value, "completion_tokens", 0) or 0
                )
        self._save()

    def _save(self) -> None:
        self.sink.save_metrics(self.snapshot())

    def persist(self) -> None:
        self._save()

    def record_extraction(
        self,
        model: str,
        status: str,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        self.providers["extraction_model"] = model
        self.providers["extraction_status"] = status
        usage = usage or {}
        self.usage["extraction_input_tokens"] = usage.get("input_tokens", 0)
        self.usage["extraction_output_tokens"] = usage.get("output_tokens", 0)
        self._save()

    def record_transfer(self, transfer: dict[str, Any]) -> None:
        self.transfer = dict(transfer)
        self._save()

    def start_number_hold(self) -> dict[str, Any]:
        now = time.monotonic()
        self.latency.number_hold_started(now)
        record = {
            "number_hold_started": max(0.0, now - self.started_at),
            "resumed": False,
            "held_secs": None,
        }
        self.number_holds.append(record)
        self._save()
        return record

    def finish_number_hold(
        self, record: dict[str, Any], *, resumed: bool, held_secs: float
    ) -> None:
        record["resumed"] = resumed
        record["held_secs"] = max(0.0, held_secs)
        self._save()

    def snapshot(self) -> dict[str, Any]:
        metrics = {
            "call_id": self.sink.call_id,
            "transport": "twilio" if self.telephony else "webrtc",
            "call_duration_seconds": max(0.0, time.monotonic() - self.started_at),
            "providers": self.providers,
            "turns": self.latency.turns,
            "turn_latency_summary": self.latency.summary(),
            "turn_end_latency_summary": self.latency.turn_end_summary(),
            "number_holds": self.number_holds,
            "service_metrics": self.service_metrics,
            "usage": self.usage,
        }
        if self.transfer is not None:
            metrics["transfer"] = self.transfer
        metrics["estimated_cost"] = calculate_cost(metrics, self.price_table)
        return metrics
