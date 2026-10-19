from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from inbound_core.config import (
    AssistantConfig,
    KnowledgeFAQ,
    PriceItem,
    load_assistant_config,
)
from inbound_core.setup_draft import SetupDraft


class TemplateSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=1)
    company_info: str | None = None
    faqs: list[KnowledgeFAQ] = Field(default_factory=list)
    price_list: list[PriceItem] = Field(default_factory=list)


class TemplateMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str
    order: int = 100
    sets_up: list[str] = Field(default_factory=list)
    sample: TemplateSample | None = None


class AssistantTemplate(BaseModel):
    name: str
    meta: TemplateMeta
    config: AssistantConfig


_TEMPLATE_NAME = re.compile(r"^[a-z0-9_]+$")


def load_template(path: str | Path, *, template_dir: str | Path | None = None) -> AssistantTemplate:
    template_path = Path(path)
    name = template_path.stem
    if not _TEMPLATE_NAME.fullmatch(name):
        raise ValueError(f"Invalid template name: {name!r}")

    with template_path.open(encoding="utf-8") as template_file:
        template_data = yaml.safe_load(template_file) or {}
    metadata = template_data.pop("template", None)
    if metadata is None:
        raise ValueError(f"Template {name!r} is missing required template metadata.")
    meta = TemplateMeta.model_validate(metadata)
    config = load_assistant_config(
        template_path,
        template_dir=template_dir or template_path.parent,
    )
    return AssistantTemplate(name=name, meta=meta, config=config)


def list_templates(template_dir: str | Path) -> list[AssistantTemplate]:
    directory = Path(template_dir)
    templates = [
        load_template(path, template_dir=directory)
        for path in directory.glob("*.yaml")
        if path.is_file()
    ]
    return sorted(templates, key=lambda template: (template.meta.order, template.name))


def find_template(template_dir: str | Path, name: str) -> AssistantTemplate | None:
    if not _TEMPLATE_NAME.fullmatch(name):
        return None
    path = Path(template_dir) / f"{name}.yaml"
    if not path.is_file():
        return None
    return load_template(path, template_dir=template_dir)


def sample_setup_draft(sample: TemplateSample) -> SetupDraft:
    return SetupDraft(
        company_info=sample.company_info,
        faqs=sample.faqs,
        price_list=sample.price_list,
    )
