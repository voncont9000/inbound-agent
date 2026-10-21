import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from inbound_voice.config import (
    AssistantConfig,
    FieldSpec,
    KnowledgeFAQ,
    ServiceConfig,
    load_assistant_config,
)
from inbound_voice.prompts import EMAIL_SPELLING_RULE, render_system_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_assistant_config_inherits_template_and_renders_prompt() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    prompt = render_system_prompt(
        assistant,
        today=date(2026, 10, 17),
        now=datetime(2026, 10, 17, 10, 0, tzinfo=UTC),
    )

    assert assistant.business_name == "Demo & Co Solicitors"
    assert assistant.language == "en-GB"
    assert set(assistant.services) == {"conveyancing", "employment"}
    assert "do I have a claim?" in assistant.email_triage.enquiry
    assert "existing clients chasing a live matter" in assistant.email_triage.needs_human
    assert assistant.contacts["complaints_contact"] == "complaints@demoandco.example"
    assert len(assistant.price_list) == 5
    assert "AI assistant" in prompt
    assert "must never give legal advice" in prompt
    assert "Never confirm or deny" in prompt
    assert "Never estimate, invent or promise a quote" in prompt
    assert "call 999" in prompt
    assert "Today is Saturday 17 October 2026 (2026-10-17)" in prompt
    assert "termination_date" in prompt
    assert assistant.live_actions == []
    assert "confirm_critical_fields" not in prompt
    assert "Start in British English, even if the instructions" in prompt
    assert "acknowledge them briefly and continue" in prompt
    assert "Never ask the same question twice in a row" in " ".join(prompt.split())
    assert "return to it once before the end" in prompt
    assert "creates an urgent task after the call" in prompt
    assert "Collect other details conversationally" in prompt
    assert "Offer a callback at most once" not in prompt
    assert "Offer to pass unanswered questions to the team." in prompt
    assert "Be honest when the business is closed." in prompt
    assert (
        "If the caller describes something urgent, acknowledge it and tell them the team will "
        "treat it as urgent."
    ) in " ".join(prompt.split())
    assert "When the caller is ready to finish, say goodbye." in " ".join(prompt.split())
    assert "Do not read back other details." in prompt
    assert (
        "If anyone is in immediate danger, tell them to call 999 now. Treat a hearing or arrest "
        "today, distress, or an imminent deadline as urgent; the team is alerted after the call."
    ) in prompt
    assert (
        "For employment termination dates, the system calculates the tribunal deadline. Do not "
        "calculate or reassure callers about legal time limits, and offer a callback."
    ) in prompt
    assert "do not separately flag the configured deadline" not in prompt
    assert "No live actions are enabled." not in prompt
    assert "end_call" not in prompt


def test_system_prompt_uses_passed_call_time_for_local_office_status() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    prompt = render_system_prompt(
        assistant,
        now=datetime(2026, 10, 19, 20, 59, tzinfo=UTC),
    )

    assert "Today is Monday 19 October 2026 (2026-10-19)." in prompt
    assert "21:59 (Europe/London), so the office is currently closed." in prompt


def test_system_prompt_matches_pre_shared_context_goldens() -> None:
    golden_path = ROOT / "tests" / "fixtures" / "render_system_prompt_before_shared_context.json"
    goldens = json.loads(golden_path.read_text(encoding="utf-8"))
    for key, expected in goldens.items():
        template, readback, caller_id, context_card = key.split("|")
        config = load_assistant_config(ROOT / "config" / "templates" / f"{template}.yaml")
        config = config.model_copy(update={"readback": readback})
        actual = render_system_prompt(
            config,
            today=date(2026, 1, 15),
            caller_id="+447700900123" if caller_id == "true" else None,
            context_card=(
                "Caller: Alex. Previously asked about opening hours."
                if context_card == "true"
                else None
            ),
            now=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )

        assert actual == expected, key


def test_prompt_only_mentions_enabled_actions() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    assistant = assistant.model_copy(update={"live_actions": ["end_call"]})

    prompt = render_system_prompt(assistant)

    assert "end_call" in prompt
    assert "confirm_critical_fields" not in prompt
    assert "request_callback" not in prompt
    assert "flag_urgent" not in prompt


def test_booking_prompt_matches_silent_tool_golden() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "booking_firm.yaml")
    prompt = render_system_prompt(
        assistant,
        today=date(2026, 1, 15),
        booking_enabled=True,
        now=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    )
    golden = (ROOT / "tests" / "fixtures" / "booking_system_prompt.txt").read_text(encoding="utf-8")

    assert prompt == golden
    assert "immediately without announcing it" in prompt
    assert "say one short line" not in prompt
    assert "same response" not in prompt


