import asyncio
import json
from types import SimpleNamespace

from inbound_voice.config import load_assistant_config
from inbound_voice.extraction import extract_intake, merge_intake
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
    assert client.models.arguments["config"]["response_mime_type"] == "application/json"
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
