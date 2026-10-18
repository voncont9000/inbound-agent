from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from inbound_api.db import tenant_session
from inbound_api.jobs import current_job
from inbound_api.models import (
    ActionRun,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Intake,
    IntakeVersion,
    Job,
    PostActionSecret,
    Task,
)
from inbound_api.post_actions.providers import FakeEmailSender
from inbound_api.post_actions.service import (
    execute_conversation_action,
    plan_conversation_actions,
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
    assert not (tmp_path / "emails.jsonl").exists()


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
