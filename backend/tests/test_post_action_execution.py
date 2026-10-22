import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from inbound_core.config import AssistantConfig
from sqlalchemy import select

from inbound_api.db import tenant_session
from inbound_api.jobs import current_job
from inbound_api.models import (
    ActionRun,
    Assistant,
    ChannelConnection,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Intake,
    IntakeVersion,
    Job,
    Person,
    PostActionSecret,
    Task,
    TaskSource,
    TaskStatus,
)
from inbound_api.post_actions.providers import (
    FakeEmailSender,
    FakeWebhookSender,
    GmailEmailSender,
)
from inbound_api.post_actions.service import (
    build_action_context,
    execute_conversation_action,
    plan_conversation_actions,
    render_action_subject,
)
from inbound_api.settings import Settings, get_settings


async def _assistant_and_conversation(tenant_pair, actions):
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    config["post_actions"] = actions
    response = await client.post(
        "/assistants",
        json={"name": "Post-action test assistant", "template": "uk_law_firm", "config": config},
    )
    assert response.status_code == 201, response.text
    assistant_id = UUID(response.json()["id"])
    tenant_id = UUID(account["tenant_id"])
    conversation_id = uuid4()
    async with tenant_session(tenant_id) as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.WHATSAPP,
                external_ref=str(conversation_id),
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                summary="Customer asked for assistance.",
            )
        )
        session.add(
            Intake(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                version=IntakeVersion.MERGED,
                data={"service": "conveyancing", "fields": {}},
            )
        )
    return account, config, assistant_id, conversation_id


@pytest.mark.asyncio
async def test_action_context_includes_customer_recap_first_name_and_open_next_step(tenant_pair):
    account, config_data, assistant_id, conversation_id = await _assistant_and_conversation(
        tenant_pair, []
    )
    tenant_id = UUID(account["tenant_id"])
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        assistant = await session.get(Assistant, assistant_id)
        person = Person(tenant_id=tenant_id, display_name="Alex Morgan")
        session.add(person)
        await session.flush()
        conversation.person_id = person.id
        conversation.metrics = {"customer_recap": "You asked us to arrange a call."}
        context = await build_action_context(
            session,
            conversation=conversation,
            assistant=assistant,
            config=AssistantConfig.model_validate(config_data),
            tasks=[
                Task(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    kind="callback",
                    status=TaskStatus.DONE,
                    payload={"title": "Old callback"},
                    source=TaskSource.POST_CALL,
                ),
                Task(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    kind="follow_up",
                    status=TaskStatus.OPEN,
                    payload={"title": "Send details"},
                    source=TaskSource.POST_CALL,
                ),
            ],
        )

    assert context["contact.first_name"] == "Alex"
    assert context["customer_recap"] == "You asked us to arrange a call."
    assert context["next_step"] == "Send details"


async def _create_assistant(client, name):
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    response = await client.post(
        "/assistants",
        json={"name": name, "template": "uk_law_firm", "config": config},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _add_gmail_connection(account, assistant_id, email):
    tenant_id = UUID(account["tenant_id"])
    async with tenant_session(tenant_id) as session:
        session.add(
            ChannelConnection(
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                kind="gmail",
                external_id=email,
                status="active",
                settings={"google_features": ["gmail"]},
            )
        )


async def _execute_gmail_action(account, conversation_id, *, transport):
    tenant_id = UUID(account["tenant_id"])
    settings = Settings(email_sender="gmail")
    async with tenant_session(tenant_id) as session:
        await plan_conversation_actions(session, conversation_id)
    async with tenant_session(tenant_id) as session:
        result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="staff",
            fire_count=1,
            settings=settings,
            email_sender=GmailEmailSender(session, transport=transport, settings=settings),
        )
        run = await session.scalar(
            select(ActionRun).where(ActionRun.conversation_id == conversation_id)
        )
        tasks = list(
            await session.scalars(select(Task).where(Task.conversation_id == conversation_id))
        )
    return result, run, tasks


def _notify_staff_action():
    return [
        {
            "kind": "notify_staff",
            "key": "staff",
            "trigger": {"events": ["conversation_completed"]},
            "to": ["staff@example.com"],
            "subject_template": "Follow up",
            "body_template": "{{summary}}",
        }
    ]


def test_test_call_email_subject_prefix_is_added_once():
    assert render_action_subject("Summary", {"test_call": True}) == "[Test] Summary"
    assert render_action_subject("[Test] Summary", {"test_call": True}) == "[Test] Summary"
    assert render_action_subject("Summary", {"test_call": False}) == "Summary"


