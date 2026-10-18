import calendar
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, create_model

from inbound_voice.config import FieldSpec, ServiceConfig


def _strip(value: str) -> str:
    return value.strip()


def _annotation_for(field: FieldSpec):
    if field.type == "email":
        return EmailStr
    if field.type == "phone":
        return Annotated[str, AfterValidator(_strip)]
    if field.type == "date":
        return date
    if field.type == "boolean":
        return bool
    if field.type == "integer":
        return int
    if field.type == "number":
        return float
    if field.type == "enum":
        return Literal[tuple(field.choices or [])]
    if field.type == "list":
        return list[str]
    return str


def build_service_model(service: ServiceConfig) -> type[BaseModel]:
    definitions = {
        field.name: (
            _annotation_for(field) | None,
            Field(default=None, json_schema_extra={"field_type": field.type}),
        )
        for field in service.fields
    }
    return create_model(
        f"{service.label.replace(' ', '')}Intake",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


def deadline_from(start: date, add_months: int, minus_days: int) -> date:
    month_index = start.month - 1 + add_months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=minus_days)


def days_until_deadline(
    start: date,
    add_months: int,
    minus_days: int,
    today: date | None = None,
) -> int:
    return (deadline_from(start, add_months, minus_days) - (today or date.today())).days
