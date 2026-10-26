import json
from uuid import uuid4

import pytest
import pytest_asyncio
from conftest import create_conversation

from inbound_api.settings import get_settings


def _headers() -> dict[str, str]:
    return {"X-Service-Token": get_settings().voice_service_token}


def _insert_case(
    graph_env,
    eval_case_ids,
    *,
    name,
    assistant_id,
    source_conversation_id=None,
    enabled=True,
    checks=None,
):
    case_id = uuid4()
    graph_env["execute"](
        "INSERT INTO eval_cases "
        "(id, source_conversation_id, name, assistant_id, caller_utterances, checks, enabled) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)",
        (
            case_id,
            source_conversation_id,
            name,
            assistant_id,
            json.dumps(["Hello"]),
            json.dumps(checks or {}),
            enabled,
        ),
    )
    eval_case_ids.append(case_id)
    return case_id


@pytest_asyncio.fixture
async def eval_case_ids(graph_env):
    case_ids = []
    yield case_ids
    for case_id in case_ids:
        graph_env["execute"]("DELETE FROM eval_cases WHERE id = %s", (case_id,))


@pytest.mark.asyncio
async def test_internal_eval_cases_require_service_token(api_client):
    assert (await api_client.get("/internal/eval-cases")).status_code == 401
    response = await api_client.get(
        "/internal/eval-cases",
        headers={"X-Service-Token": "wrong-token"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_eval_cases_returns_enabled_cases_and_resolves_source_tenant(
    api_client, graph_env, eval_case_ids
):
    config = {
        "business_name": "Evaluation Assistant",
        "greeting": "Hello",
        "office_hours": {"days": ["Monday"], "opens": "09:00", "closes": "17:00"},
        "price_list": [],
        "policies": [],
        "contacts": {},
        "services": {"general": {"label": "General", "fields": []}},
        "urgency_rules": [],
        "live_actions": [],
    }
    graph_env["execute"](
        "UPDATE assistants SET config = %s::jsonb WHERE id = %s",
        (json.dumps(config), graph_env["assistant_id"]),
    )
    conversation_id = create_conversation(graph_env)
    routed_id = _insert_case(
        graph_env,
        eval_case_ids,
        name="routed",
        assistant_id=graph_env["assistant_id"],
        source_conversation_id=conversation_id,
        checks={"transcript_checks": {"caller_greeting": "help"}},
    )
    no_source_id = _insert_case(
        graph_env,
        eval_case_ids,
        name="missing source conversation",
        assistant_id=graph_env["assistant_id"],
    )
    no_assistant_id = _insert_case(
        graph_env,
        eval_case_ids,
        name="no assistant",
        assistant_id=None,
        source_conversation_id=conversation_id,
    )
    disabled_id = _insert_case(
        graph_env,
        eval_case_ids,
        name="disabled",
        assistant_id=graph_env["assistant_id"],
        source_conversation_id=conversation_id,
        enabled=False,
    )

    response = await api_client.get("/internal/eval-cases", headers=_headers())

    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert set(items) == {str(routed_id), str(no_source_id), str(no_assistant_id)}
    assert str(disabled_id) not in items
    assert items[str(routed_id)]["source_conversation_id"] == str(conversation_id)
    assert items[str(routed_id)]["route"]["assistant_id"] == str(graph_env["assistant_id"])
    assert items[str(routed_id)]["checks"] == {
        "transcript_checks": {"caller_greeting": "help"},
        "assistant_checks": {},
        "intake_checks": {},
    }
    assert items[str(no_source_id)]["assistant_id"] == str(graph_env["assistant_id"])
    assert items[str(no_source_id)]["route"] is None
    assert items[str(no_assistant_id)]["assistant_id"] is None
    assert items[str(no_assistant_id)]["route"] is None