@pytest.mark.asyncio
async def test_execution_skips_missing_sheets_scope_and_unknown_customer_email(
    tenant_pair, tmp_path
):
    actions = [
        {
            "kind": "sheets_row",
            "key": "sheet",
            "trigger": {"events": ["conversation_completed"]},
            "spreadsheet_id": "test-sheet",
            "sheet_name": "Enquiries",
            "columns": [{"header": "Summary", "value_template": "{{summary}}"}],
        },
        {
            "kind": "email_customer",
            "key": "customer",
            "trigger": {"events": ["conversation_completed"]},
            "subject_template": "Thanks",
            "body_template": "We received your message.",
        },
    ]
    account, _, _, conversation_id = await _assistant_and_conversation(tenant_pair, actions)
    tenant_id = UUID(account["tenant_id"])
    settings = Settings(email_sender="fake", sheets_writer="fake", post_actions_fake_dir=tmp_path)
    async with tenant_session(tenant_id) as session:
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 2}
    async with tenant_session(tenant_id) as session:
        sheet_result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="sheet",
            fire_count=1,
            settings=settings,
        )
    async with tenant_session(tenant_id) as session:
        email_result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="customer",
            fire_count=1,
            settings=settings,
            email_sender=FakeEmailSender(tmp_path),
        )
        runs = {
            run.action_key: run
            for run in await session.scalars(
                select(ActionRun).where(ActionRun.conversation_id == conversation_id)
            )
        }
        tasks = list(
            await session.scalars(select(Task).where(Task.conversation_id == conversation_id))
        )
    assert sheet_result["status"] == "skipped"
    assert email_result["status"] == "skipped"
    assert runs["sheet"].status == "skipped"
    assert runs["customer"].status == "skipped"
    assert [task.kind for task in tasks] == ["action_setup_needed"]
    assert tasks[0].payload["title"] == "Connect Google Sheets"
    assert tasks[0].payload["message"] == "Connect Google Sheets to log conversations"
    assert not (tmp_path / "emails.jsonl").exists()


@pytest.mark.asyncio
async def test_email_action_uses_workspace_gmail_and_not_another_tenant(tenant_pair, monkeypatch):
    import inbound_api.integrations.google_auth as google_auth
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )

    async def fake_access_token(_connection):
        return "fake-access-token"

    monkeypatch.setattr(providers, "google_access_token", fake_access_token)
    account = tenant_pair["first"]
    _, _, assistant_id, conversation_id = await _assistant_and_conversation(
        tenant_pair, _notify_staff_action()
    )
    other_assistant_id = await _create_assistant(account["client"], "Workspace Gmail owner")
    await _add_gmail_connection(account, other_assistant_id, "workspace@example.test")

    foreign_account = tenant_pair["second"]
    foreign_assistant_id = await _create_assistant(foreign_account["client"], "Foreign Gmail owner")
    await _add_gmail_connection(foreign_account, foreign_assistant_id, "foreign@example.test")

    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-1"})

    result, run, _ = await _execute_gmail_action(
        account,
        conversation_id,
        transport=httpx.MockTransport(respond),
    )

    assert result["status"] == "succeeded"
    assert run is not None
    assert run.output == {"message_id": "sent-1", "from": "workspace@example.test"}
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_email_action_skips_when_only_foreign_tenant_has_gmail(tenant_pair, monkeypatch):
    import inbound_api.integrations.google_auth as google_auth
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )
    monkeypatch.setattr(providers, "google_access_token", lambda *_: None)
    account = tenant_pair["first"]
    _, _, _, conversation_id = await _assistant_and_conversation(
        tenant_pair, _notify_staff_action()
    )

    foreign_account = tenant_pair["second"]
    foreign_assistant_id = await _create_assistant(foreign_account["client"], "Foreign Gmail owner")
    await _add_gmail_connection(foreign_account, foreign_assistant_id, "foreign-only@example.test")

    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "unexpected"})

    result, run, tasks = await _execute_gmail_action(
        account,
        conversation_id,
        transport=httpx.MockTransport(respond),
    )

    assert result["status"] == "skipped"
    assert run is not None and run.status == "skipped"
    assert requests == []
    assert len(tasks) == 1
    assert tasks[0].kind == "action_setup_needed"
    assert tasks[0].payload == {
        "title": "Connect Gmail",
        "action_key": "staff",
        "message": "Connect a Gmail account to send follow-up emails",
    }


@pytest.mark.asyncio
async def test_email_action_prefers_assistants_own_gmail(tenant_pair, monkeypatch):
    import inbound_api.integrations.google_auth as google_auth
    import inbound_api.post_actions.providers as providers

    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )

    async def fake_access_token(_connection):
        return "fake-access-token"

    monkeypatch.setattr(providers, "google_access_token", fake_access_token)
    account = tenant_pair["first"]
    _, _, assistant_id, conversation_id = await _assistant_and_conversation(
        tenant_pair, _notify_staff_action()
    )
    other_assistant_id = await _create_assistant(account["client"], "Other Gmail owner")
    await _add_gmail_connection(account, other_assistant_id, "workspace@example.test")
    await _add_gmail_connection(account, assistant_id, "assistant@example.test")

    async def respond(_request):
        return httpx.Response(200, json={"id": "sent-own"})

    result, run, _ = await _execute_gmail_action(
        account,
        conversation_id,
        transport=httpx.MockTransport(respond),
    )

    assert result["status"] == "succeeded"
    assert run is not None
    assert run.output == {"message_id": "sent-own", "from": "assistant@example.test"}


