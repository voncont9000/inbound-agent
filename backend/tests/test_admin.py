import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from httpx import AsyncClient

from inbound_api.settings import get_settings


def _seed_conversations(owner_database_url: str, tenant_pair: dict) -> dict[str, UUID]:
    now = datetime.now(UTC)
    tenant_ids = [UUID(tenant_pair["first"]["tenant_id"]), UUID(tenant_pair["second"]["tenant_id"])]
    assistant_ids = [uuid4(), uuid4()]
    conversation_ids = [uuid4(), uuid4(), uuid4()]
    with psycopg.connect(owner_database_url) as connection:
        for index, tenant_id in enumerate(tenant_ids):
            connection.execute(
                "INSERT INTO assistants (id, tenant_id, name, config) "
                "VALUES (%s, %s, %s, '{}'::jsonb)",
                (assistant_ids[index], tenant_id, f"Admin assistant {index}"),
            )
        for index, conversation_id in enumerate(conversation_ids):
            tenant_index = 1 if index == 2 else 0
            connection.execute(
                "INSERT INTO conversations "
                "(id, tenant_id, assistant_id, channel, external_ref, status, started_at, "
                "ended_at, summary, metrics) "
                "VALUES (%s, %s, %s, 'phone', %s, 'completed', %s, %s, %s, %s::jsonb)",
                (
                    conversation_id,
                    tenant_ids[tenant_index],
                    assistant_ids[tenant_index],
                    str(conversation_id),
                    now - timedelta(hours=5 - index),
                    now - timedelta(hours=4 - index),
                    "Quality conversation",
                    json.dumps({"test_call": index == 0, "trace_id": "trace-example"})
                    if index == 0
                    else "{}",
                ),
            )
            if index < 2:
                objective = (
                    {"response_p50_s": 1.2, "response_p95_s": 3.1}
                    if index == 0
                    else {"response_p50_s": 2.2, "response_p95_s": 4.1}
                )
                connection.execute(
                    "INSERT INTO conversation_scores "
                    "(tenant_id, conversation_id, assistant_id, judge_model, judge_version, "
                    "overall, needs_review, checks, issues, objective) "
                    "VALUES (%s, %s, %s, 'test-judge', 'v1', %s, %s, "
                    "%s::jsonb, %s::jsonb, %s::jsonb)",
                    (
                        tenant_ids[tenant_index],
                        conversation_id,
                        assistant_ids[tenant_index],
                        3 if index == 0 else 5,
                        index == 0,
                        json.dumps(
                            [{"key": "answered_question", "passed": index != 0, "note": None}]
                        ),
                        json.dumps(["missed detail"] if index == 0 else []),
                        json.dumps(objective),
                    ),
                )
        for role, message in (
            ("caller", "I need help with A+B."),
            ("assistant", "I can help with that."),
            ("caller", "My email is a+b@example.test."),
        ):
            connection.execute(
                "INSERT INTO messages (tenant_id, conversation_id, role, text, ts) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tenant_ids[0], conversation_ids[0], role, message, now),
            )
        connection.execute(
            "INSERT INTO intakes (tenant_id, conversation_id, version, data) "
            "VALUES (%s, %s, 'merged', %s::jsonb)",
            (
                tenant_ids[0],
                conversation_ids[0],
                json.dumps(
                    {
                        "service": "consultation",
                        "fields": {
                            "email": {"value": "a+b@example.test", "confidence": 0.9},
                            "plain": "A.B",
                            "empty": {"value": ""},
                        },
                    }
                ),
            ),
        )
    return {
        "tenant_id": tenant_ids[0],
        "other_tenant_id": tenant_ids[1],
        "assistant_id": assistant_ids[0],
        "conversation_id": conversation_ids[0],
        "second_conversation_id": conversation_ids[1],
        "other_conversation_id": conversation_ids[2],
    }


