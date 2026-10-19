import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSTextFrame,
)

from inbound_voice.metrics import (
    CallMetricsObserver,
    TurnLatencyTracker,
    calculate_cost,
    summarize_turn_end_latencies,
    summarize_turn_latencies,
)


class RecordingTranscriptSink:
    def __init__(self) -> None:
        self.transcripts: list[tuple[str, str, str | None]] = []

    def append_transcript(self, role: str, text: str, timestamp: str | None = None) -> None:
        self.transcripts.append((role, text, timestamp))


def test_turn_latency_tracks_only_latest_pending_turn() -> None:
    tracker = TurnLatencyTracker()
    tracker.user_started_speaking()
    tracker.vad_user_stopped_speaking(10.0)
    tracker.user_stopped_speaking(10.2)

    tracker.user_started_speaking()
    tracker.vad_user_stopped_speaking(20.0)
    tracker.user_stopped_speaking(20.3)
    turn = tracker.first_bot_audio(20.5)

    assert turn is not None
    assert len(tracker.turns) == 1
    assert turn["seconds"] == pytest.approx(0.5)
    assert turn["turn_end_to_audio_seconds"] == pytest.approx(0.2)


def test_interruption_clears_pending_turn() -> None:
    tracker = TurnLatencyTracker()
    tracker.vad_user_stopped_speaking(10.0)
    tracker.user_stopped_speaking(10.2)
    tracker.interrupted()

    assert tracker.first_bot_audio(10.5) is None
    assert tracker.turns == []


def test_turn_latency_stats_summarize_both_stop_events() -> None:
    tracker = TurnLatencyTracker()
    turns = [(10.0, 10.2, 10.5), (20.0, 20.4, 21.0), (30.0, 30.1, 30.2)]
    for vad_stop, turn_end, audio in turns:
        tracker.vad_user_stopped_speaking(vad_stop)
        tracker.user_stopped_speaking(turn_end)
        tracker.first_bot_audio(audio)

    assert summarize_turn_latencies(tracker.turns) == {
        "turn_count": 3,
        "p50_seconds": 0.5,
        "p95_seconds": 0.95,
        "max_seconds": 1.0,
    }
    assert summarize_turn_end_latencies(tracker.turns) == pytest.approx(
        {
            "turn_count": 3,
            "p50_seconds": 0.3,
            "p95_seconds": 0.57,
            "max_seconds": 0.6,
        }
    )


def test_number_hold_start_includes_full_wait_when_vad_stop_is_later() -> None:
    tracker = TurnLatencyTracker()
    tracker.user_started_speaking()
    tracker.vad_user_stopped_speaking(10.3)
    tracker.number_hold_started(10.0)
    tracker.user_stopped_speaking(12.0)

    turn = tracker.first_bot_audio(12.5)

    assert turn is not None
    assert turn["vad_user_stopped_speaking_at"] == 10.0
    assert turn["seconds"] == pytest.approx(2.5)
    assert turn["turn_end_to_audio_seconds"] == pytest.approx(0.5)


def test_stt_cost_uses_call_duration_not_measured_usage() -> None:
    cost = calculate_cost(
        {
            "usage": {"stt_audio_seconds": 999, "tts_characters": 1000},
            "call_duration_seconds": 120,
        },
        {
            "currency": "USD",
            "prices": {
                "deepgram_stt_per_minute": {"value": 0.006},
                "deepgram_tts_per_1000_characters": {"value": None},
                "llm_models": {
                    "google/gemini-2.5-flash": {
                        "input_per_1m_tokens": {"value": 0.3},
                        "output_per_1m_tokens": {"value": 2.5},
                    }
                },
                "twilio_uk_inbound_per_minute": {"value": None},
            },
        },
    )

    assert cost["components"]["stt"] == 0.012
    assert cost["components"]["tts"] is None
    assert cost["components"]["llm_input"] is None
    assert cost["components"]["twilio"] is None
    assert cost["total"] == 0.012
    assert "twilio" in cost["unknown_components"]


