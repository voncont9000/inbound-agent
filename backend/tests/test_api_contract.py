import json
from pathlib import Path

from inbound_api.main import app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def test_committed_openapi_is_current() -> None:
    assert json.loads(OPENAPI_PATH.read_text(encoding="utf-8")) == app.openapi()


def test_all_feature_routers_are_mounted() -> None:
    paths = set(app.openapi()["paths"])

    assert {
        "/auth/signup",
        "/auth/login",
        "/auth/logout",
        "/me",
        "/templates",
        "/assistant-config/schema",
        "/assistants",
        "/assistants/{assistant_id}",
        "/assistants/{assistant_id}/channels",
        "/assistants/{assistant_id}/channels/{channel_id}",
        "/assistants/{assistant_id}/versions",
        "/assistants/{assistant_id}/knowledge/sources",
        "/assistants/{assistant_id}/knowledge/import",
        "/assistants/{assistant_id}/knowledge/digest",
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
        "/internal/voice/calls",
        "/internal/voice/calls/{conversation_id}/report",
    } <= paths
