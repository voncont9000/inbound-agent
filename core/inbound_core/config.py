from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from inbound_core.phone import normalise_phone


class OfficeHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[str]
    opens: str
    closes: str
    timezone: str = "Europe/London"


class TransferTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    phone: str
    when_to_use: str
    office_hours_only: bool = True

    @field_validator("phone")
    @classmethod
    def normalise_transfer_phone(cls, value: str) -> str:
        normalised = normalise_phone(value)
        if normalised is None:
            raise ValueError("Transfer phone must be a valid phone number.")
        return normalised


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


class SentenceUrgencyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sentence"]
    text: str = Field(min_length=1)


class PronunciationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1)
    say_as: str = Field(min_length=1)


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_disclosure: Literal["at_start", "when_asked"] = "at_start"
    style: Literal["formal", "friendly", "neutral"] | None = None
    off_topic: Literal["redirect", "answer_briefly"] | None = None
    angry_caller: Literal["de_escalate"] | None = None
    sales_calls: Literal["decline", "take_message"] | None = None
    pronunciations: list[PronunciationEntry] = Field(default_factory=list)


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


class AppointmentType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    duration_minutes: int = Field(gt=0, le=480)
    when_to_use: str
    location_or_link: str | None = None


class BookingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_id: str = "primary"
    appointment_types: list[AppointmentType] = Field(min_length=1)
    min_notice_hours: int = Field(default=2, ge=0)
    horizon_days: int = Field(default=14, ge=1, le=60)
    buffer_minutes: int = Field(default=0, ge=0)
    max_slots_offered: int = Field(default=3, ge=1, le=5)
    slot_step_minutes: int = Field(default=30, ge=5, le=240)
    send_updates: Literal["all", "externalOnly", "none"] = "all"
    check_message: str = "Let me check the diary."
    book_message: str = "One moment while I book that."

    @model_validator(mode="after")
    def validate_unique_keys(self) -> BookingConfig:
        keys = [item.key for item in self.appointment_types]
        if len(keys) != len(set(keys)):
            raise ValueError("Appointment type keys must be unique.")
        return self


class EmailTriageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enquiry: str = (
        "A customer or prospective customer asking about the business's services, prices, "
        "availability or their own case, which you can answer or move forward from the "
        "business context."
    )
    needs_human: str = (
        "A person must decide or act: complaints, disputes, threats or safety concerns, "
        "requests the business context does not cover, or anything you cannot answer "
        "without inventing facts."
    )
    spam: str = "Unsolicited marketing, phishing, scams or automated bulk mail."
    not_relevant: str = (
        "Genuine mail that needs no reply, such as a thank-you with no question, a "
        "delivery receipt, or a message clearly meant for a different business."
    )


PostActionEvent = Literal[
    "conversation_completed",
    "urgent",
    "callback_requested",
    "needs_human",
    "complaint",
]


class PostActionTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[PostActionEvent] = Field(min_length=1)
    channels: list[Literal["phone", "web", "gmail", "whatsapp"]] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def deduplicate_events(self) -> PostActionTrigger:
        self.events = list(dict.fromkeys(self.events))
        return self


class PostActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    enabled: bool = True
    trigger: PostActionTrigger


class EmailCustomerAction(PostActionBase):
    kind: Literal["email_customer"]
    subject_template: str = Field(min_length=1)
    body_template: str = Field(min_length=1)


class NotifyStaffAction(PostActionBase):
    kind: Literal["notify_staff"]
    to: list[EmailStr] = Field(min_length=1)
    subject_template: str = Field(min_length=1)
    body_template: str = Field(min_length=1)


class SheetColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: str = Field(min_length=1)
    value_template: str


class SheetsRowAction(PostActionBase):
    kind: Literal["sheets_row"]
    spreadsheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    sheet_name: str = Field(default="Sheet1", min_length=1)
    columns: list[SheetColumn] = Field(min_length=1)


class WebhookAction(PostActionBase):
    kind: Literal["webhook"]
    url: AnyHttpUrl


PostAction = Annotated[
    EmailCustomerAction | NotifyStaffAction | SheetsRowAction | WebhookAction,
    Field(discriminator="kind"),
]


UrgencyRule = Annotated[
    KeywordUrgencyRule | DateDeadlineUrgencyRule | SentenceUrgencyRule,
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
    rules: RulesConfig = Field(default_factory=RulesConfig)
    post_actions: list[PostAction] = Field(default_factory=list)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    live_actions: list[
        Literal[
            "confirm_critical_fields",
            "flag_urgent",
            "request_callback",
            "log_complaint",
            "end_call",
            "check_availability",
            "book_appointment",
            "transfer_call",
        ]
    ]
    booking: BookingConfig | None = None
    transfer_targets: list[TransferTarget] = Field(default_factory=list)
    transfer_handoff_message: str = "I'm putting you through now, please hold."
    transfer_failed_message: str = (
        "Sorry, nobody is available to take your call right now. Someone will call you back "
        "as soon as possible. Goodbye."
    )
    email_triage: EmailTriageConfig = Field(default_factory=EmailTriageConfig)
    review_confidence_threshold: float = Field(default=0.7, ge=0, le=1)

    @model_validator(mode="after")
    def validate_post_actions(self) -> AssistantConfig:
        keys = [action.key for action in self.post_actions]
        if len(keys) != len(set(keys)):
            raise ValueError("Post-action keys must be unique.")
        service_keys = set(self.services)
        for action in self.post_actions:
            unknown = set(action.trigger.services) - service_keys
            if unknown:
                raise ValueError(
                    f"Post-action {action.key!r} references unknown service(s): "
                    f"{', '.join(sorted(unknown))}."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_transfer_target_keys(self) -> AssistantConfig:
        keys = [target.key for target in self.transfer_targets]
        if len(keys) != len(set(keys)):
            raise ValueError("Transfer target keys must be unique.")
        return self


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
