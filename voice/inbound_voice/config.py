from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


UrgencyRule = Annotated[
    Union[KeywordUrgencyRule, DateDeadlineUrgencyRule],
    Field(discriminator="type"),
]


class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str
    greeting: str
    office_hours: OfficeHours
    price_list: list[PriceItem]
    policies: list[str]
    contacts: dict[str, str]
    services: dict[str, ServiceConfig]
    urgency_rules: list[UrgencyRule]
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


def load_assistant_config(path: str | Path) -> AssistantConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as config_file:
        assistant_data = yaml.safe_load(config_file) or {}
    template_name = assistant_data.pop("extends", None)
    if template_name:
        template_path = config_path.parent.parent / "templates" / f"{template_name}.yaml"
        with template_path.open(encoding="utf-8") as template_file:
            template_data = yaml.safe_load(template_file) or {}
        assistant_data = _deep_merge(template_data, assistant_data)
    return AssistantConfig.model_validate(assistant_data)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
