import json
import shutil
from pathlib import Path

import pytest

from eval.score import aggregate_calls, render_summary, score_call
from eval.synth_callers import load_scenarios


def test_latency_breakdown_aligns_turns_with_service_metrics() -> None:
    from eval.score import _latency_breakdown_medians

    result = _latency_breakdown_medians(
        [
            {
                "vad_user_stopped_speaking_at": 1.5,
                "user_stopped_speaking_at": 2.0,
                "first_bot_audio_at": 3.0,
            }
        ],
        [
            {
                "processor": "LLMService",
                "kind": "ttfb",
                "model": "gemini-test",
                "monotonic_timestamp": 2.2,
            },
            {
                "processor": "TTSService",
                "kind": "ttfa",
                "model": "speechify-test",
                "monotonic_timestamp": 2.7,
            },
        ],
    )

    assert result["caller_stop_to_turn_end_seconds"] == 0.5
    assert result["turn_end_to_llm_first_token_seconds"] == pytest.approx(0.2)
    assert result["llm_first_token_to_tts_first_audio_seconds"] == pytest.approx(0.5)


FIXTURE = Path(__file__).parent / "fixtures" / "eval-call"
SCENARIO = {
    "transcript_checks": {
        "caller": "mc ?allister",
        "employer": "brightwell",
        "acas": r"\bacas\b",
    },
    "intake_checks": {
        "caller_name": "(?i)siobhan.*mc ?allister",
        "termination_date": "^2026-08-10$",
        "acas_early_conciliation_started": "^False$",
        "missing": "anything",
        "null_field": "None",
    },
    "task_checks": {"urgent": True},
}


def test_score_call_uses_fixture_transcript_intake_tasks_and_metrics(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["providers"]["tts_voice"] = "voice-123"
    metrics["providers"]["llm"] = "openrouter"
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    result = score_call(run_dir, SCENARIO)

    assert result["call_id"] == "fixture-call"
    assert result["transcript_checks"]["caller"]["passed"]
    assert result["transcript_checks"]["acas"]["passed"]
    assert result["intake_checks"]["termination_date"]["passed"]
    assert result["intake_checks"]["acas_early_conciliation_started"]["value"] is False
    assert result["intake_checks"]["acas_early_conciliation_started"]["passed"]
    assert not result["intake_checks"]["missing"]["passed"]
    assert result["task_checks"]["urgent"]["passed"]
    assert not result["intake_checks"]["null_field"]["passed"]
    assert result["checks_passed"] == 7
    assert result["checks_total"] == 9
    assert result["stt_ttfb_median_seconds"] == 0.2
    assert result["tts_ttfb_median_seconds"] == 0.15
    assert result["llm_ttfb_median_seconds"] is None
    assert result["ended_via_end_call"]
    assert result["tts_voice"] == "voice-123"
    assert result["llm"] == "openrouter"
    assert result["llm_model"] == "google/gemini-2.5-flash"
    assert result["estimated_cost"]["components"]["stt"] == pytest.approx(0.015)
    assert result["live_intake_checks"]["caller_name"]["passed"]
    assert result["post_call_intake_checks"]["caller_name"]["passed"]
    assert result["needs_review"] == []


def test_score_call_checks_end_source_and_disconnect_timing(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "tasks.json").write_text(
        json.dumps([{"type": "end_call", "source": "idle"}]), encoding="utf-8"
    )
    (run_dir / "caller_timeline.json").write_text(
        json.dumps(
            {
                "server_disconnect_detected": True,
                "seconds_from_last_agent_speech_end_to_disconnect": 1.5,
            }
        ),
        encoding="utf-8",
    )

    result = score_call(
        run_dir,
        {
            "expected_end_call_source": "idle",
            "expected_server_disconnect": True,
        },
    )

    assert result["task_checks"]["end_call_source"]["passed"]
    assert result["task_checks"]["server_disconnect_detected"]["passed"]
    assert result["end_call_source"] == "idle"
    assert result["server_disconnect_detected"]
    assert result["seconds_from_last_agent_speech_end_to_disconnect"] == 1.5


def test_scenarios_script_final_confirmation_and_named_idle_check() -> None:
    scenarios = load_scenarios()
    final_confirmation = "Yes, that's all correct. Thanks, bye."

    assert scenarios["employment"]["utterances"][-1] == final_confirmation
    assert scenarios["conveyancing"]["utterances"][-1] == final_confirmation
    assert scenarios["idle_check"]["utterances"] == ["Hi, I'm calling about a property purchase."]
    assert scenarios["idle_check"]["expected_end_call_source"] == "idle"
    assert scenarios["idle_check"]["expected_server_disconnect"] is True


def test_aggregate_calls_and_summary_use_requested_latency_and_check_stats() -> None:
    calls = [
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "llm": "google",
            "llm_model": "gemini-2.5-flash",
            "scenario": "employment",
            "rep": 1,
            "checks_passed": 4,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.6, "p95_seconds": 1.1},
            "turn_end_latency_summary": {"p50_seconds": 0.2, "p95_seconds": 0.4},
            "turn_latencies_seconds": [0.5, 1.0],
            "server_disconnect_detected": True,
            "seconds_from_last_agent_speech_end_to_disconnect": 2.0,
            "estimated_cost": {"currency": "USD", "total": 0.08},
            "ended_via_end_call": True,
        },
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "llm": "google",
            "llm_model": "gemini-2.5-flash",
            "scenario": "employment",
            "rep": 2,
            "checks_passed": 5,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.8, "p95_seconds": 1.4},
            "turn_end_latency_summary": {"p50_seconds": 0.3, "p95_seconds": 0.5},
            "turn_latencies_seconds": [1.5, 2.0],
            "server_disconnect_detected": False,
            "estimated_cost": {"currency": "USD", "total": 0.1},
            "ended_via_end_call": False,
        },
    ]

    aggregate = aggregate_calls(calls)
    summary = render_summary(calls)

    assert aggregate["median_p50_latency_seconds"] == 0.7
    assert aggregate["max_p95_latency_seconds"] == 1.4
    assert aggregate["all_turns_p95_latency_seconds"] == pytest.approx(1.925)
    assert aggregate["checks_passed"] == 9
    assert aggregate["checks_total"] == 10
    assert aggregate["mean_cost"] == 0.09
    assert "google (gemini-2.5-flash)" in summary
    assert "Voice" in summary
    assert "Intake live / post / merged" in summary
    assert "Review" in summary
    assert "Task sources" in summary
    assert "Smart turn stop" in summary
    assert "LLM TTFB median" in summary
    assert "all-turn p95" in summary
    assert "Server disconnect" in summary
    assert "Speech-end→disconnect" in summary
    assert "beatrice_32" in summary
    assert "| Aggregate " in summary
    assert "9/10" in summary


def test_false_urgent_check_passes_when_no_urgent_task_exists(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "tasks.json").write_text(json.dumps([{"type": "end_call"}]), encoding="utf-8")

    result = score_call(run_dir, {"task_checks": {"urgent": False}})

    assert result["task_checks"]["urgent"] == {
        "expected": False,
        "actual": False,
        "passed": True,
    }
