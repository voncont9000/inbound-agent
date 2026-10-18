import inspect
from pathlib import Path

from bot import SMART_TURN_STOP_DEFAULT_SECS
from eval.run_matrix import _fake_api_digest, build_parser, run_matrix
from inbound_voice.config import load_assistant_config

ASSISTANT_PATH = Path(__file__).parents[1] / "config" / "assistants" / "demo_firm.yaml"


def test_fake_api_uses_fixture_knowledge_digest(tmp_path: Path) -> None:
    digest_path = tmp_path / "digest.md"
    digest_content = "Imported fixture knowledge"
    digest_path.write_text(digest_content, encoding="utf-8")
    assistant = load_assistant_config(ASSISTANT_PATH)

    assert _fake_api_digest(assistant, digest_path) == digest_content
    assert _fake_api_digest(assistant, None) == assistant.knowledge.digest_content


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