@pytest.mark.asyncio
async def test_sheets_action_uses_workspace_connection(tenant_pair, tmp_path, monkeypatch):
    import inbound_api.integrations.google_auth as google_auth

    monkeypatch.setattr(
        google_auth,
        "get_settings",
        lambda: SimpleNamespace(gmail_mode="fake"),
    )
    actions = [
        {
            "kind": "sheets_row",
            "key": "sheet",
            "trigger": {"events": ["conversation_completed"]},
            "spreadsheet_id": "test-sheet",
            "sheet_name": "Conversations",
            "columns": [{"header": "Summary", "value_template": "{{summary}}"}],
        }
    ]
    account, _, _, conversation_id = await _assistant_and_conversation(tenant_pair, actions)
    other_assistant_id = await _create_assistant(account["client"], "Workspace Sheets owner")
    tenant_id = UUID(account["tenant_id"])
    async with tenant_session(tenant_id) as session:
        session.add(
            ChannelConnection(
                tenant_id=tenant_id,
                assistant_id=other_assistant_id,
                kind="gmail",
                external_id="sheets-owner@example.test",
                status="active",
                settings={"google_features": ["sheets"]},
            )
        )
        await plan_conversation_actions(session, conversation_id)

    settings = Settings(sheets_writer="fake", post_actions_fake_dir=tmp_path)
    async with tenant_session(tenant_id) as session:
        result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="sheet",
            fire_count=1,
            settings=settings,
        )
        run = await session.scalar(
            select(ActionRun).where(ActionRun.conversation_id == conversation_id)
        )

    assert result["status"] == "succeeded"
    assert run is not None
    assert run.output["account_email"] == "sheets-owner@example.test"


