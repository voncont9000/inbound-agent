import inspect

from bot import SMART_TURN_STOP_DEFAULT_SECS
from eval.run_matrix import build_parser, run_matrix


def test_smart_turn_stop_defaults_match_bot_default() -> None:
    arguments = build_parser().parse_args(
        [
            "--stt",
            "soniox",
            "--tts",
            "speechify",
            "--scenario",
            "conveyancing",
            "--reps",
            "1",
        ]
    )

    assert arguments.smart_turn_stop_secs == SMART_TURN_STOP_DEFAULT_SECS
    assert (
        inspect.signature(run_matrix).parameters["smart_turn_stop_secs"].default
        == SMART_TURN_STOP_DEFAULT_SECS
    )


def test_matrix_parser_accepts_twilio_transport_and_fake_api() -> None:
    arguments = build_parser().parse_args(
        [
            "--stt",
            "soniox",
            "--tts",
            "speechify",
            "--scenario",
            "conveyancing",
            "--reps",
            "1",
            "--transport",
            "twilio",
            "--fake-api",
        ]
    )

    assert arguments.transport == "twilio"
    assert arguments.fake_api is True
