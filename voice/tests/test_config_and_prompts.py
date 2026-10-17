from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from inbound_voice.config import AssistantConfig, FieldSpec, ServiceConfig, load_assistant_config
from inbound_voice.prompts import render_system_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_assistant_config_inherits_template_and_renders_prompt() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    prompt = render_system_prompt(assistant, today=date(2026, 10, 17))

    assert assistant.business_name == "Demo & Co Solicitors"
    assert assistant.language == "British English"
    assert set(assistant.services) == {"conveyancing", "employment"}
    assert assistant.contacts["complaints_contact"] == "complaints@demoandco.example"
    assert len(assistant.price_list) == 4
    assert "AI assistant" in prompt
    assert "must never give legal advice" in prompt
    assert "Never confirm or deny" in prompt
    assert "Never estimate, invent or promise a quote" in prompt
    assert "call 999" in prompt
    assert "Today is 2026-10-17" in prompt
    assert "termination_date" in prompt
    assert assistant.live_actions == ["end_call"]
    assert "confirm_critical_fields" not in prompt
    assert "Speak British English." in prompt
    assert "acknowledge them briefly and continue" in prompt
    assert "Never ask the same question twice in a row" in " ".join(prompt.split())
    assert "return to it once before the end" in prompt
    assert "read back all collected, unconfirmed critical details in one sentence" in prompt
    assert "Read back names, phone numbers, email addresses and dates" in prompt
    assert "ask the caller to confirm them" in prompt
    assert "Do not call a tool to confirm critical fields" in prompt
    assert "creates an urgent task after the call" in prompt
    assert "Collect other details conversationally" in prompt
    assert "end_call" in prompt


def test_prompt_only_mentions_enabled_actions() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    assistant = assistant.model_copy(update={"live_actions": ["end_call"]})

    prompt = render_system_prompt(assistant)

    assert "end_call" in prompt
    assert "confirm_critical_fields" not in prompt
    assert "request_callback" not in prompt
    assert "flag_urgent" not in prompt
    assert "Read back names, phone numbers, email addresses and dates" in prompt
    assert "Do not call a tool to confirm critical fields" in prompt

    assistant = assistant.model_copy(
        update={"live_actions": ["confirm_critical_fields", "end_call"]}
    )
    prompt = render_system_prompt(assistant)
    assert "confirm_critical_fields" in prompt
    assert "Do not call a tool to confirm critical fields" not in prompt

    assistant = assistant.model_copy(update={"live_actions": []})
    prompt = render_system_prompt(assistant)

    assert "end_call" not in prompt
    assert "flag_urgent" not in prompt


def test_assistant_language_defaults_to_english() -> None:
    assistant = AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "office_hours": {
                "days": ["Monday"],
                "opens": "09:00",
                "closes": "17:00",
                "timezone": "Europe/London",
            },
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {},
            "urgency_rules": [],
            "live_actions": [],
        }
    )

    assert assistant.language == "English"


def test_field_spec_requires_enum_choices_and_unique_names() -> None:
    with pytest.raises(ValidationError, match="Enum fields require"):
        FieldSpec(name="status", label="Status", type="enum")
    with pytest.raises(ValidationError, match="unique"):
        ServiceConfig.model_validate(
            {
                "label": "Test",
                "fields": [
                    {"name": "status", "label": "Status", "type": "string"},
                    {"name": "status", "label": "Other status", "type": "string"},
                ],
            }
        )
