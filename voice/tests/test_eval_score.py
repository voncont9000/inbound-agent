import json
import shutil
from pathlib import Path

import pytest

from eval.score import aggregate_calls, render_summary, score_call

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
    assert result["ended_via_end_call"]
    assert result["tts_voice"] == "voice-123"
    assert result["estimated_cost"]["components"]["stt"] == pytest.approx(0.015)


def test_aggregate_calls_and_summary_use_requested_latency_and_check_stats() -> None:
    calls = [
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "scenario": "employment",
            "rep": 1,
            "checks_passed": 4,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.6, "p95_seconds": 1.1},
            "turn_end_latency_summary": {"p50_seconds": 0.2, "p95_seconds": 0.4},
            "estimated_cost": {"currency": "USD", "total": 0.08},
            "ended_via_end_call": True,
        },
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "scenario": "employment",
            "rep": 2,
            "checks_passed": 5,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.8, "p95_seconds": 1.4},
            "turn_end_latency_summary": {"p50_seconds": 0.3, "p95_seconds": 0.5},
            "estimated_cost": {"currency": "USD", "total": 0.1},
            "ended_via_end_call": False,
        },
    ]

    aggregate = aggregate_calls(calls)
    summary = render_summary(calls)

    assert aggregate["median_p50_latency_seconds"] == 0.7
    assert aggregate["max_p95_latency_seconds"] == 1.4
    assert aggregate["checks_passed"] == 9
    assert aggregate["checks_total"] == 10
    assert aggregate["mean_cost"] == 0.09
    assert "Voice" in summary
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
