import asyncio
import json
from types import SimpleNamespace

from inbound_voice.config import load_assistant_config
from inbound_voice.extraction import (
    _extraction_schema,
    extract_intake,
    merge_intake,
    reextract_run,
)
from inbound_voice.intake import build_service_model
from inbound_voice.tools import DEFAULT_CONFIG_PATH


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.arguments = None

    async def generate_content(self, **arguments):
        self.arguments = arguments
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response=response, error=error)
        self.aio = SimpleNamespace(models=self.models)


def _response(fields):
    return SimpleNamespace(
        text=json.dumps({"service": "employment", "fields": fields}),
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=25),
    )


def test_extraction_coerces_values_and_marks_invalid_values() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    client = FakeClient(
        _response(
            [
                {
                    "name": "caller_name",
                    "value": "  Alex Smith ",
                    "confidence": 0.91,
                    "evidence": "My name is Alex Smith.",
                },
                {
                    "name": "email",
                    "value": "not-an-email",
                    "confidence": 0.8,
                    "evidence": "My email is not-an-email.",
                },
            ]
        )
    )

    result = asyncio.run(
        extract_intake(
            [{"role": "caller", "text": "My name is Alex Smith."}],
            {"service": "employment", "fields": {}},
            config,
            client,
            "gemini-test",
        )
    )

    assert result["status"] == "completed"
    assert result["fields"]["caller_name"]["value"] == "  Alex Smith "
    assert result["fields"]["email"] == {
        "value": "not-an-email",
        "confidence": 0.0,
        "evidence": "My email is not-an-email.",
        "invalid": True,
    }
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 25}
    generation_config = client.models.arguments["config"]
    assert generation_config["response_mime_type"] == "application/json"
    assert "response_schema" not in generation_config
    assert "response_json_schema" in generation_config
    schema = generation_config["response_json_schema"]
    assert schema["properties"]["service"]["enum"] == list(config.services)
    assert schema["properties"]["fields"]["items"]["properties"]["name"]["enum"] == sorted(
        {field.name for service in config.services.values() for field in service.fields}
    )
    assert schema == _extraction_schema(config)
    assert "1. caller:" in client.models.arguments["contents"]


def test_extraction_can_be_skipped_or_fail_without_raising() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    skipped = asyncio.run(extract_intake([], {}, config, None, "gemini-test"))
    failed = asyncio.run(
        extract_intake([], {}, config, FakeClient(error=RuntimeError("offline")), "x")
    )

    assert skipped["status"] == "skipped"
    assert failed["status"] == "failed"
    assert "offline" in failed["error"]


def test_reextract_run_rewrites_intake_and_updates_extraction_cost(tmp_path, monkeypatch) -> None:
    import inbound_voice.extraction as extraction

    (tmp_path / "transcript.jsonl").write_text(
        json.dumps({"role": "caller", "text": "I was dismissed."}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "intake.live.json").write_text(
        json.dumps({"service": "employment", "fields": {}}),
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {
                "transport": "webrtc",
                "call_duration_seconds": 60,
                "providers": {
                    "stt": "soniox",
                    "tts": "speechify",
                    "llm_model": "gemini-3.5-flash-lite",
                },
                "usage": {
                    "tts_characters": 0,
                    "llm_input_tokens": 0,
                    "llm_output_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    async def fake_extract_intake(transcript_rows, live_intake, config, client, model):
        assert transcript_rows[0]["text"] == "I was dismissed."
        assert live_intake["service"] == "employment"
        assert client is None
        return {
            "status": "completed",
            "service": "employment",
            "model": model,
            "fields": {
                "issue_type": {
                    "value": "dismissal",
                    "confidence": 0.9,
                    "evidence": "I was dismissed.",
                }
            },
            "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        }

    monkeypatch.setattr(extraction, "extract_intake", fake_extract_intake)
    monkeypatch.setenv("EXTRACTION_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    result = asyncio.run(reextract_run(tmp_path, DEFAULT_CONFIG_PATH))

    merged = json.loads((tmp_path / "intake.json").read_text(encoding="utf-8"))
    post_call = json.loads((tmp_path / "intake.post_call.json").read_text(encoding="utf-8"))
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert result["extraction_status"] == "completed"
    assert merged["fields"]["issue_type"]["status"] == "filled"
    assert post_call["fields"]["issue_type"]["value"] == "dismissal"
    assert metrics["usage"]["extraction_input_tokens"] == 1_000_000
    assert metrics["estimated_cost"]["components"]["extraction"] == 4.5


def test_merge_intake_covers_agreed_live_filled_conflict_and_corrected() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])
    live = {
        "service": "employment",
        "fields": {
            "caller_name": {"value": "Alex Smith", "confirmed": True, "confirmed_at": "now"},
            "phone": {"value": "+44 7700 900123", "confirmed": True, "confirmed_at": "now"},
            "email": {"value": "old@example.com", "confirmed": True, "confirmed_at": "now"},
            "employer_name": {"value": "Old Ltd", "confirmed": False, "confirmed_at": "now"},
        },
    }
    post = {
        "service": "employment",
        "fields": {
            "caller_name": {
                "value": "alex smith",
                "confidence": 0.7,
                "evidence": "I'm Alex Smith.",
            },
            "phone": {"value": None, "confidence": 0.1, "evidence": ""},
            "email": {
                "value": "new@example.com",
                "confidence": 0.9,
                "evidence": "Use new@example.com.",
            },
            "employer_name": {
                "value": "New Ltd",
                "confidence": 0.8,
                "evidence": "I work for New Ltd.",
            },
            "issue_type": {
                "value": "dismissal",
                "confidence": 0.8,
                "evidence": "I was dismissed.",
            },
        },
    }

    result = merge_intake(live, post, service_model, 0.7)

    assert result["fields"]["caller_name"]["status"] == "agreed"
    assert result["fields"]["phone"]["status"] == "live_only"
    assert result["fields"]["email"]["status"] == "conflict"
    assert result["fields"]["email"]["alternative"] == "new@example.com"
    assert result["fields"]["employer_name"]["status"] == "corrected"
    assert result["fields"]["employer_name"]["alternative"] == "Old Ltd"
    assert result["fields"]["issue_type"]["status"] == "filled"
    assert result["needs_review"] == ["email"]


def test_merge_intake_flags_low_confidence_and_invalid_fields() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])
    post = {
        "service": "employment",
        "fields": {
            "caller_name": {
                "value": "Alex",
                "confidence": 0.2,
                "evidence": "I'm Alex.",
            },
            "email": {
                "value": "invalid-email",
                "confidence": 0,
                "evidence": "my email is invalid-email",
                "invalid": True,
            },
        },
    }

    result = merge_intake({"service": None, "fields": {}}, post, service_model, 0.7)

    assert result["needs_review"] == ["caller_name", "email"]
    assert result["fields"]["email"]["invalid"] is True


def test_merge_intake_normalizes_phone_numbers() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])

    result = merge_intake(
        {
            "service": "employment",
            "fields": {
                "phone": {
                    "value": "+44 7700 900123",
                    "confirmed": True,
                    "confirmed_at": "now",
                }
            },
        },
        {
            "service": "employment",
            "fields": {
                "phone": {
                    "value": "+44 (7700) 900123",
                    "confidence": 0.8,
                    "evidence": "07700 900123",
                }
            },
        },
        service_model,
        0.7,
    )

    assert result["fields"]["phone"]["status"] == "agreed"
