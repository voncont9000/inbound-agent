import pytest

from eval.run_matrix import (
    PROVIDER_ERROR,
    _configure_caller_id_override,
    _configure_llm_provider,
    _configure_tts_voice,
)


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
