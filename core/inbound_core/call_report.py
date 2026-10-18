from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from inbound_core.config import AssistantConfig


class TranscriptTurn(BaseModel):
    role: Literal["caller", "assistant"]
    text: str
    timestamp: datetime


class CallStart(BaseModel):
    to_number: str
    from_number: str | None = None
    external_call_id: str
    channel: Literal["phone", "web"]
    started_at: datetime
    web_call_token: str | None = None


class CallTransferMetrics(BaseModel):
    target_key: str
    status: Literal[
        "answered",
        "completed",
        "no-answer",
        "busy",
        "failed",
        "canceled",
        "unknown",
        "error",
        "simulated",
    ]
    latency_ms: int | None = None


class CallReport(BaseModel):
    external_call_id: str
    ended_at: datetime
    end_reason: str | None = None
    summary: str | None = None
    transcript: list[TranscriptTurn]
    live_intake: dict[str, Any]
    post_call_intake: dict[str, Any] | None = None
    merged_intake: dict[str, Any]
    tasks: list[dict[str, Any]]
    knowledge_gaps: list[dict[str, Any]]
    metrics: dict[str, Any]
    estimated_cost: dict[str, Any] | None = None


class RouteResponse(BaseModel):
    tenant_id: UUID
    assistant_id: UUID
    config: AssistantConfig
    digest: str | None = None
    context_card: str | None = None
