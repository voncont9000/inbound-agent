import pytest

from inbound_voice.metrics import (
    TurnLatencyTracker,
    calculate_cost,
    summarize_turn_latencies,
)


def test_turn_latency_stats_and_percentiles() -> None:
    tracker = TurnLatencyTracker()
    tracker.user_stopped_speaking(10.0)
    tracker.first_bot_audio(10.5)
    tracker.user_stopped_speaking(20.0)
    tracker.first_bot_audio(21.0)
    tracker.user_stopped_speaking(30.0)
    tracker.first_bot_audio(30.2)

    assert [row["seconds"] for row in tracker.turns] == pytest.approx([0.5, 1.0, 0.2])
    assert summarize_turn_latencies(tracker.turns) == {
        "turn_count": 3,
        "p50_seconds": 0.5,
        "p95_seconds": 0.95,
        "max_seconds": 1.0,
    }


def test_cost_calculation_tolerates_null_prices_and_usage() -> None:
    cost = calculate_cost(
        {
            "usage": {"stt_audio_seconds": 120, "tts_characters": 1000},
            "call_duration_seconds": 300,
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
