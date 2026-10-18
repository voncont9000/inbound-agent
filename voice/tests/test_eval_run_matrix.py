import asyncio
import socket

import pytest
import uvicorn
from test_api_client import _route_response

from eval.fake_api import create_fake_api
from eval.run_matrix import (
    FAKE_API_TOKEN,
    PROVIDER_ERROR,
    _configure_caller_id_override,
    _configure_llm_provider,
    _configure_tts_voice,
    _fake_api_state,
    _rep_fake_state,
    build_parser,
)
from eval.synth_callers import load_scenarios


def test_every_eval_scenario_has_a_caller_id() -> None:
    for scenario_name, scenario in load_scenarios().items():
        caller_id = scenario.get("caller_id")
        assert isinstance(caller_id, str) and caller_id.strip(), (
            f"Scenario {scenario_name} must have a non-empty caller_id."
        )


def test_rep_fake_state_excludes_bookings_from_earlier_repetitions() -> None:
    previous = {"bookings": [{"booking_id": "earlier"}], "calls": ["earlier-call"]}
    current = {
        "bookings": [{"booking_id": "earlier"}, {"booking_id": "this-repetition"}],
        "calls": ["earlier-call", "this-call"],
    }

    state = _rep_fake_state(previous, current)

    assert state == {
        "bookings": [{"booking_id": "this-repetition"}],
        "calls": ["earlier-call", "this-call"],
    }


def test_booking_smoke_command_has_default_provider_repetition_args() -> None:
    args = build_parser().parse_args(
        ["--transport", "twilio", "--fake-api", "--scenario", "booking"]
    )

    assert args.stt == ["deepgram"]
    assert args.tts == ["deepgram"]
    assert args.reps == [1]


@pytest.mark.asyncio
async def test_fake_api_state_reads_response_from_live_server() -> None:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]

    app = create_fake_api(_route_response(), FAKE_API_TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)

        state = await _fake_api_state(f"http://127.0.0.1:{port}")
        assert state == {"calls": [], "reports": [], "bookings": []}
    finally:
        server.should_exit = True
        await server_task


def test_provider_error_detection_ignores_timestamp_status_fragment() -> None:
    line = "2026-10-17 14:14:11.403 | DEBUG | [SCTP] USERDATA_MAX_LENGTH set to 1100"
    price_line = (
        "Employment settlement agreement review: £500–£900 plus VAT. "
        "Illustrative demo range; confirm scope and any employer contribution."
    )

    assert PROVIDER_ERROR.search(line) is None
    assert PROVIDER_ERROR.search(price_line) is None


@pytest.mark.parametrize(
    "line",
    [
        "HTTP 403 Forbidden",
        "HTTP 400 Bad Request",
        "status=401",
        "HTTP 429 Too Many Requests",
        "HTTP 503 Service Unavailable",
    ],
)
def test_provider_error_detection_matches_http_status_codes(line: str) -> None:
    assert PROVIDER_ERROR.search(line) is not None


def test_matrix_runner_sets_provider_voice_defaults_and_overrides() -> None:
    process_env = {"TTS_VOICE": "inherited"}
    _configure_tts_voice(process_env, "speechify")
    assert process_env["TTS_VOICE"] == "beatrice_32"

    _configure_tts_voice(process_env, "speechify", speechify_voice="custom-speechify")
    assert process_env["TTS_VOICE"] == "custom-speechify"

    _configure_tts_voice(process_env, "elevenlabs")
    assert process_env["TTS_VOICE"] == "MzqUf1HbJ8UmQ0wUsx2p"

    _configure_tts_voice(process_env, "elevenlabs", elevenlabs_voice="custom-elevenlabs")
    assert process_env["TTS_VOICE"] == "custom-elevenlabs"


def test_matrix_runner_sets_llm_provider_and_uses_provider_default_model() -> None:
    process_env = {"LLM_MODEL": "inherited-custom-model"}

    _configure_llm_provider(process_env, "google")
    assert process_env["LLM_PROVIDER"] == "google"
    assert "LLM_MODEL" not in process_env
    process_env["LLM_MODEL"] = "inherited-custom-model"
    _configure_llm_provider(process_env, "openrouter")
    assert process_env["LLM_PROVIDER"] == "openrouter"
    assert "LLM_MODEL" not in process_env


def test_matrix_runner_sets_and_clears_test_caller_id() -> None:
    process_env = {"CALLER_ID_OVERRIDE": "inherited"}

    _configure_caller_id_override(process_env, "07700900456")
    assert process_env["CALLER_ID_OVERRIDE"] == "07700900456"

    _configure_caller_id_override(process_env, None)
    assert "CALLER_ID_OVERRIDE" not in process_env