def test_provider_specific_costs_use_selected_rates() -> None:
    from pathlib import Path

    from inbound_voice.metrics import load_price_table

    prices = load_price_table(Path(__file__).resolve().parents[1] / "config" / "prices.yaml")
    cost = calculate_cost(
        {
            "transport": "webrtc",
            "call_duration_seconds": 120,
            "providers": {
                "stt": "assemblyai",
                "tts": "speechify",
                "llm_model": "google/gemini-2.5-flash",
            },
            "usage": {
                "stt_audio_seconds": 999,
                "tts_characters": 1000,
                "llm_input_tokens": 1000,
                "llm_output_tokens": 1000,
            },
        },
        prices,
    )

    assert cost["components"]["stt"] == pytest.approx(0.015)
    assert cost["components"]["tts"] == 0.01
    assert cost["components"]["llm_input"] == 0.0003
    assert cost["components"]["llm_output"] == 0.0025
    assert cost["components"]["twilio"] == 0.0


def test_google_gemini_flash_cost_uses_direct_api_price() -> None:
    from pathlib import Path

    from inbound_voice.metrics import load_price_table

    prices = load_price_table(Path(__file__).resolve().parents[1] / "config" / "prices.yaml")
    cost = calculate_cost(
        {
            "transport": "webrtc",
            "call_duration_seconds": 60,
            "providers": {
                "stt": "assemblyai",
                "tts": "speechify",
                "llm": "google",
                "llm_model": "gemini-2.5-flash",
            },
            "usage": {
                "tts_characters": 1000,
                "llm_input_tokens": 1_000_000,
                "llm_output_tokens": 1_000_000,
            },
        },
        prices,
    )

    assert cost["components"]["llm_input"] == 0.3
    assert cost["components"]["llm_output"] == 2.5


def test_extraction_cost_is_a_separate_component() -> None:
    from pathlib import Path

    from inbound_voice.metrics import load_price_table

    prices = load_price_table(Path(__file__).resolve().parents[1] / "config" / "prices.yaml")
    cost = calculate_cost(
        {
            "transport": "webrtc",
            "call_duration_seconds": 60,
            "providers": {
                "stt": "assemblyai",
                "tts": "speechify",
                "llm": "google",
                "llm_model": "gemini-3.5-flash-lite",
                "extraction_model": "gemini-3.8-flash",
            },
            "usage": {
                "tts_characters": 0,
                "llm_input_tokens": 0,
                "llm_output_tokens": 0,
                "extraction_input_tokens": 1_000_000,
                "extraction_output_tokens": 1_000_000,
            },
        },
        prices,
    )

    assert cost["components"]["extraction"] == 4.5


def test_legacy_metrics_default_to_deepgram_and_gemini_flash() -> None:
    from pathlib import Path

    from inbound_voice.metrics import load_price_table

    prices = load_price_table(Path(__file__).resolve().parents[1] / "config" / "prices.yaml")
    cost = calculate_cost(
        {
            "transport": "twilio",
            "call_duration_seconds": 60,
            "usage": {
                "tts_characters": 1000,
                "llm_input_tokens": 1_000_000,
                "llm_output_tokens": 1_000_000,
            },
        },
        prices,
    )

    assert cost["components"] == {
        "stt": 0.0048,
        "tts": 0.03,
        "llm_input": 0.3,
        "llm_output": 2.5,
        "twilio": 0.01,
    }
    assert cost["unknown_components"] == []


def test_unknown_llm_model_has_unknown_llm_components() -> None:
    from pathlib import Path

    from inbound_voice.metrics import load_price_table

    prices = load_price_table(Path(__file__).resolve().parents[1] / "config" / "prices.yaml")
    cost = calculate_cost(
        {
            "transport": "webrtc",
            "call_duration_seconds": 60,
            "providers": {
                "stt": "assemblyai",
                "tts": "speechify",
                "llm_model": "example/unknown-model",
            },
            "usage": {
                "tts_characters": 1000,
                "llm_input_tokens": 1000,
                "llm_output_tokens": 1000,
            },
        },
        prices,
    )

    assert cost["components"]["llm_input"] is None
    assert cost["components"]["llm_output"] is None
    assert cost["unknown_components"] == ["llm_input", "llm_output"]


