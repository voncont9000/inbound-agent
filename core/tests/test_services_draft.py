import json

from inbound_core.config import AssistantConfig
from inbound_core.setup_draft import build_services_draft_prompt, parse_services_draft


def _config() -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "business_name": "Alessandro's",
            "greeting": "Hello",
            "instructions": "Take table reservations and answer questions about the menu.",
            "office_hours": {"days": ["Monday"], "opens": "09:00", "closes": "17:00"},
            "services": {"general_enquiry": {"label": "General enquiry", "fields": []}},
            "price_list": [],
            "policies": [],
            "contacts": {},
            "urgency_rules": [],
            "live_actions": [],
        }
    )


def test_prompt_includes_instructions_and_faqs() -> None:
    prompt = build_services_draft_prompt(_config())
    assert "table reservations" in prompt
    assert "Alessandro's" in prompt


def test_parse_services_draft_builds_fields_and_reuses_existing_keys() -> None:
    raw = json.dumps(
        {
            "services": [
                {
                    "label": "Table reservation",
                    "fields": [
                        {"label": "Name", "type": "string", "required": True, "critical": True},
                        {
                            "label": "Phone number",
                            "type": "phone",
                            "required": True,
                            "critical": True,
                        },
                        {"label": "Party size", "type": "integer", "required": True},
                        {"label": "Preferred time", "type": "bogus"},
                    ],
                },
                {"label": "general enquiry", "fields": []},
                {"label": "", "fields": []},
            ]
        }
    )
    services, warnings = parse_services_draft(raw, _config())
    assert warnings == []
    assert list(services) == ["table_reservation", "general_enquiry"]
    reservation = services["table_reservation"]
    assert [field.name for field in reservation.fields] == [
        "name",
        "phone_number",
        "party_size",
        "preferred_time",
    ]
    assert reservation.fields[3].type == "string"
    assert reservation.fields[0].critical is True
    assert [field.name for field in services["general_enquiry"].fields] == [
        "caller_name",
        "phone",
        "email",
    ]


def test_parse_services_draft_rejects_bad_json() -> None:
    services, warnings = parse_services_draft("nope", _config())
    assert services == {}
    assert warnings == ["The suggestion was not valid JSON."]
