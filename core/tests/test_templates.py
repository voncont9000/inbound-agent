from pathlib import Path

from inbound_core.config import DateDeadlineUrgencyRule, EmailTriageConfig
from inbound_core.prompts import render_business_context
from inbound_core.templates import find_template, list_templates, load_template

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "voice" / "config" / "templates"


def test_all_templates_have_valid_metadata_and_configs() -> None:
    paths = sorted(TEMPLATE_DIR.glob("*.yaml"))
    templates = [load_template(path, template_dir=TEMPLATE_DIR) for path in paths]

    assert len({template.name for template in templates}) == len(templates)
    assert [template.name for template in list_templates(TEMPLATE_DIR)] == [
        "blank",
        "uk_law_firm",
        "restaurant",
        "dental_clinic",
        "home_services",
        "lettings_agency",
    ]
    assert all(template.meta.label and template.meta.description for template in templates)

    blank = next(template for template in templates if template.name == "blank")
    assert blank.config.policies == []
    assert blank.config.urgency_rules == []
    assert blank.config.rules.style is None
    assert blank.config.rules.off_topic is None
    assert blank.config.rules.angry_caller is None
    assert blank.config.rules.sales_calls is None
    assert blank.config.rules.ai_disclosure == "at_start"
    assert blank.config.rules.pronunciations == []
    assert blank.config.call_settings.technical_terms == []
    assert blank.config.live_actions == []
    assert len(blank.config.services) == 1
    assert blank.meta.sample is None

    for template in templates:
        if template.name == "blank":
            continue
        assert template.meta.sample is not None
        assert template.meta.sample.company_info.startswith("SAMPLE CONTENT")
        assert "(sample" in template.meta.sample.business_name
        assert template.config.policies
        assert template.config.urgency_rules
        assert template.config.email_triage != EmailTriageConfig()
        assert all(
            any(field.critical for field in service.fields)
            for service in template.config.services.values()
        )
        for rule in template.config.urgency_rules:
            if isinstance(rule, DateDeadlineUrgencyRule):
                assert rule.service in template.config.services
                assert rule.field in {
                    field.name for field in template.config.services[rule.service].fields
                }
        render_business_context(template.config)


def test_find_template_returns_none_for_unknown_name() -> None:
    assert find_template(TEMPLATE_DIR, "unknown") is None
