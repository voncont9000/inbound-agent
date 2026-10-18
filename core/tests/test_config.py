from pathlib import Path

from inbound_core.config import AssistantConfig, assistant_config_json_schema, load_assistant_config


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
