import pytest

from inbound_voice.metrics import (
    TurnLatencyTracker,
    calculate_cost,
    summarize_turn_end_latencies,
    summarize_turn_latencies,
)


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
                "llm_input_per_1m_tokens": {"value": 0.3},
                "llm_output_per_1m_tokens": {"value": 2.5},
                "twilio_uk_inbound_per_minute": {"value": None},
            },
        },
    )

    assert cost["components"]["deepgram_stt"] == 0.012
    assert cost["components"]["deepgram_tts"] is None
    assert cost["components"]["llm_input"] is None
    assert cost["components"]["twilio"] is None
    assert cost["total"] == 0.012
    assert "twilio" in cost["unknown_components"]
