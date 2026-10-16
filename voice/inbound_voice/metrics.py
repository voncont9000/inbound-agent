import math
import time
from pathlib import Path
from typing import Any

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
    values = [float(turn["seconds"]) for turn in turns if turn.get("seconds") is not None]
    return {
        "turn_count": len(values),
        "p50_seconds": percentile(values, 50),
        "p95_seconds": percentile(values, 95),
        "max_seconds": max(values) if values else None,
    }


class TurnLatencyTracker:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self._waiting_for_audio: dict[str, float] = {}

    def user_stopped_speaking(self, timestamp: float | None = None) -> str:
        turn_id = str(len(self.turns) + len(self._waiting_for_audio) + 1)
        self._waiting_for_audio[turn_id] = timestamp if timestamp is not None else time.monotonic()
        return turn_id

    def first_bot_audio(self, timestamp: float | None = None) -> dict[str, Any] | None:
        if not self._waiting_for_audio:
            return None
        turn_id = next(iter(self._waiting_for_audio))
        stopped_at = self._waiting_for_audio.pop(turn_id)
        audio_at = timestamp if timestamp is not None else time.monotonic()
        turn = {
            "turn_id": turn_id,
            "user_stopped_speaking_at": stopped_at,
            "first_bot_audio_at": audio_at,
            "seconds": max(0.0, audio_at - stopped_at),
        }
        self.turns.append(turn)
        return turn

    def summary(self) -> dict[str, float | int | None]:
        return summarize_turn_latencies(self.turns)


class CallMetricsObserver(BaseObserver):
    """Observe Pipecat frame flow and write call-level latency and usage metrics."""

    def __init__(self, sink: CallSink, telephony: bool = False) -> None:
        super().__init__(observe_every_push=False)
        self.sink = sink
        self.telephony = telephony
        self.latency = TurnLatencyTracker()
        self.service_metrics: list[dict[str, Any]] = []
        self.usage: dict[str, float | int] = {
            "stt_audio_seconds": 0,
            "tts_characters": 0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
        }
        self.price_table = load_price_table(
            Path(__file__).resolve().parents[1] / "config" / "prices.yaml"
        )
        self.started_at = time.monotonic()

    async def on_push_frame(self, data: Any) -> None:
        from pipecat.frames.frames import (
            MetricsFrame,
            OutputAudioRawFrame,
            TranscriptionFrame,
            TTSTextFrame,
            UserStoppedSpeakingFrame,
        )

        frame = data.frame
        timestamp = time.monotonic()
        if isinstance(frame, TranscriptionFrame):
            if getattr(frame, "finalized", True):
                self.sink.append_transcript("caller", frame.text, frame.timestamp)
        elif isinstance(frame, TTSTextFrame):
            self.sink.append_transcript("assistant", frame.text)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self.latency.user_stopped_speaking(timestamp)
        elif isinstance(frame, OutputAudioRawFrame):
            turn = self.latency.first_bot_audio(timestamp)
            if turn is not None:
                self._save()
        elif isinstance(frame, MetricsFrame):
            self._record_service_metrics(frame)

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

    def snapshot(self) -> dict[str, Any]:
        metrics = {
            "call_id": self.sink.call_id,
            "transport": "twilio" if self.telephony else "webrtc",
            "call_duration_seconds": max(0.0, time.monotonic() - self.started_at),
            "turns": self.latency.turns,
            "turn_latency_summary": self.latency.summary(),
            "service_metrics": self.service_metrics,
            "usage": self.usage,
        }
        metrics["estimated_cost"] = calculate_cost(metrics, self.price_table)
        return metrics


def load_price_table(path: str | Path) -> dict[str, Any]:
    import yaml

    with Path(path).open(encoding="utf-8") as price_file:
        return yaml.safe_load(price_file)


def calculate_cost(metrics: dict[str, Any], price_table: dict[str, Any]) -> dict[str, Any]:
    prices = price_table.get("prices", price_table)
    usage = metrics.get("usage", {})
    transport = metrics.get("transport")
    twilio_cost = (
        _priced_cost(
            metrics.get("call_duration_seconds"),
            prices.get("twilio_uk_inbound_per_minute", {}).get("value"),
            divisor=60,
        )
        if transport == "twilio"
        else 0.0
        if transport == "webrtc"
        else None
    )
    components = {
        "deepgram_stt": _priced_cost(
            usage.get("stt_audio_seconds"),
            prices.get("deepgram_stt_per_minute", {}).get("value"),
            divisor=60,
        ),
        "deepgram_tts": _priced_cost(
            usage.get("tts_characters"),
            prices.get("deepgram_tts_per_1000_characters", {}).get("value"),
            divisor=1000,
        ),
        "llm_input": _priced_cost(
            usage.get("llm_input_tokens"),
            prices.get("llm_input_per_1m_tokens", {}).get("value"),
            divisor=1_000_000,
        ),
        "llm_output": _priced_cost(
            usage.get("llm_output_tokens"),
            prices.get("llm_output_per_1m_tokens", {}).get("value"),
            divisor=1_000_000,
        ),
        "twilio": twilio_cost,
    }
    return {
        "currency": price_table.get("currency", "USD"),
        "components": components,
        "total": round(sum(value for value in components.values() if value is not None), 8),
        "unknown_components": [name for name, value in components.items() if value is None],
    }


def _priced_cost(quantity: Any, rate: Any, divisor: int) -> float | None:
    if quantity is None or rate is None:
        return None
    return float(quantity) * float(rate) / divisor
