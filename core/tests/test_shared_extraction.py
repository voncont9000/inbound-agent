import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from inbound_core.config import AssistantConfig
from inbound_core.extraction import _extraction_prompt, extract_intake
from inbound_core.intake import build_service_model, days_until_deadline
from inbound_core.pricing import calculate_cost, load_price_table
from inbound_core.prompts import render_business_context


def _config() -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "business_name": "Example business",
            "greeting": "Hello.",
            "office_hours": {
                "days": ["Monday"],
                "opens": "09:00",
                "closes": "17:00",
            },
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {
                "general": {
                    "label": "General enquiry",
                    "fields": [{"name": "caller_name", "label": "Name", "type": "string"}],
                }
            },
            "urgency_rules": [],
            "live_actions": [],
        }
    )


class _Models:
    def __init__(self):
        self.arguments = None

    async def generate_content(self, **arguments):
        self.arguments = arguments
        response = {
            "service": "general",
            "fields": [
                {
                    "name": "caller_name",
                    "value": "Alex Smith",
                    "confidence": 0.9,
                    "evidence": "My name is Alex Smith.",
                    "confirmed_by_caller": False,
                }
            ],
            "callback_requested": {
                "requested": False,
                "reason": None,
                "preferred_time": None,
                "preferred_time_iso": None,
            },
            "summary": "The customer asked a general question.",
            "complaint": {"made": False, "summary": None},
            "unanswered_questions": [],
        }
        return SimpleNamespace(
            text=json.dumps(response),
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        )


def test_extraction_uses_injected_client_and_message_thread_wording() -> None:
    config = _config()
    models = _Models()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    result = asyncio.run(
        extract_intake(
            [{"role": "caller", "text": "My name is Alex Smith."}],
            {"service": "general", "fields": {}},
            config,
            client,
            "test-model",
            call_started_at=datetime(2026, 1, 1, tzinfo=UTC),
            channel="whatsapp",
        )
    )

    assert result["status"] == "completed"
    assert result["fields"]["caller_name"]["value"] == "Alex Smith"
    assert "this message thread" in models.arguments["contents"]
    assert "Conversation start:" in models.arguments["contents"]
    assert "customer's messages" in models.arguments["contents"]


def test_default_and_phone_extraction_prompts_match() -> None:
    args = (
        [{"role": "caller", "text": "Hello"}],
        {"service": "general", "fields": {}},
        _config(),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert _extraction_prompt(*args) == _extraction_prompt(*args, channel="phone")


def test_core_intake_and_deadline_helpers() -> None:
    model = build_service_model(_config().services["general"])

    assert model.model_validate({"caller_name": "Alex"}).caller_name == "Alex"
    assert (
        days_until_deadline(
            date(2026, 1, 31),
            add_months=1,
            minus_days=1,
            today=date(2026, 2, 26),
        )
        == 1
    )


def test_core_pricing_and_table_loading(tmp_path) -> None:
    price_path = tmp_path / "prices.yaml"
    price_path.write_text("currency: USD\n", encoding="utf-8")
    assert load_price_table(price_path) == {"currency": "USD"}

    cost = calculate_cost(
        {
            "transport": "webrtc",
            "call_duration_seconds": 120,
            "providers": {"stt": "deepgram", "tts": "deepgram", "llm_model": "test-model"},
            "usage": {
                "tts_characters": 1000,
                "llm_input_tokens": 1000,
                "llm_output_tokens": 1000,
            },
        },
        {
            "currency": "USD",
            "prices": {
                "deepgram_stt_per_minute": {"value": 0.006},
                "deepgram_tts_per_1000_characters": {"value": 0.01},
                "llm_models": {
                    "test-model": {
                        "input_per_1m_tokens": {"value": 1.0},
                        "output_per_1m_tokens": {"value": 2.0},
                    }
                },
            },
        },
    )

    assert cost["components"]["stt"] == pytest.approx(0.012)
    assert cost["components"]["tts"] == 0.01
    assert cost["components"]["llm_input"] == pytest.approx(0.001)
    assert cost["components"]["llm_output"] == pytest.approx(0.002)
    assert cost["components"]["twilio"] == 0.0


def test_business_context_is_channel_neutral_and_includes_deadline_rules() -> None:
    config = AssistantConfig.model_validate(
        {
            **_config().model_dump(mode="json"),
            "urgency_rules": [
                {
                    "type": "date_deadline",
                    "service": "general",
                    "field": "caller_name",
                    "add_months": 3,
                    "minus_days": 1,
                    "urgent_within_days": 28,
                    "reason": "Time critical",
                }
            ],
        }
    )

    prompt = render_business_context(config, today=date(2026, 1, 15), channel="whatsapp")

    assert "You are the assistant for Example business." in prompt
    assert "DEADLINE INSTRUCTIONS" in prompt
    assert "after the conversation" in prompt
    assert "Speak " not in prompt


@pytest.mark.parametrize("channel", ["whatsapp", "gmail"])
def test_business_context_has_written_language_instruction(channel: str) -> None:
    config = _config().model_copy(update={"language": "de-DE"})
    prompt = render_business_context(config, channel=channel)

    assert "Write in German." in prompt


def test_multilingual_business_context_has_written_language_instruction() -> None:
    config = _config().model_copy(update={"language": "multi"})
    prompt = render_business_context(config, channel="whatsapp")

    assert "Reply in the language the customer writes in." in prompt
