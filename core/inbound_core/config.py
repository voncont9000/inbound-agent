from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class OfficeHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[str]
    opens: str
    closes: str
    timezone: str = "Europe/London"


class PriceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    price: str
    notes: str


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: Literal[
        "string", "email", "phone", "date", "boolean", "integer", "number", "enum", "list"
    ]
    choices: list[str] | None = None
    required: bool = False
    critical: bool = False
    description: str = ""

    @model_validator(mode="after")
    def validate_choices(self) -> FieldSpec:
        if self.type == "enum" and not self.choices:
            raise ValueError("Enum fields require at least one choice.")
        if self.type != "enum" and self.choices is not None:
            raise ValueError("Choices are only valid for enum fields.")
        return self


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    fields: list[FieldSpec]

    @model_validator(mode="after")
    def validate_unique_names(self) -> ServiceConfig:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Field names must be unique within a service.")
        return self


class KeywordUrgencyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["keyword"]
    phrases: list[str]
    reason: str


class DateDeadlineUrgencyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["date_deadline"]
    service: str
    field: str
    add_months: int
    minus_days: int
    urgent_within_days: int
    reason: str


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl | None = None
    file: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> KnowledgeSource:
        if (self.url is None) == (self.file is None):
            raise ValueError("A knowledge source must have exactly one of url or file.")
        return self


class KnowledgeFAQ(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[KnowledgeSource] = Field(default_factory=list)
    faqs: list[KnowledgeFAQ] = Field(default_factory=list)
    digest_path: str | None = None
    max_digest_chars: int = Field(default=24_000, gt=0)
    digest_content: str | None = Field(default=None, exclude=True)


UrgencyRule = Annotated[
    KeywordUrgencyRule | DateDeadlineUrgencyRule,
    Field(discriminator="type"),
]


class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = "English"
    business_name: str
    greeting: str
    instructions: str = ""
    readback: Literal["off", "email", "critical"] = "off"
    office_hours: OfficeHours
    price_list: list[PriceItem]
    policies: list[str]
    contacts: dict[str, str]
    services: dict[str, ServiceConfig]
    urgency_rules: list[UrgencyRule]
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    live_actions: list[
        Literal[
            "confirm_critical_fields",
            "flag_urgent",
            "request_callback",
            "log_complaint",
            "end_call",
        ]
    ]
    review_confidence_threshold: float = Field(default=0.7, ge=0, le=1)


def assistant_config_json_schema() -> dict[str, Any]:
    return AssistantConfig.model_json_schema()


def load_assistant_config(
    path: str | Path,
    *,
    template_dir: str | Path | None = None,
    digest_root: str | Path | None = None,
    digest_path_override: str | Path | None = None,
) -> AssistantConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as config_file:
        assistant_data = yaml.safe_load(config_file) or {}
    template_name = assistant_data.pop("extends", None)
    if template_name:
        templates = (
            Path(template_dir)
            if template_dir is not None
            else config_path.parent.parent / "templates"
        )
        template_path = templates / f"{template_name}.yaml"
        with template_path.open(encoding="utf-8") as template_file:
            template_data = yaml.safe_load(template_file) or {}
        assistant_data = _deep_merge(template_data, assistant_data)
    config = AssistantConfig.model_validate(assistant_data)
    digest_path = (
        Path(digest_path_override)
        if digest_path_override is not None and str(digest_path_override).strip()
        else (Path(config.knowledge.digest_path) if config.knowledge.digest_path else None)
    )
    if digest_path is not None and not digest_path.is_absolute():
        root = Path(digest_root) if digest_root is not None else config_path.parent.parent
        digest_path = root / digest_path
    if digest_path is not None and digest_path.is_file():
        config.knowledge.digest_content = digest_path.read_text(encoding="utf-8")
    return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
