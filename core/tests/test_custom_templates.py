import json

from inbound_core.custom_templates import (
    SECTORS,
    USE_CASES,
    build_custom_template_prompt,
    parse_custom_template,
)
from inbound_core.templates import find_template

TEMPLATES = "../voice/config/templates"


def _blank():
    template = find_template(TEMPLATES, "blank")
    assert template is not None
    return template.config


def test_prompt_mentions_sector_use_case_and_generic_rules() -> None:
    prompt = build_custom_template_prompt(
        sector=SECTORS[0], use_case=USE_CASES[1], details="we close on Sundays", base=_blank()
    )
    assert "Sector: Legal services" in prompt
    assert "Use case: Take bookings and reservations" in prompt
    assert "we close on Sundays" in prompt
    assert "Ask one question at a time." in prompt


def test_parse_builds_config_from_blank() -> None:
    raw = json.dumps(
        {
            "label": "Garage bookings",
            "description": "Books MOTs and services.",
            "sets_up": ["MOT and service booking intake", "Breakdown urgency"],
            "greeting": "Hello, you are speaking with the AI assistant for {business_name}.",
            "instructions": 'Say: "{greeting}". Ask for the registration. Hours: {office_hours}.',
            "policies": ["Never quote a final price over the phone."],
            "services": [
                {
                    "label": "MOT booking",
                    "fields": [
                        {"label": "Vehicle registration", "type": "string", "critical": True},
                        {"label": "Preferred date", "type": "date", "required": True},
                    ],
                }
            ],
        }
    )
    draft = parse_custom_template(raw, _blank(), fallback_label="Custom")
    assert draft.label == "Garage bookings"
    assert draft.sets_up == ["MOT and service booking intake", "Breakdown urgency"]
    assert draft.config.greeting.startswith(
        "Hello, you are speaking with the AI assistant for Your business"
    )
    assert "{greeting}" in draft.config.instructions
    assert draft.config.policies == ["Never quote a final price over the phone."]
    assert list(draft.config.services) == ["mot_booking"]
    assert draft.config.services["mot_booking"].fields[0].critical is True
    assert draft.warnings == []
    assert draft.config.language == "en-GB"


def test_parse_falls_back_to_blank_on_bad_json() -> None:
    base = _blank()
    draft = parse_custom_template("nope", base, fallback_label="Legal services")
    assert draft.label == "Legal services"
    assert draft.config == base
    assert draft.warnings


def test_parse_keeps_generic_instructions_without_greeting_placeholder() -> None:
    raw = json.dumps({"label": "X", "instructions": "no placeholders", "services": []})
    draft = parse_custom_template(raw, _blank(), fallback_label="Custom")
    assert draft.config.instructions == _blank().instructions
    assert any("instructions" in warning for warning in draft.warnings)
    assert any("services" in warning for warning in draft.warnings)
