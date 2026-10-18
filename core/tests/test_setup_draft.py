import json
from pathlib import Path

from inbound_core.config import AssistantConfig, KnowledgeFAQ, load_assistant_config
from inbound_core.setup_draft import (
    MAX_COMPANY_INFO_CHARS,
    SetupDraft,
    apply_setup_draft,
    build_setup_draft_prompt,
    common_intake_fields,
    parse_setup_draft,
    slugify_key,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "voice" / "config" / "templates"


def template_config() -> AssistantConfig:
    return load_assistant_config(TEMPLATES / "uk_law_firm.yaml", template_dir=TEMPLATES)


def test_prompt_includes_timezone_and_template_services() -> None:
    prompt = build_setup_draft_prompt("Northside Clinic", template_config(), "Clinic facts")

    assert "Northside Clinic" in prompt
    assert "Europe/London" in prompt
    assert "- conveyancing: Residential conveyancing" in prompt
    assert "Clinic facts" in prompt
    assert f"at most {MAX_COMPANY_INFO_CHARS} characters" in prompt


def test_parse_setup_draft_valid_sections() -> None:
    template = template_config()
    raw = json.dumps(
        {
            "company_info": "  A local clinic.  ",
            "faqs": [{"question": "Where are you?", "answer": "In town."}],
            "office_hours": {
                "days": ["monday", "FRIDAY"],
                "opens": "08:30",
                "closes": "17:00",
                "timezone": "Europe/London",
            },
            "price_list": [{"service": "Consultation", "price": "£40", "notes": "plus VAT"}],
            "services": [
                {"label": "Appointments", "template_key": "employment"},
                {"label": "General enquiries", "template_key": None},
            ],
        }
    )

    draft, warnings = parse_setup_draft(raw, template)

    assert warnings == []
    assert draft.company_info == "A local clinic."
    assert draft.faqs == [KnowledgeFAQ(question="Where are you?", answer="In town.")]
    assert draft.office_hours is not None
    assert draft.office_hours.days == ["Monday", "Friday"]
    assert draft.price_list is not None and draft.price_list[0].price == "£40"
    assert draft.services is not None
    assert draft.services["employment"] == template.services["employment"]
    assert draft.services["general_enquiries"].label == "General enquiries"


def test_parse_invalid_json_returns_empty_draft_and_warning() -> None:
    draft, warnings = parse_setup_draft("{", template_config())

    assert draft == SetupDraft()
    assert warnings


def test_parse_keeps_valid_sections_and_drops_invalid_items() -> None:
    draft, warnings = parse_setup_draft(
        json.dumps(
            {
                "company_info": "Useful details",
                "faqs": [
                    {"question": "Valid question?", "answer": "Valid answer."},
                    {"question": "Missing answer"},
                ],
                "office_hours": {
                    "days": ["Funday"],
                    "opens": "9am",
                    "closes": "17:00",
                    "timezone": "Not/AZone",
                },
                "price_list": [],
                "services": [],
            }
        ),
        template_config(),
    )

    assert draft.company_info == "Useful details"
    assert draft.faqs is not None and len(draft.faqs) == 1
    assert draft.office_hours is None
    assert draft.price_list is None
    assert draft.services is None
    assert any("Opening hours" in warning for warning in warnings)
    assert any("Dropped 1 invalid FAQ." in warning for warning in warnings)


def test_parse_truncates_company_info_and_normalizes_services() -> None:
    template = template_config()
    raw = json.dumps(
        {
            "company_info": "x" * (MAX_COMPANY_INFO_CHARS + 20),
            "services": [
                {"label": "Residential conveyancing", "template_key": "conveyancing"},
                {"label": "General enquiries", "template_key": None},
                {"label": "General enquiries", "template_key": None},
                {"label": "  "},
            ],
        }
    )
    draft, _ = parse_setup_draft(raw, template)

    assert draft.company_info is not None
    assert len(draft.company_info) == MAX_COMPANY_INFO_CHARS
    assert draft.services is not None
    assert list(draft.services) == ["conveyancing", "general_enquiries", "general_enquiries_2"]
    assert [field.name for field in draft.services["general_enquiries"].fields] == [
        field.name for field in common_intake_fields(template)
    ]


def test_common_intake_fields_falls_back_when_template_has_no_services() -> None:
    template = template_config().model_copy(update={"services": {}})

    fields = common_intake_fields(template)

    assert [(field.name, field.label, field.type, field.critical) for field in fields] == [
        ("caller_name", "Name", "string", True),
        ("phone", "Phone number", "phone", True),
        ("email", "Email address", "email", False),
    ]
    assert slugify_key("  --- ", set()) == "service"


def test_apply_draft_uses_only_accepted_sections_and_prunes_references() -> None:
    template = template_config()
    template_data = template.model_dump(mode="python")
    template_data["knowledge"]["faqs"] = [
        {"question": "  When are you open? ", "answer": "Template hours."},
        {"question": "Do you offer callbacks?", "answer": "Yes."},
    ]
    template_data["post_actions"] = [
        {
            "kind": "email_customer",
            "key": "service_followup",
            "enabled": True,
            "trigger": {"events": ["conversation_completed"], "services": ["employment"]},
            "subject_template": "Follow up",
            "body_template": "Thanks",
        }
    ]
    template = AssistantConfig.model_validate(template_data)
    accepted = SetupDraft(
        company_info="Accepted summary",
        faqs=[
            KnowledgeFAQ(question="when are you open?", answer="Accepted hours."),
            KnowledgeFAQ(question="Can I book online?", answer="Yes."),
        ],
        price_list=[],
        services={"conveyancing": template.services["conveyancing"]},
    )

    result = apply_setup_draft(template, "New business name", accepted)

    assert result.business_name == "New business name"
    assert result.office_hours == template.office_hours
    assert result.price_list == []
    assert [(faq.question.strip(), faq.answer) for faq in result.knowledge.faqs] == [
        ("when are you open?", "Accepted hours."),
        ("Do you offer callbacks?", "Yes."),
        ("Can I book online?", "Yes."),
    ]
    assert set(result.services) == {"conveyancing"}
    assert not any(rule.type == "date_deadline" for rule in result.urgency_rules)
    assert result.post_actions == []
    assert "Accepted summary" not in result.model_dump_json()