def test_caller_context_is_inserted_only_when_nonblank() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    now = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    original = render_system_prompt(assistant, now=now)

    assert render_system_prompt(assistant, context_card=None, now=now) == original
    assert render_system_prompt(assistant, context_card=" \n ", now=now) == original

    prompt = render_system_prompt(
        assistant,
        context_card="  Previously asked about a callback.  ",
        now=now,
    )

    assert "WHAT WE ALREADY KNOW ABOUT THIS CALLER" in prompt
    assert "<<<CALLER CONTEXT\nPreviously asked about a callback.\nCALLER CONTEXT>>>" in prompt
    assert prompt.index("CALLER CONTEXT>>>") < prompt.index("CALL BEHAVIOUR MECHANICS")
    assert (
        "When the caller gives an email address, spell it back once, letter by letter, "
        "and ask them to confirm. Do not read back other details."
    ) in prompt
    assert EMAIL_SPELLING_RULE in prompt
    assert "Read back names, phone numbers, email addresses and dates" not in prompt

    assistant = assistant.model_copy(
        update={"live_actions": ["confirm_critical_fields", "end_call"]}
    )
    prompt = render_system_prompt(assistant)
    assert "confirm_critical_fields" in prompt
    assert "If the caller confirms any values, call confirm_critical_fields" in prompt

    assistant = assistant.model_copy(update={"live_actions": []})
    prompt = render_system_prompt(assistant)

    assert "end_call" not in prompt
    assert "flag_urgent" not in prompt

    assistant = assistant.model_copy(update={"live_actions": ["flag_urgent"]})
    prompt = render_system_prompt(assistant)

    assert "Use flag_urgent when appropriate." in prompt
    assert "Do not call flag_urgent solely for this configured deadline." in prompt

    assistant = assistant.model_copy(update={"live_actions": []})
    prompt = render_system_prompt(assistant)
    assert "flag_urgent" not in prompt


def test_neutral_instructions_do_not_create_callback_behavior() -> None:
    assistant = AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "instructions": "Use neutral language.",
            "readback": "off",
            "office_hours": {
                "days": ["Monday"],
                "opens": "09:00",
                "closes": "17:00",
            },
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {},
            "urgency_rules": [],
            "live_actions": [],
        }
    )

    prompt = render_system_prompt(assistant)

    assert "callback" not in prompt.casefold()
    assert "Use neutral language." in prompt
    assert "Do not read details back to the caller." in prompt
    assert EMAIL_SPELLING_RULE not in prompt


def test_critical_readback_renders_existing_confirmation_mechanics() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    assistant.readback = "critical"

    prompt = render_system_prompt(assistant)

    assert "Read back names, phone numbers, email addresses and dates to confirm them." in prompt
    assert "read back all collected, unconfirmed critical details in one sentence" in prompt
    assert EMAIL_SPELLING_RULE in prompt


def test_caller_id_instruction_is_rendered_verbatim() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    prompt = render_system_prompt(assistant, caller_id="07700900456")

    assert (
        "The caller is calling from 07700900456. Do not ask for their phone number; ask once "
        "whether this is the best number to reach them on, and only take a different number "
        "if they say no."
    ) in prompt


def test_knowledge_prompt_includes_only_configured_sections() -> None:
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    assistant.knowledge.digest_content = None
    assert "Business knowledge (" not in render_system_prompt(assistant)

    assistant.knowledge.faqs = [
        KnowledgeFAQ(question="Do you use video calls?", answer="Yes, via Microsoft Teams.")
    ]
    prompt = render_system_prompt(assistant)
    assert "FAQs set by the business" in prompt
    assert "- Q: Do you use video calls? A: Yes, via Microsoft Teams." in prompt
    assert "Reference sheet:" not in prompt

    assistant.knowledge.digest_content = "## Other facts\nThe office is in Leeds."
    prompt = render_system_prompt(assistant)
    assert "Reference sheet:\n## Other facts\nThe office is in Leeds." in prompt
    assert 'Never mention sources, URLs in brackets or "CONFLICT"' in prompt
    assert "Keep answers to one or two short sentences" in prompt
    assert prompt.index("PUBLISHED PRICES") < prompt.index("Business knowledge")


def test_config_loads_digest_once_with_environment_override(tmp_path, monkeypatch) -> None:
    digest_path = tmp_path / "digest.md"
    digest_path.write_text("Loaded digest.", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DIGEST_PATH", str(digest_path))
    assistant = load_assistant_config(ROOT / "config" / "assistants" / "demo_firm.yaml")
    digest_path.write_text("Changed after config load.", encoding="utf-8")
    prompt = render_system_prompt(assistant)
    assert "Loaded digest." in prompt
    assert "Changed after config load." not in prompt


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

    assert assistant.language == "en-GB"


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
