import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from inbound_core.config import AssistantConfig, RulesConfig, SentenceUrgencyRule
from inbound_core.extraction import (
    _extraction_prompt,
    _extraction_schema,
    build_intake_artifacts,
    build_post_call_tasks,
    extract_intake,
)
from inbound_core.intake import build_service_model, days_until_deadline
from inbound_core.pricing import calculate_cost, load_price_table
from inbound_core.prompts import render_business_context
from inbound_core.rules import render_ai_disclosure, render_conversation_rules


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


def test_default_rules_do_not_change_text_channel_business_context() -> None:
    config = _config()
    old_config_payload = config.model_dump(mode="json")
    old_config_payload.pop("rules")
    old_config = AssistantConfig.model_validate(old_config_payload)

    prompt = render_business_context(config, channel="whatsapp")

    assert old_config.rules == RulesConfig()
    assert render_business_context(old_config, channel="whatsapp") == prompt
    assert "CONVERSATION RULES" not in prompt
    assert (
        render_business_context(
            config.model_copy(update={"rules": RulesConfig(ai_disclosure="when_asked")}),
            channel="whatsapp",
        )
        == prompt
    )


def test_conversation_rule_presets_and_transfer_gating() -> None:
    config = _config().model_copy(
        update={
            "rules": RulesConfig(
                ai_disclosure="when_asked",
                style="friendly",
                off_topic="redirect",
                angry_caller="de_escalate",
                sales_calls="take_message",
            )
        }
    )

    phone_rules = render_conversation_rules(config, channel="phone")
    assert phone_rules == [
        "- Do not volunteer that you are an AI assistant. If asked whether you are a person, "
        "say honestly that you are an AI assistant.",
        "- Use a warm, friendly tone while staying professional.",
        "- If the caller raises something unrelated to Example business, say politely that you "
        "can only help with Example business enquiries and return to their enquiry.",
        "- If the caller is angry or upset, stay calm, acknowledge their frustration, apologise "
        "for the inconvenience without admitting fault, and focus on the next step.",
        "- If the caller is selling something or calls from a competitor, take their name, "
        "company and a one-line message for the team, then close politely.",
    ]
    assert not any("transfer" in line or "person" in line for line in phone_rules[3:])
    assert (
        "offer the transfer."
        in render_conversation_rules(config, channel="phone", handoff_available=True)[3]
    )
    assert all(
        "transfer" not in line for line in render_conversation_rules(config, channel="whatsapp")
    )
    assert any(
        "answer in one short sentence" in line
        for line in render_conversation_rules(
            config.model_copy(update={"rules": RulesConfig(off_topic="answer_briefly")}),
            channel="whatsapp",
        )
    )
    formal_config = _config()
    formal_config.rules.style = "formal"
    assert "- Use a formal, professional tone." in render_conversation_rules(
        formal_config, channel="phone"
    )
    neutral_config = _config()
    neutral_config.rules.style = "neutral"
    assert "- Use a neutral, matter-of-fact tone." in render_conversation_rules(
        neutral_config, channel="whatsapp"
    )
    assert any(
        line.endswith("decline politely and close the conversation.")
        for line in render_conversation_rules(
            config.model_copy(update={"rules": RulesConfig(sales_calls="decline")}),
            channel="whatsapp",
        )
    )


def test_text_channel_disclosure_preserves_whatsapp_prompts() -> None:
    config = _config()

    assert render_ai_disclosure(config, channel="whatsapp", first_reply=True) == (
        "- In this first reply only, include one short clause disclosing that you are the "
        "AI assistant for Example business, exactly once. Do not use the spoken call greeting."
    )
    assert render_ai_disclosure(config, channel="whatsapp", first_reply=False) == (
        "- This is a later reply. Do not disclose again that you are an AI assistant."
    )
    when_asked = config.model_copy(update={"rules": RulesConfig(ai_disclosure="when_asked")})
    assert render_ai_disclosure(when_asked, channel="whatsapp", first_reply=True) == (
        "- Do not volunteer that you are an AI assistant. If asked whether you are a person, "
        "say honestly that you are the AI assistant for Example business."
    )
    assert render_ai_disclosure(config, channel="gmail", first_reply=True) == ""


def test_sentence_urgency_extraction_and_task_deduplication() -> None:
    config = _config().model_copy(
        update={
            "urgency_rules": [
                SentenceUrgencyRule(type="sentence", text="A deadline is within four weeks.")
            ]
        }
    )

    class Models:
        async def generate_content(self, **arguments):
            self.arguments = arguments
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "service": "general",
                        "fields": [],
                        "summary": "The customer has a deadline.",
                        "callback_requested": {
                            "requested": False,
                            "reason": None,
                            "preferred_time": None,
                            "preferred_time_iso": None,
                        },
                        "complaint": {"made": False, "summary": None},
                        "unanswered_questions": [],
                        "urgency_matches": [
                            {
                                "rule": 0,
                                "reason": "The customer said a deadline is in two weeks.",
                                "evidence": "My deadline is in two weeks.",
                            },
                            {"rule": 1, "reason": "Invalid index", "evidence": "No match"},
                            {"rule": 0, "reason": "  ", "evidence": "Empty reason"},
                            {"rule": True, "reason": "Boolean index", "evidence": "No match"},
                        ],
                    }
                ),
                usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
            )

    models = Models()
    result = asyncio.run(
        extract_intake(
            [{"role": "caller", "text": "My deadline is in two weeks."}],
            {"service": "general", "fields": {}},
            config,
            SimpleNamespace(aio=SimpleNamespace(models=models)),
            "test-model",
            call_started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    assert "urgency_matches" in models.arguments["config"]["response_json_schema"]["properties"]
    assert "0. A deadline is within four weeks." in models.arguments["contents"]
    assert result["urgency_matches"] == [
        {
            "rule": 0,
            "reason": "The customer said a deadline is in two weeks.",
            "evidence": "My deadline is in two weeks.",
        }
    ]
    post_call, merged = build_intake_artifacts({"service": "general", "fields": {}}, result, config)
    assert post_call["urgency_matches"] == result["urgency_matches"]
    tasks = build_post_call_tasks(post_call, merged, config)
    assert tasks == [
        {
            "type": "urgent",
            "reason": "The customer said a deadline is in two weeks.",
            "rule": 0,
            "rule_text": "A deadline is within four weeks.",
            "evidence": "My deadline is in two weeks.",
            "source": "post_call",
        }
    ]
    assert build_post_call_tasks(post_call, merged, config, tasks) == []


def test_sentence_urgency_schema_and_prompt_are_omitted_without_sentence_rules() -> None:
    config = _config()
    args = (
        [],
        {"service": "general", "fields": {}},
        config,
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    schema = _extraction_schema(config)
    prompt = _extraction_prompt(*args)

    assert "urgency_matches" not in schema["properties"]
    assert "Urgency rules" not in prompt
    assert prompt == Path(__file__).with_name("extraction_prompt_main.txt").read_text()
