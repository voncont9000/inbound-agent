from pathlib import Path

import pytest
from pydantic import ValidationError

from inbound_core.config import (
    AssistantConfig,
    CallSettings,
    EmailTriageConfig,
    RecordingSettings,
    SheetsRowAction,
    assistant_config_json_schema,
    load_assistant_config,
)
from inbound_core.languages import (
    DEFAULT_LOCALE,
    DEFAULT_RECORDING_NOTICE,
    LOCALES,
    Locale,
    normalize_locale,
    spoken_language_instruction,
    written_language_instruction,
)


def _sheets_action(spreadsheet_id: str) -> SheetsRowAction:
    return SheetsRowAction.model_validate(
        {
            "kind": "sheets_row",
            "key": "save_row",
            "trigger": {"events": ["conversation_completed"]},
            "spreadsheet_id": spreadsheet_id,
            "columns": [{"header": "Name", "value_template": "{{contact.name}}"}],
        }
    )


def test_sheets_action_extracts_id_from_google_url() -> None:
    action = _sheets_action("https://docs.google.com/spreadsheets/u/0/d/sheet_123/edit#gid=0")

    assert action.spreadsheet_id == "sheet_123"


def test_sheets_action_accepts_bare_id() -> None:
    action = _sheets_action("sheet_123-abc")

    assert action.spreadsheet_id == "sheet_123-abc"


def test_sheets_action_rejects_unparseable_url() -> None:
    with pytest.raises(ValidationError):
        _sheets_action("https://docs.google.com/document/d/not-a-sheet/edit")


def test_template_resolution_and_deep_merge(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    assistants = tmp_path / "assistants"
    templates.mkdir()
    assistants.mkdir()
    (templates / "base.yaml").write_text(
        """
business_name: Example
greeting: Hello
office_hours:
  days: [Monday]
  opens: "09:00"
  closes: "17:00"
price_list: []
policies: [base]
contacts: {main: "hello@example.com"}
services: {}
urgency_rules: []
live_actions: []
""",
        encoding="utf-8",
    )
    config_path = assistants / "example.yaml"
    config_path.write_text(
        """
extends: base
policies: [override]
contacts:
  support: "support@example.com"
""",
        encoding="utf-8",
    )

    config = load_assistant_config(config_path, template_dir=templates)

    assert config.policies == ["override"]
    assert config.contacts == {
        "main": "hello@example.com",
        "support": "support@example.com",
    }
    assert config.knowledge.sources == []


def test_assistant_config_strips_template_metadata() -> None:
    templates = Path(__file__).resolve().parents[2] / "voice" / "config" / "templates"

    config = load_assistant_config(
        Path(__file__).resolve().parents[2] / "voice" / "config" / "assistants" / "demo_firm.yaml",
        template_dir=templates,
    )

    assert config.business_name == "Demo & Co Solicitors"


def test_single_locale_language_instructions_follow_the_customer() -> None:
    assert spoken_language_instruction("it-IT") == (
        "Start in Italian, even if the instructions, greeting or knowledge below are written in "
        "another language. Always reply in the language the caller is speaking: if they speak "
        "another language, reply in that language from your next reply on, even if they never "
        "spoke Italian. If you are unsure which language they speak, use Italian."
    )
    assert written_language_instruction("de-DE") == (
        "Write in German. If the customer writes in another language, reply in their language."
    )


def test_multilingual_spoken_instruction_and_recording_notices() -> None:
    assert spoken_language_instruction("multi") == (
        "Speak the language the caller uses, and switch with them if they change language; "
        "if unsure, use English."
    )
    assert LOCALES["it-IT"].recording_notice == (
        "Questa chiamata può essere registrata per finalità di qualità e formazione."
    )
    assert LOCALES["multi"].recording_notice == DEFAULT_RECORDING_NOTICE
    assert all(locale.recording_notice for locale in LOCALES.values())


def test_digest_path_is_loaded_relative_to_supplied_root(tmp_path: Path) -> None:
    config_path = tmp_path / "assistant.yaml"
    digest = tmp_path / "knowledge" / "digest.md"
    digest.parent.mkdir()
    digest.write_text("Business facts", encoding="utf-8")
    config_path.write_text(
        """
business_name: Example
greeting: Hello
office_hours:
  days: [Monday]
  opens: "09:00"
  closes: "17:00"
price_list: []
policies: []
contacts: {}
services: {}
urgency_rules: []
live_actions: []
knowledge:
  digest_path: knowledge/digest.md
""",
        encoding="utf-8",
    )

    config = load_assistant_config(config_path, digest_root=tmp_path)

    assert config.knowledge.digest_content == "Business facts"


def test_assistant_config_schema_is_available() -> None:
    schema = assistant_config_json_schema()

    assert schema == AssistantConfig.model_json_schema()
    assert "business_name" in schema["properties"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("English", "en-GB"),
        ("British English", "en-GB"),
        (" uk English ", "en-GB"),
        ("EN_gb", "en-GB"),
        ("American English", "en-US"),
        ("German", "de-DE"),
        ("de", "de-DE"),
        ("Español", "es-ES"),
        ("Mexican Spanish", "es-MX"),
        ("Français", "fr-FR"),
        ("Italian", "it-IT"),
        ("Brazilian Portuguese", "pt-BR"),
        ("multilingual", "multi"),
        ("unknown language", "en-GB"),
    ],
)
def test_legacy_language_values_normalize(value: str, expected: Locale) -> None:
    assert normalize_locale(value) == expected