def test_assistant_transcript_flushes_on_bot_stopped_speaking() -> None:
    sink = RecordingTranscriptSink()
    observer = CallMetricsObserver(sink)

    async def run() -> None:
        for frame in (
            TTSTextFrame(text="Hello,", aggregated_by="test"),
            TTSTextFrame(text="you", aggregated_by="test"),
            TTSTextFrame(text="are", aggregated_by="test"),
            BotStoppedSpeakingFrame(),
        ):
            await observer.on_push_frame(SimpleNamespace(frame=frame))

    asyncio.run(run())

    assert [(role, text) for role, text, _ in sink.transcripts] == [("assistant", "Hello, you are")]
    assert datetime.fromisoformat(sink.transcripts[0][2])


def test_interruption_splits_assistant_transcript_rows() -> None:
    sink = RecordingTranscriptSink()
    observer = CallMetricsObserver(sink)

    async def run() -> None:
        for frame in (
            TTSTextFrame(text="Hello,", aggregated_by="test"),
            TTSTextFrame(text="you", aggregated_by="test"),
            InterruptionFrame(),
            TTSTextFrame(text="are", aggregated_by="test"),
            BotStoppedSpeakingFrame(),
        ):
            await observer.on_push_frame(SimpleNamespace(frame=frame))

    asyncio.run(run())

    assert [(role, text) for role, text, _ in sink.transcripts] == [
        ("assistant", "Hello, you"),
        ("assistant", "are"),
    ]


def test_final_caller_transcription_flushes_assistant_text_first() -> None:
    sink = RecordingTranscriptSink()
    observer = CallMetricsObserver(sink)

    async def run() -> None:
        for frame in (
            TTSTextFrame(text="Hello,", aggregated_by="test"),
            TranscriptionFrame(
                text="Are you there?",
                user_id="caller",
                timestamp="2026-10-19T12:00:00Z",
                finalized=True,
            ),
            TTSTextFrame(text="Yes.", aggregated_by="test"),
            BotStoppedSpeakingFrame(),
        ):
            await observer.on_push_frame(SimpleNamespace(frame=frame))

    asyncio.run(run())

    assert [(role, text) for role, text, _ in sink.transcripts] == [
        ("assistant", "Hello,"),
        ("caller", "Are you there?"),
        ("assistant", "Yes."),
    ]


def test_assistant_text_with_inter_frame_spaces_is_concatenated() -> None:
    sink = RecordingTranscriptSink()
    observer = CallMetricsObserver(sink)
    first = TTSTextFrame(text=" Hello", aggregated_by="test")
    second = TTSTextFrame(text=" there", aggregated_by="test")
    first.includes_inter_frame_spaces = True
    second.includes_inter_frame_spaces = True

    async def run() -> None:
        await observer.on_push_frame(SimpleNamespace(frame=first))
        await observer.on_push_frame(SimpleNamespace(frame=second))
        await observer.on_push_frame(SimpleNamespace(frame=BotStoppedSpeakingFrame()))

    asyncio.run(run())

    assert [(role, text) for role, text, _ in sink.transcripts] == [("assistant", "Hello there")]


def test_explicit_assistant_flush_writes_remaining_text_and_skips_empty() -> None:
    sink = RecordingTranscriptSink()
    observer = CallMetricsObserver(sink)

    async def run() -> None:
        await observer.on_push_frame(
            SimpleNamespace(frame=TTSTextFrame(text="One more word", aggregated_by="test"))
        )
        observer.flush_assistant_transcript()
        observer.flush_assistant_transcript()
        await observer.on_push_frame(
            SimpleNamespace(frame=TTSTextFrame(text="   ", aggregated_by="test"))
        )
        observer.flush_assistant_transcript()

    asyncio.run(run())

    assert [(role, text) for role, text, _ in sink.transcripts] == [("assistant", "One more word")]