async def test_admin_routes_overview_filters_details_and_eval_round_trip(
    tenant_pair, owner_database_url, monkeypatch
) -> None:
    first = tenant_pair["first"]
    second = tenant_pair["second"]
    settings = get_settings()
    monkeypatch.setattr(settings, "platform_admin_emails", [first["email"].lower()])
    monkeypatch.setattr(settings, "langfuse_host", "https://traces.example/")
    seeded = _seed_conversations(owner_database_url, tenant_pair)
    client: AsyncClient = first["client"]

    assert (await client.get("/me")).json()["user"]["is_platform_admin"] is True
    assert (await second["client"].get("/me")).json()["user"]["is_platform_admin"] is False

    overview = await client.get("/admin/overview", params={"days": 7})
    assert overview.status_code == 200, overview.text
    overview_data = overview.json()
    assert overview_data["conversations"] == 3
    assert overview_data["scored"] == 2
    assert overview_data["needs_review"] == 1
    assert overview_data["avg_overall"] == 4.0
    assert overview_data["response_p50_s"] == 1.7
    assert overview_data["response_p95_s"] == 3.6
    assert {item["tenant_id"] for item in overview_data["by_tenant"]} == {
        str(seeded["tenant_id"]),
        str(seeded["other_tenant_id"]),
    }
    assert overview_data["by_day"]
    assert overview_data["by_day"][-1]["date"] >= overview_data["by_day"][0]["date"]

    first_page = await client.get("/admin/conversations", params={"limit": 1})
    assert first_page.status_code == 200, first_page.text
    first_page_data = first_page.json()
    assert len(first_page_data["items"]) == 1
    assert first_page_data["next_cursor"]
    next_page = await client.get(
        "/admin/conversations",
        params={"limit": 1, "cursor": first_page_data["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert (
        first_page_data["items"][0]["conversation_id"]
        != next_page.json()["items"][0]["conversation_id"]
    )
    flagged = await client.get("/admin/conversations", params={"needs_review": "true"})
    assert [item["conversation_id"] for item in flagged.json()["items"]] == [
        str(seeded["conversation_id"])
    ]
    tenant_filter = await client.get(
        "/admin/conversations", params={"tenant_id": seeded["other_tenant_id"]}
    )
    assert {item["tenant_id"] for item in tenant_filter.json()["items"]} == {
        str(seeded["other_tenant_id"])
    }

    detail = await client.get(f"/admin/conversations/{seeded['conversation_id']}")
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()
    assert detail_data["trace_url"] == "https://traces.example/trace/trace-example"
    assert detail_data["score"]["overall"] == 3
    no_trace = await client.get(f"/admin/conversations/{seeded['second_conversation_id']}")
    assert no_trace.status_code == 200
    assert no_trace.json()["trace_url"] is None

    for _ in range(2):
        response = await client.post(f"/admin/conversations/{seeded['conversation_id']}/rescore")
        assert response.status_code == 202, response.text
        assert response.json() == {"queued": True}
    with psycopg.connect(owner_database_url) as connection:
        queued = connection.execute(
            "SELECT count(*) FROM jobs WHERE kind = 'conversation_score' "
            "AND status = 'queued' AND payload ->> 'conversation_id' = %s",
            (str(seeded["conversation_id"]),),
        ).fetchone()[0]
    assert queued == 1

    eval_case = await client.post(f"/admin/conversations/{seeded['conversation_id']}/eval-case")
    assert eval_case.status_code == 201, eval_case.text
    eval_case_id = eval_case.json()["eval_case_id"]
    cases = await client.get("/admin/eval-cases")
    created_case = next(item for item in cases.json() if item["id"] == eval_case_id)
    assert created_case["name"] == "Admin assistant 0 – " + detail_data["started_at"][:10]
    assert created_case["caller_utterances"] == [
        "I need help with A+B.",
        "My email is a+b@example.test.",
    ]
    assert created_case["checks"]["intake_checks"] == {
        "email": r"(?i)^a\+b@example\.test$",
        "plain": r"(?i)^A\.B$",
    }

    run_payload = {
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "git_sha": "abc123",
        "trigger": "manual",
        "summary": {"cases": 1, "passed": 1, "failed": 0},
        "results": [
            {
                "eval_case_id": eval_case_id,
                "passed": True,
                "failures": [],
                "metrics": {"duration_s": 2.5},
            }
        ],
    }
    wrong_token = await client.post(
        "/internal/eval-runs",
        json=run_payload,
        headers={"X-Service-Token": "incorrect"},
    )
    assert wrong_token.status_code == 401
    created_run = await client.post(
        "/internal/eval-runs",
        json=run_payload,
        headers={"X-Service-Token": settings.voice_service_token},
    )
    assert created_run.status_code == 201, created_run.text
    eval_run_id = created_run.json()["eval_run_id"]
    run_detail = await client.get(f"/admin/eval-runs/{eval_run_id}")
    assert run_detail.status_code == 200, run_detail.text
    assert run_detail.json()["summary"] == run_payload["summary"]
    assert run_detail.json()["results"][0]["metrics"] == {"duration_s": 2.5}
    run_list = await client.get("/admin/eval-runs", params={"limit": 10})
    assert any(item["id"] == eval_run_id for item in run_list.json())

    with psycopg.connect(owner_database_url) as connection:
        connection.execute("DELETE FROM eval_cases WHERE id = %s", (eval_case_id,))
        connection.execute("DELETE FROM eval_runs WHERE id = %s", (eval_run_id,))


async def test_admin_routes_require_admin_and_authentication(
    tenant_pair, api_client: AsyncClient, monkeypatch
) -> None:
    first = tenant_pair["first"]
    second = tenant_pair["second"]
    monkeypatch.setattr(get_settings(), "platform_admin_emails", [first["email"].lower()])
    conversation_id = uuid4()
    requests = [
        ("get", "/admin/overview"),
        ("get", "/admin/conversations"),
        ("get", "/admin/conversations/" + str(conversation_id)),
        ("post", "/admin/conversations/" + str(conversation_id) + "/rescore"),
        ("post", "/admin/conversations/" + str(conversation_id) + "/eval-case"),
        ("get", "/admin/eval-cases"),
        ("get", "/admin/eval-runs"),
        ("get", "/admin/eval-runs/" + str(uuid4())),
    ]
    for method, path in requests:
        unauthenticated = await getattr(api_client, method)(path)
        assert unauthenticated.status_code == 401, (path, unauthenticated.text)
        non_admin = await getattr(second["client"], method)(path)
        assert non_admin.status_code == 404, (path, non_admin.text)


async def test_auth_responses_include_platform_admin_flag(
    api_client: AsyncClient, owner_database_url, monkeypatch
) -> None:
    email = f"admin-{uuid4().hex}@example.com"
    monkeypatch.setattr(get_settings(), "platform_admin_emails", [email])
    payload = {
        "email": email,
        "password": "valid-platform-password",
        "display_name": "Quality Admin",
        "tenant_name": "Quality Tenant",
    }
    signup = await api_client.post("/auth/signup", json=payload)
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenants"][0]["id"]
    assert signup.json()["user"]["is_platform_admin"] is True
    login = await api_client.post(
        "/auth/login",
        json={"email": email, "password": payload["password"]},
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["is_platform_admin"] is True
    me = await api_client.get("/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["is_platform_admin"] is True
    with psycopg.connect(owner_database_url) as connection:
        connection.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        connection.execute("DELETE FROM users WHERE email = %s", (email,))
