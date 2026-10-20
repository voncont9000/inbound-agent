import pytest
from pydantic import ValidationError

from inbound_core.config import AssistantConfig, EmailCustomerAction, NotifyStaffAction
from inbound_core.post_actions import (
    TEMPLATE_VARIABLES,
    derive_events,
    format_value,
    matched_events,
    render_subject,
    render_template,
)


def _config(**overrides):
    return AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "office_hours": {
                "days": ["Monday"],
                "opens": "09:00",
                "closes": "17:00",
            },
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {"repair": {"label": "Repair", "fields": []}},
            "urgency_rules": [],
            "live_actions": [],
            **overrides,
        }
    )


def _customer_action(**overrides):
    return EmailCustomerAction.model_validate(
        {
            "kind": "email_customer",
            "key": "confirmation",
            "trigger": {"events": ["conversation_completed"]},
            "subject_template": "Thanks",
            "body_template": "Received",
            **overrides,
        }
    )


def test_derive_events_always_includes_completion_and_maps_task_kinds():
    assert derive_events(
        ["urgent", "callback", "complaint", "needs_human", "email_needs_human"]
    ) == {
        "conversation_completed",
        "urgent",
        "callback_requested",
        "complaint",
        "needs_human",
    }
    assert derive_events(["dismissed_kind"]) == {"conversation_completed"}


def test_matched_events_applies_enabled_channel_service_and_event_filters():
    action = _customer_action(
        trigger={
            "events": ["conversation_completed", "urgent", "urgent"],
            "channels": ["phone"],
            "services": ["repair"],
        }
    )
    assert action.trigger.events == ["conversation_completed", "urgent"]
    assert matched_events(
        action,
        {"conversation_completed", "urgent"},
        channel="phone",
        service="repair",
    ) == {"conversation_completed", "urgent"}
    assert not matched_events(action, {"urgent"}, channel="web", service="repair")
    assert not matched_events(action, {"urgent"}, channel="phone", service="install")
    assert not matched_events(
        _customer_action(enabled=False),
        {"conversation_completed"},
        channel="phone",
        service="repair",
    )


def test_template_rendering_substitutes_plain_text_and_ignores_unknown_names():
    assert (
        render_template(
            "Hello {{ contact.name }} {{unknown}} {{contact.email}}",
            {"contact.name": "Ada", "contact.email": "ada@example.test"},
        )
        == "Hello Ada  ada@example.test"
    )
    assert render_subject("One\n{{contact.name}}\r", {"contact.name": "Ada"}) == "One Ada "


def test_template_rendering_substitutes_boolean_context_values():
    assert render_template("test call: {{test_call}}", {"test_call": True}) == "test call: true"
    assert render_template("test call: {{test_call}}", {"test_call": False}) == "test call: false"


@pytest.mark.parametrize("value", ["=SUM(A1:A2)", "+cmd", "-1", "@value", "\tvalue", "\rvalue"])
def test_sheet_cells_escape_formula_prefixes_in_values_only(value):
    assert (
        render_template("literal = {{value}}", {"value": value}, sheet_cell=True)
        == f"literal = '{value}"
    )
    assert render_template("=literal", {}, sheet_cell=True) == "=literal"


def test_format_value_handles_lists_booleans_and_none():
    assert format_value(["one", 2, None]) == "one, 2, "
    assert format_value(True) == "yes"
    assert format_value(False) == "no"
    assert format_value(None) == ""
    assert format_value(12) == "12"


def test_assistant_post_actions_validate_unique_keys_services_and_email():
    valid_action = {
        "kind": "notify_staff",
        "key": "alert",
        "trigger": {"events": ["urgent"]},
        "to": ["team@example.com"],
        "subject_template": "Urgent",
        "body_template": "Review.",
    }
    config = _config(post_actions=[valid_action])
    assert config.post_actions[0].to == ["team@example.com"]

    with pytest.raises(ValidationError, match="unique"):
        _config(post_actions=[valid_action, valid_action])
    with pytest.raises(ValidationError, match="unknown service"):
        _config(
            post_actions=[
                {
                    **valid_action,
                    "key": "alert",
                    "trigger": {"events": ["urgent"], "services": ["missing"]},
                }
            ]
        )
    with pytest.raises(ValidationError):
        NotifyStaffAction.model_validate({**valid_action, "to": ["not-an-email"]})
    with pytest.raises(ValidationError):
        _config(
            post_actions=[
                {
                    "kind": "email_customer",
                    "key": "customer",
                    "trigger": {"events": ["conversation_completed"]},
                    "to": ["not-permitted@example.test"],
                    "subject_template": "Hi",
                    "body_template": "Hello",
                }
            ]
        )


def test_template_variables_are_a_fixed_list_of_name_description_pairs():
    assert all(
        isinstance(name, str) and isinstance(description, str)
        for name, description in TEMPLATE_VARIABLES
    )
    assert ("test_call", "True for browser test calls.") in TEMPLATE_VARIABLES
