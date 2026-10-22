from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from inbound_core.call_report import CallReport, CallStart, CallTransferMetrics, RouteResponse
from inbound_core.config import AssistantConfig, PostAction
from inbound_core.languages import Locale
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_serializer,
    field_validator,
)

from inbound_api.integrations.google_auth import (
    GOOGLE_SCOPE_SETS,
    google_scopes_for_features,
    has_google_feature,
)


class ErrorResponse(BaseModel):
    detail: str


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str


class TenantResponse(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "staff"]


class AuthResponse(BaseModel):
    user: UserResponse
    tenants: list[TenantResponse]
    access_token: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    display_name: str
    tenant_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LogoutResponse(BaseModel):
    logged_out: bool


class MeResponse(BaseModel):
    user: UserResponse
    tenant: TenantResponse
    memberships: list[TenantResponse]


class TemplateResponse(BaseModel):
    name: str
    label: str
    description: str
    sets_up: list[str]
    sample_business_name: str | None = None
    config: AssistantConfig


class CustomTemplateOptions(BaseModel):
    sectors: list[str]
    use_cases: list[str]


class CustomTemplateRequest(BaseModel):
    sector: str = Field(min_length=1, max_length=80)
    use_case: str = Field(min_length=1, max_length=120)
    details: str = Field(default="", max_length=600)
    language: Locale | None = None


class CustomTemplateResponse(BaseModel):
    label: str
    description: str
    sets_up: list[str]
    config: AssistantConfig
    warnings: list[str]


class AssistantCreate(BaseModel):
    name: str
    template: str | None = None
    config: AssistantConfig


class AssistantUpdate(BaseModel):
    name: str | None = None
    template: str | None = None
    config: AssistantConfig


class AssistantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    template: str | None
    config: AssistantConfig
    version: int
    created_at: datetime
    updated_at: datetime
    conversation_count: int = 0


class AssistantListResponse(BaseModel):
    items: list[AssistantResponse]


class ConversationBulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=100)


class ConversationBulkDeleteResponse(BaseModel):
    deleted: int
    skipped_in_progress: int


class ServicesDraftResponse(BaseModel):
    services: dict[str, Any]
    warnings: list[str]


class TranslateCallSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_call_message: str | None = None
    still_there_prompt: str | None = None
    silence_goodbye_message: str | None = None


class AssistantLines(BaseModel):
    model_config = ConfigDict(extra="forbid")

    greeting: str | None = None
    call_settings: TranslateCallSettings | None = None
    transfer_handoff_message: str | None = None
    transfer_failed_message: str | None = None


class TranslateLinesRequest(BaseModel):
    locale: Locale
    lines: AssistantLines


class TranslateLinesResponse(AssistantLines):
    pass


class BulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=100)


class BulkDeleteResponse(BaseModel):
    deleted: int


class TestCallResponse(BaseModel):
    url: str
    expires_at: int


class VoiceLocaleOption(BaseModel):
    code: Locale
    label: str
    multilingual: bool


class VoiceCard(BaseModel):
    id: str
    name: str
    gender: str
    accent: str
    locale: str
    preview_url: str | None
    is_default: bool
    pauses_mid_sentence: bool = False


class VoiceCatalogueResponse(BaseModel):
    provider: Literal["speechify"] = "speechify"
    locale: Locale
    locales: list[VoiceLocaleOption]
    default_voice_id: str
    voices: list[VoiceCard]


class AssistantVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    config: AssistantConfig
    created_by: UUID
    created_at: datetime


class AssistantVersionsResponse(BaseModel):
    items: list[AssistantVersionResponse]


class ChannelConnectionCreate(BaseModel):
    kind: Literal["phone", "gmail", "whatsapp", "web"]
    external_id: str
    settings: dict[str, Any] = Field(default_factory=dict)


class EmailIncludeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["from", "to", "subject"]
    contains: str

    @field_validator("contains")
    @classmethod
    def validate_contains(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("contains must be between 1 and 200 characters")
        return value


class ChannelConnectionUpdate(BaseModel):
    include_rules: list[EmailIncludeRule] | None = Field(default=None, max_length=20)
    assistant_id: UUID | None = None


class WorkspaceChannelCreate(ChannelConnectionCreate):
    assistant_id: UUID


class GmailOAuthStartRequest(BaseModel):
    features: list[str] = Field(default_factory=list)

    @field_validator("features")
    @classmethod
    def validate_features(cls, features: list[str]) -> list[str]:
        requested = list(dict.fromkeys(features))
        google_scopes_for_features(requested)
        return requested


class ChannelConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID | None
    kind: Literal["phone", "gmail", "whatsapp", "web"]
    external_id: str
    status: str
    settings: dict[str, Any]
    created_at: datetime

    @field_serializer("settings")
    def serialize_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if self.kind == "gmail":
            return {key: value for key, value in settings.items() if key != "google_scopes"}
        return settings

    @computed_field
    @property
    def google_features(self) -> list[str]:
        if self.kind != "gmail":
            return []
        return [feature for feature in GOOGLE_SCOPE_SETS if has_google_feature(self, feature)]


class ChannelConnectionsResponse(BaseModel):
    items: list[ChannelConnectionResponse]


class KnowledgeSourceCreate(BaseModel):
    kind: Literal["url"]
    uri: str


class KnowledgeFileForm(BaseModel):
    filename: str
    content_type: str | None = None


class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID
    kind: Literal["url", "file"]
    uri: str
    status: str
    manifest: dict[str, Any]
    fetched_at: datetime | None
    created_at: datetime


class KnowledgeSourcesResponse(BaseModel):
    items: list[KnowledgeSourceResponse]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    run_after: datetime
    created_at: datetime


class KnowledgeDigestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID
    content: str
    edited_by: UUID | None
    created_at: datetime


class KnowledgeDigestUpdate(BaseModel):
    content: str


class KnowledgeGapResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID
    conversation_id: UUID
    question: str
    evidence: str
    status: Literal["open", "answered", "dismissed"]
    created_at: datetime


class KnowledgeGapsResponse(BaseModel):
    items: list[KnowledgeGapResponse]
    next_cursor: str | None = None


class KnowledgeGapUpdate(BaseModel):
    status: Literal["open", "answered", "dismissed"]


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID
    person_id: UUID | None
    channel: Literal["phone", "web", "gmail", "whatsapp"]
    external_ref: str
    status: Literal["active", "completed", "failed"]
    started_at: datetime
    ended_at: datetime | None
    summary: str | None
    end_reason: str | None
    needs_review: bool = False
    test_call: bool = False
    contact_name: str | None = None


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Literal["caller", "assistant", "operator", "system"]
    text: str
    ts: datetime
    meta: dict[str, Any]


class IntakeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: Literal["live", "post_call", "merged"]
    data: dict[str, Any]


class TaskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: Literal["open", "done", "dismissed"]
    due_at: datetime | None
    payload: dict[str, Any]
    source: Literal["live", "post_call", "manual"]
    conversation_id: UUID | None = None
    person_id: UUID | None = None


class ActionRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action_key: str
    action_kind: str
    status: Literal["queued", "succeeded", "failed", "skipped"]
    attempts: int
    fire_count: int
    pending_events: list[str]
    last_error: str | None
    output: dict[str, Any]
    fired_events: list[str]
    updated_at: datetime


class CallRecordingSummary(BaseModel):
    duration_seconds: float | None
    size_bytes: int
    created_at: datetime
    expires_at: datetime
    is_test: bool


class ConversationDetailResponse(ConversationSummary):
    messages: list[MessageResponse]
    intakes: list[IntakeResponse]
    tasks: list[TaskSummary]
    transfer: CallTransferMetrics | None = None
    metrics: dict[str, Any]
    estimated_cost: dict[str, Any] | None
    action_runs: list[ActionRunSummary] = Field(default_factory=list)
    recording: CallRecordingSummary | None = None


class PostActionTestRequest(BaseModel):
    action: PostAction
    conversation_id: UUID | None = None


class PostActionTestResponse(BaseModel):
    conversation_id: UUID
    events: list[str]
    matched_events: list[str]
    would_skip: str | None
    rendered: dict[str, Any]


class PostActionVariable(BaseModel):
    name: str
    description: str


class WebhookSecretSummary(BaseModel):
    action_key: str
    created_at: datetime


class WebhookSecretListResponse(BaseModel):
    items: list[WebhookSecretSummary]


class WebhookSecretCreateResponse(BaseModel):
    action_key: str
    secret: str
    created_at: datetime


class ConversationReviewCreate(BaseModel):
    field: str
    resolved_value: Any
    note: str | None = None


class ConversationReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field: str
    resolved_value: Any
    resolved_by: UUID
    note: str | None
    created_at: datetime


class TaskUpdate(BaseModel):
    status: Literal["open", "done", "dismissed"]


class TaskListResponse(BaseModel):
    items: list[TaskSummary]
    next_cursor: str | None = None


class ContactSummary(BaseModel):
    id: UUID
    display_name: str
    merged_into_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ContactsResponse(BaseModel):
    items: list[ContactSummary]
    next_cursor: str | None


class IdentityResponse(BaseModel):
    id: UUID
    type: Literal["phone", "email", "whatsapp"]
    value_normalized: str
    verified: bool
    confidence: float
    source: str


class FactResponse(BaseModel):
    id: UUID
    predicate: str
    value: Any | None
    valid_from: datetime
    valid_to: datetime | None
    evidence: str
    confidence: float
    status: Literal["active", "superseded", "rejected"]


class CommitmentResponse(BaseModel):
    id: UUID
    what: str
    due_at: datetime | None
    status: Literal["open", "kept", "broken", "cancelled"]


class ContactDetailResponse(ContactSummary):
    identities: list[IdentityResponse]
    active_facts: list[FactResponse]
    conversations: list[ConversationSummary]
    open_tasks: list[TaskSummary]
    open_commitments: list[CommitmentResponse]
    context_card: str


class ContactMergeRequest(BaseModel):
    other_id: UUID


class ContactMergeResponse(BaseModel):
    survivor_id: UUID
    merged_id: UUID


class ContactSplitRequest(BaseModel):
    identity_ids: list[UUID]


class ContactSplitResponse(BaseModel):
    person_id: UUID
    split_person_id: UUID


class ConversationCreatedResponse(BaseModel):
    conversation_id: UUID


class CallReportAccepted(BaseModel):
    accepted: bool


class VoiceRouteResponse(RouteResponse):
    pass


class CallReportRequest(CallReport):
    model_config = ConfigDict(extra="forbid")


class CallStartRequest(CallStart):
    model_config = ConfigDict(extra="forbid")