def test_locale_config_compatibility_and_schema() -> None:
    config = AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "office_hours": {"days": ["Monday"], "opens": "09:00", "closes": "17:00"},
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {},
            "urgency_rules": [],
            "live_actions": [],
        }
    )
    assert config.language == DEFAULT_LOCALE
    assert config.voice_id is None

    without_voice = config.model_dump()
    without_voice.pop("voice_id")
    assert AssistantConfig.model_validate(without_voice).voice_id is None
    assert (
        AssistantConfig.model_validate({**without_voice, "language": "de-DE"}).language == "de-DE"
    )

    language_schema = assistant_config_json_schema()["properties"]["language"]
    assert language_schema["default"] == DEFAULT_LOCALE
    assert language_schema["enum"] == [
        "en-GB",
        "en-US",
        "de-DE",
        "es-ES",
        "es-MX",
        "fr-FR",
        "it-IT",
        "pt-BR",
        "multi",
    ]


def test_transfer_handoff_message_has_a_spoken_default() -> None:
    assert (
        AssistantConfig.model_fields["transfer_handoff_message"].default
        == "I'm putting you through now, please hold."
    )


def test_email_triage_defaults_and_rejects_extra_fields() -> None:
    config = EmailTriageConfig()

    assert config.enquiry == (
        "A customer or prospective customer asking about the business's services, prices, "
        "availability or their own request, which you can answer or move forward from the "
        "business context."
    )
    assert config.needs_human == (
        "A person must decide or act: complaints, disputes, threats or safety concerns, "
        "requests the business context does not cover, or anything you cannot answer "
        "without inventing facts."
    )
    assert config.spam == "Unsolicited marketing, phishing, scams or automated bulk mail."
    assert config.not_relevant == (
        "Genuine mail that needs no reply, such as a thank-you with no question, a delivery "
        "receipt, or a message clearly meant for a different business."
    )
    assert AssistantConfig.model_fields["email_triage"].default_factory is EmailTriageConfig

    with pytest.raises(ValidationError):
        EmailTriageConfig(unknown="not allowed")


def test_new_call_and_recording_settings_default_and_normalize() -> None:
    legacy = AssistantConfig.model_validate(
        {
            "business_name": "Example",
            "greeting": "Hello",
            "office_hours": {"days": ["Monday"], "opens": "09:00", "closes": "17:00"},
            "price_list": [],
            "policies": [],
            "contacts": {},
            "services": {},
            "urgency_rules": [],
            "live_actions": [],
        }
    )
    assert legacy.call_settings == CallSettings()
    assert legacy.recording == RecordingSettings()
    assert legacy.recording.enabled is True
    assert CallSettings(technical_terms=[" term ", "", "TERM", "Call"]).technical_terms == [
        "term",
        "Call",
    ]


def test_technical_terms_reject_too_long_values() -> None:
    with pytest.raises(ValidationError, match="at most 60 characters"):
        CallSettings(technical_terms=["x" * 61])
