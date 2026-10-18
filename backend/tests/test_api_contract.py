import json
from pathlib import Path

from inbound_api.main import app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"
PRE_PLATFORM_API_COMPONENTS = {
    "ActionRunSummary",
    "AssistantConfig-Input",
    "AssistantConfig-Output",
    "AssistantCreate",
    "AssistantListResponse",
    "AssistantResponse",
    "AssistantUpdate",
    "AssistantVersionResponse",
    "AssistantVersionsResponse",
    "AuthResponse",
    "CallReportAccepted",
    "CallReportRequest",
    "CallStartRequest",
    "CommitmentResponse",
    "ContactDetailResponse",
    "ContactMergeRequest",
    "ContactMergeResponse",
    "ContactSplitRequest",
    "ContactSplitResponse",
    "ContactSummary",
    "ContactsResponse",
    "ConversationCreatedResponse",
    "ConversationDetailResponse",
    "ConversationListResponse",
    "ConversationReviewCreate",
    "ConversationReviewResponse",
    "ConversationSummary",
    "DateDeadlineUrgencyRule",
    "FactResponse",
    "FieldSpec",
    "HTTPValidationError",
    "IdentityResponse",
    "IntakeResponse",
    "JobResponse",
    "KeywordUrgencyRule",
    "KnowledgeConfig-Input",
    "KnowledgeConfig-Output",
    "KnowledgeDigestResponse",
    "KnowledgeDigestUpdate",
    "KnowledgeFAQ",
    "KnowledgeGapResponse",
    "KnowledgeGapUpdate",
    "KnowledgeGapsResponse",
    "KnowledgeSource",
    "KnowledgeSourceCreate",
    "KnowledgeSourceResponse",
    "KnowledgeSourcesResponse",
    "LoginRequest",
    "LogoutResponse",
    "MeResponse",
    "MessageResponse",
    "OfficeHours",
    "PostActionTestRequest",
    "PostActionTestResponse",
    "PostActionVariable",
    "WebhookSecretCreateResponse",
    "WebhookSecretListResponse",
    "WebhookSecretSummary",
    "PriceItem",
    "RouteResponse",
    "ServiceConfig",
    "SignupRequest",
    "TaskListResponse",
    "TaskSummary",
    "TaskUpdate",
    "TemplateResponse",
    "TestCallResponse",
    "TenantResponse",
    "TranscriptTurn",
    "UserResponse",
    "ValidationError",
}


def test_committed_openapi_is_current() -> None:
    assert json.loads(OPENAPI_PATH.read_text(encoding="utf-8")) == app.openapi()


def test_openapi_preserves_legacy_components_and_knowledge_source_create() -> None:
    components = app.openapi()["components"]["schemas"]

    assert "KnowledgeSourceCreate" in components
    assert PRE_PLATFORM_API_COMPONENTS <= components.keys()


def test_all_feature_routers_are_mounted() -> None:
    paths = set(app.openapi()["paths"])

    assert {
        "/auth/signup",
        "/auth/login",
        "/auth/logout",
        "/me",
        "/templates",
        "/assistant-config/schema",
        "/post-actions/variables",
        "/assistants",
        "/assistants/{assistant_id}",
        "/assistants/{assistant_id}/test-calls",
        "/assistants/{assistant_id}/channels",
        "/assistants/{assistant_id}/channels/{channel_id}",
        "/assistants/{assistant_id}/versions",
        "/assistants/{assistant_id}/knowledge/sources",
        "/assistants/{assistant_id}/knowledge/import",
        "/assistants/{assistant_id}/knowledge/digest",
        "/assistants/{assistant_id}/post-actions/test",
        "/assistants/{assistant_id}/post-actions/webhook-secrets",
        "/assistants/{assistant_id}/post-actions/{action_key}/webhook-secret",
        "/knowledge-gaps",
        "/knowledge-gaps/{gap_id}",
        "/jobs/{job_id}",
        "/conversations",
        "/conversations/{conversation_id}",
        "/conversations/{conversation_id}/reviews",
        "/tasks",
        "/tasks/{task_id}",
        "/contacts",
        "/contacts/{contact_id}",
        "/contacts/{contact_id}/merge",
        "/contacts/{contact_id}/split",
        "/internal/voice/route",
        "/internal/voice/web-route",
        "/internal/voice/calls",
        "/internal/voice/calls/{conversation_id}/report",
    } <= paths
