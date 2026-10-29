from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _enum(enum_class: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda values: [item.value for item in values],
    )


class MembershipRole(StrEnum):
    OWNER = "owner"
    STAFF = "staff"


class ChannelKind(StrEnum):
    PHONE = "phone"
    GMAIL = "gmail"
    GOOGLE = "google"
    WHATSAPP = "whatsapp"
    WEB = "web"


class KnowledgeGapStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class IdentityType(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class IdentityMergeAction(StrEnum):
    MERGE = "merge"
    SPLIT = "split"


class FactStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    KEPT = "kept"
    BROKEN = "broken"
    CANCELLED = "cancelled"


class ConversationChannel(StrEnum):
    PHONE = "phone"
    WEB = "web"
    GMAIL = "gmail"
    WHATSAPP = "whatsapp"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(StrEnum):
    CALLER = "caller"
    ASSISTANT = "assistant"
    OPERATOR = "operator"
    SYSTEM = "system"


class IntakeVersion(StrEnum):
    LIVE = "live"
    POST_CALL = "post_call"
    MERGED = "merged"


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class TaskSource(StrEnum):
    LIVE = "live"
    POST_CALL = "post_call"
    MANUAL = "manual"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TenantMixin(TimestampMixin):
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(Text, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(TenantMixin, Base):
    __tablename__ = "memberships"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MembershipRole] = mapped_column(
        _enum(MembershipRole, "membership_role"), nullable=False
    )


class Assistant(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "assistants"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    template: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))


class AssistantConfigVersion(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "assistant_config_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "assistant_id", "version"),)

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )


class ChannelConnection(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "channel_connections"

    assistant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    credentials_ciphertext: Mapped[str | None] = mapped_column(Text)


class KnowledgeSource(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "knowledge_sources"

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("tenant_id", "source_id", "chunk_index"),)

    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(768))


class KnowledgeDigest(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "knowledge_digests"

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))


class KnowledgeGap(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "knowledge_gaps"

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[KnowledgeGapStatus] = mapped_column(
        _enum(KnowledgeGapStatus, "knowledge_gap_status"),
        nullable=False,
        server_default=sql_text("'open'"),
    )


class Person(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "persons"

    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    merged_into_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id")
    )


class Organisation(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "organisations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)


class PersonOrgRole(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "person_org_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "person_id", "organisation_id", "role"),)

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)


class Identity(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("tenant_id", "type", "value_normalized"),)

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[IdentityType] = mapped_column(_enum(IdentityType, "identity_type"), nullable=False)
    value_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class IdentityMergeLog(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "identity_merge_log"

    action: Mapped[IdentityMergeAction] = mapped_column(
        _enum(IdentityMergeAction, "identity_merge_action"), nullable=False
    )
    survivor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id"), nullable=False
    )
    merged_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Fact(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint(
            "object_id IS NOT NULL OR value_json IS NOT NULL",
            name="ck_facts_object_or_value",
        ),
    )

    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    value_json: Mapped[Any | None] = mapped_column(JSONB)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[FactStatus] = mapped_column(
        _enum(FactStatus, "fact_status"), nullable=False, server_default=sql_text("'active'")
    )


class Commitment(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "commitments"

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    what: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CommitmentStatus] = mapped_column(
        _enum(CommitmentStatus, "commitment_status"),
        nullable=False,
        server_default=sql_text("'open'"),
    )
    source_message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )


class CalendarBooking(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "calendar_bookings"
    __table_args__ = (
        UniqueConstraint("conversation_id", "slot_id"),
        Index("ix_calendar_bookings_tenant_calendar_start", "tenant_id", "calendar_id", "start_at"),
    )

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL")
    )
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_link: Mapped[str | None] = mapped_column(Text)
    appointment_type: Mapped[str] = mapped_column(Text, nullable=False)
    slot_id: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attendee_name: Mapped[str] = mapped_column(Text, nullable=False)
    attendee_email: Mapped[str | None] = mapped_column(Text)
    attendee_phone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'booked'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Edge(TenantMixin, Base):
    __tablename__ = "edges"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    src_type: Mapped[str] = mapped_column(Text, primary_key=True)
    src_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    rel: Mapped[str] = mapped_column(Text, primary_key=True)
    dst_type: Mapped[str] = mapped_column(Text, primary_key=True)
    dst_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )


class Embedding(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "embeddings"

    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(768), nullable=False)


class Conversation(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_ref"),
        Index("ix_conversations_tenant_started", "tenant_id", "started_at"),
    )

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id"), nullable=False
    )
    person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL")
    )
    channel: Mapped[ConversationChannel] = mapped_column(
        _enum(ConversationChannel, "conversation_channel"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus, "conversation_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    end_reason: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    estimated_cost: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ConversationScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversation_scores"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    judge_model: Mapped[str] = mapped_column(Text, nullable=False)
    judge_version: Mapped[str] = mapped_column(Text, nullable=False)
    overall: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    issues: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    objective: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EvalCase(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "eval_cases"

    source_conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="SET NULL")
    )
    caller_utterances: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    checks: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text(
            '\'{"transcript_checks":{},"assistant_checks":{},"intake_checks":{}}\'::jsonb'
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))


class EvalRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "eval_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    git_sha: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'manual'"))
    summary: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text('\'{"cases":0,"passed":0,"failed":0}\'::jsonb'),
    )
    results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CallRecording(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "call_recordings"
    __table_args__ = (
        UniqueConstraint("conversation_id"),
        Index("ix_call_recordings_tenant_expires", "tenant_id", "expires_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_tenant_conversation_ts", "tenant_id", "conversation_id", "ts"),
        Index(
            "uq_messages_tenant_external_id",
            "tenant_id",
            "external_id",
            unique=True,
            postgresql_where=sql_text("external_id IS NOT NULL"),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )


class Intake(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "intakes"
    __table_args__ = (UniqueConstraint("conversation_id", "version"),)

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[IntakeVersion] = mapped_column(
        _enum(IntakeVersion, "intake_version"), nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IntakeReview(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "intake_reviews"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    resolved_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)


class Task(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "tasks"

    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    person_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), nullable=False, server_default=sql_text("'open'")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    source: Mapped[TaskSource] = mapped_column(_enum(TaskSource, "task_source"), nullable=False)


class ActionRun(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "action_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "action_key"),
        Index("ix_action_runs_tenant_conversation", "tenant_id", "conversation_id"),
        CheckConstraint("status IN ('queued', 'succeeded', 'failed', 'skipped')"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    action_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'queued'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    fired_events: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    pending_events: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    fire_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))


class PostActionSecret(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "post_action_secrets"
    __table_args__ = (UniqueConstraint("assistant_id", "action_key"),)

    assistant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False
    )
    action_key: Mapped[str] = mapped_column(Text, nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)


class Job(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, server_default=sql_text("'queued'")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
