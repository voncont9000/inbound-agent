import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_report_cli_on_fixture_runs(tmp_path) -> None:
    call_dir = tmp_path / "fixture-call"
    call_dir.mkdir()
    (call_dir / "metrics.json").write_text(
        json.dumps(
            {
                "call_duration_seconds": 60,
                "turns": [{"seconds": 0.8}],
                "turn_latency_summary": {
                    "turn_count": 1,
                    "p50_seconds": 0.8,
                    "p95_seconds": 0.8,
                    "max_seconds": 0.8,
                },
                "turn_end_latency_summary": {
                    "turn_count": 1,
                    "p50_seconds": 0.3,
                    "p95_seconds": 0.3,
                    "max_seconds": 0.3,
                },
                "providers": {
                    "stt": "assemblyai",
                    "tts": "speechify",
                    "tts_voice": None,
                    "llm": "openrouter",
                    "llm_model": "google/gemini-2.5-flash-lite",
                },
                "usage": {
                    "stt_audio_seconds": 60,
                    "tts_characters": 1000,
                    "llm_input_tokens": 1000,
                    "llm_output_tokens": 1000,
                },
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inbound_voice.report",
            str(tmp_path),
            "--prices",
            str(ROOT / "config" / "prices.yaml"),
        ],
        check=True,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    output = result.stdout
    assert "fixture-call" in output
    assert "0.800s" in output
    assert "0.300s" in output
    assert "USD" in output
    assert "STT" in output
    assert "TTS" in output
    assert "LLM" in output
    assert "LLM MODEL" in output
    assert "assemblyai" in output
    assert "speechify" in output
    assert "openrouter" in output
    assert "google/gemini-2.5-flash-lite" in output
