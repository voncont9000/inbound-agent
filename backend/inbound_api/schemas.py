from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from inbound_core.call_report import CallReport, CallStart, RouteResponse
from inbound_core.config import AssistantConfig
from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class TemplateResponse(BaseModel):
    name: str
    config: AssistantConfig


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


class AssistantListResponse(BaseModel):
    items: list[AssistantResponse]


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


class ChannelConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assistant_id: UUID
    kind: Literal["phone", "gmail", "whatsapp", "web"]
    external_id: str
    status: str
    settings: dict[str, Any]
    created_at: datetime


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


class ConversationDetailResponse(ConversationSummary):
    messages: list[MessageResponse]
    intakes: list[IntakeResponse]
    needs_review: bool
    tasks: list[TaskSummary]
    metrics: dict[str, Any]
    estimated_cost: dict[str, Any] | None


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