@pytest.mark.asyncio
async def test_test_call_post_actions_are_planned_and_marked(tenant_pair, tmp_path, monkeypatch):
    actions = [
        {
            "kind": "notify_staff",
            "key": "staff",
            "trigger": {"events": ["conversation_completed"]},
            "to": ["staff@example.com"],
            "subject_template": "Summary {{test_call}}",
            "body_template": "Browser test: {{test_call}}",
        },
        {
            "kind": "webhook",
            "key": "hook",
            "trigger": {"events": ["conversation_completed"]},
            "url": "https://example.test/hook",
        },
    ]
    account, _, assistant_id, test_conversation_id = await _assistant_and_conversation(
        tenant_pair, actions
    )
    tenant_id = UUID(account["tenant_id"])
    normal_conversation_id = uuid4()
    async with tenant_session(tenant_id) as session:
        test_conversation = await session.get(Conversation, test_conversation_id)
        assert test_conversation is not None
        test_conversation.metrics = {"test_call": True}
        session.add(
            Conversation(
                id=normal_conversation_id,
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.WHATSAPP,
                external_ref=str(normal_conversation_id),
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                summary="Customer asked for assistance.",
            )
        )

    monkeypatch.setattr(
        get_settings(), "credentials_encryption_key", Fernet.generate_key().decode()
    )
    secret = await account["client"].post(
        f"/assistants/{assistant_id}/post-actions/hook/webhook-secret"
    )
    assert secret.status_code == 200, secret.text

    settings = Settings(email_sender="fake", webhook_sender="fake", post_actions_fake_dir=tmp_path)
    conversation_ids = (test_conversation_id, normal_conversation_id)
    async with tenant_session(tenant_id) as session:
        assert await plan_conversation_actions(session, test_conversation_id) == {"queued": 2}
        assert await plan_conversation_actions(session, normal_conversation_id) == {"queued": 2}

    for conversation_id in conversation_ids:
        for action_key in ("staff", "hook"):
            async with tenant_session(tenant_id) as session:
                result = await execute_conversation_action(
                    session,
                    conversation_id=conversation_id,
                    action_key=action_key,
                    fire_count=1,
                    settings=settings,
                    email_sender=FakeEmailSender(tmp_path),
                    webhook_sender=FakeWebhookSender(tmp_path),
                )
            assert result["status"] == "succeeded"

    email_records = [
        json.loads(line) for line in (tmp_path / "emails.jsonl").read_text().splitlines()
    ]
    assert [record["subject"] for record in email_records] == [
        "[Test] Summary true",
        "Summary false",
    ]
    assert [record["body"] for record in email_records] == [
        "Browser test: true",
        "Browser test: false",
    ]

    webhook_records = [
        json.loads(line) for line in (tmp_path / "webhooks.jsonl").read_text().splitlines()
    ]
    assert [record["body"]["test"] for record in webhook_records] == [True, False]
    assert [record["body"]["context"]["test_call"] for record in webhook_records] == [
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_execution_retries_transient_failure_then_fails_at_max_attempts(tenant_pair):
    actions = [
        {
            "kind": "notify_staff",
            "key": "staff",
            "trigger": {"events": ["conversation_completed"]},
            "to": ["staff@example.com"],
            "subject_template": "Follow up",
            "body_template": "{{summary}}",
        }
    ]
    account, _, _, conversation_id = await _assistant_and_conversation(tenant_pair, actions)
    tenant_id = UUID(account["tenant_id"])
    async with tenant_session(tenant_id) as session:
        await plan_conversation_actions(session, conversation_id)
        job = await session.scalar(
            select(Job).where(
                Job.kind == "post_action_execute",
                Job.payload["conversation_id"].astext == str(conversation_id),
            )
        )
        assert job is not None
        job.attempts = 1
        job_id = job.id

    class ConnectFailure:
        async def send(self, **_kwargs):
            raise httpx.ConnectError("connection refused")

    settings = Settings(job_max_attempts=3)
    token = current_job.set((job_id, uuid4()))
    try:
        with pytest.raises(httpx.ConnectError, match="connection refused"):
            async with tenant_session(tenant_id) as session:
                await execute_conversation_action(
                    session,
                    conversation_id=conversation_id,
                    action_key="staff",
                    fire_count=1,
                    settings=settings,
                    email_sender=ConnectFailure(),
                )
    finally:
        current_job.reset(token)
    async with tenant_session(tenant_id) as session:
        run = await session.scalar(
            select(ActionRun).where(ActionRun.conversation_id == conversation_id)
        )
        assert run is not None
        assert run.status == "queued"
        assert run.attempts == 1
        assert "ConnectError: connection refused" in run.last_error
        job = await session.get(Job, job_id)
        assert job is not None
        job.attempts = 3

    token = current_job.set((job_id, uuid4()))
    try:
        async with tenant_session(tenant_id) as session:
            result = await execute_conversation_action(
                session,
                conversation_id=conversation_id,
                action_key="staff",
                fire_count=1,
                settings=settings,
                email_sender=ConnectFailure(),
            )
    finally:
        current_job.reset(token)
    async with tenant_session(tenant_id) as session:
        run = await session.scalar(
            select(ActionRun).where(ActionRun.conversation_id == conversation_id)
        )
        tasks = list(
            await session.scalars(select(Task).where(Task.conversation_id == conversation_id))
        )
    assert result["status"] == "failed"
    assert run is not None and run.status == "failed" and run.attempts == 3
    assert [task.kind for task in tasks] == ["action_failed"]
    assert tasks[0].payload["title"] == "After-conversation action failed"


@pytest.mark.asyncio
async def test_ssrf_block_is_failed_action_through_execution(tenant_pair, monkeypatch):
    actions = [
        {
            "kind": "webhook",
            "key": "private-hook",
            "trigger": {"events": ["conversation_completed"]},
            "url": "http://127.0.0.1:9999/hook",
        }
    ]
    account, _, assistant_id, conversation_id = await _assistant_and_conversation(
        tenant_pair, actions
    )
    tenant_id = UUID(account["tenant_id"])
    monkeypatch.setattr(
        get_settings(), "credentials_encryption_key", Fernet.generate_key().decode()
    )
    secret = await account["client"].post(
        f"/assistants/{assistant_id}/post-actions/private-hook/webhook-secret"
    )
    assert secret.status_code == 200, secret.text
    async with tenant_session(tenant_id) as session:
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 1}
    async with tenant_session(tenant_id) as session:
        result = await execute_conversation_action(
            session,
            conversation_id=conversation_id,
            action_key="private-hook",
            fire_count=1,
            settings=Settings(
                app_env="test",
                webhook_sender="http",
                post_action_webhook_allow_private_urls=False,
            ),
        )
    async with tenant_session(tenant_id) as session:
        run = await session.scalar(
            select(ActionRun).where(ActionRun.conversation_id == conversation_id)
        )
        task = await session.scalar(select(Task).where(Task.conversation_id == conversation_id))
        stored_secret = await session.scalar(
            select(PostActionSecret).where(PostActionSecret.assistant_id == assistant_id)
        )
    assert secret.json()["secret"]
    assert stored_secret is not None
    assert result["status"] == "failed"
    assert run is not None and run.status == "failed"
    assert task is not None and task.kind == "action_failed"
