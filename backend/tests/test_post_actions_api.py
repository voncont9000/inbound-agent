from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from inbound_api.channels.credentials import decrypt_credential
from inbound_api.db import tenant_session
from inbound_api.models import (
    ActionRun,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Intake,
    IntakeVersion,
    PostActionSecret,
)
from inbound_api.post_actions.service import plan_conversation_actions
from inbound_api.settings import get_settings


@pytest.mark.asyncio
async def test_post_action_dry_run_and_webhook_secret_lifecycle(tenant_pair, monkeypatch):
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    config["post_actions"] = [
        {
            "kind": "notify_staff",
            "key": "staff-alert",
            "trigger": {"events": ["conversation_completed"]},
            "to": ["staff@example.com"],
            "subject_template": "New {{service}} enquiry",
            "body_template": "{{summary}}",
        },
        {
            "kind": "webhook",
            "key": "staff-hook",
            "trigger": {"events": ["urgent"]},
            "url": "https://example.test/hook",
        },
    ]
    created = await client.post(
        "/assistants",
        json={"name": "Post-actions assistant", "template": "uk_law_firm", "config": config},
    )
    assert created.status_code == 201, created.text
    assistant_id = UUID(created.json()["id"])
    conversation_id = uuid4()
    async with tenant_session(UUID(account["tenant_id"])) as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=UUID(account["tenant_id"]),
                assistant_id=assistant_id,
                channel=ConversationChannel.PHONE,
                external_ref=str(conversation_id),
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                summary="Caller needs a callback.",
                metrics={"test_call": True},
            )
        )
        session.add(
            Intake(
                tenant_id=UUID(account["tenant_id"]),
                conversation_id=conversation_id,
                version=IntakeVersion.MERGED,
                data={"service": "conveyancing", "fields": {}},
            )
        )

    async with tenant_session(UUID(account["tenant_id"])) as session:
        assert await plan_conversation_actions(session, conversation_id) == {"queued": 0}

    dry_run = await client.post(
        f"/assistants/{assistant_id}/post-actions/test",
        json={
            "conversation_id": str(conversation_id),
            "action": config["post_actions"][0],
        },
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["rendered"]["to"] == ["staff@example.com"]
    assert dry_run.json()["rendered"]["body"] == "Caller needs a callback."
    async with tenant_session(UUID(account["tenant_id"])) as session:
        assert (
            await session.scalar(
                select(ActionRun.id).where(ActionRun.conversation_id == conversation_id)
            )
            is None
        )

    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "credentials_encryption_key", encryption_key)
    generated = await client.post(
        f"/assistants/{assistant_id}/post-actions/staff-hook/webhook-secret"
    )
    assert generated.status_code == 200, generated.text
    secret_value = generated.json()["secret"]
    rotated = await client.post(
        f"/assistants/{assistant_id}/post-actions/staff-hook/webhook-secret"
    )
    assert rotated.status_code == 200, rotated.text
    rotated_secret = rotated.json()["secret"]
    assert rotated_secret != secret_value
    listed = await client.get(f"/assistants/{assistant_id}/post-actions/webhook-secrets")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [
        {"action_key": "staff-hook", "created_at": generated.json()["created_at"]}
    ]
    assert secret_value not in listed.text

    async with tenant_session(UUID(account["tenant_id"])) as session:
        secret = await session.scalar(
            select(PostActionSecret).where(
                PostActionSecret.assistant_id == assistant_id,
                PostActionSecret.action_key == "staff-hook",
            )
        )
        assert secret is not None
        assert secret.secret_ciphertext != secret_value
        assert decrypt_credential(secret.secret_ciphertext) == rotated_secret

    foreign = tenant_pair["second"]["client"]
    assert (
        await foreign.get(f"/assistants/{assistant_id}/post-actions/webhook-secrets")
    ).status_code == 404
