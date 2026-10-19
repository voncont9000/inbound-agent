from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import select

from inbound_api.db import tenant_session
from inbound_api.models import (
    AuditLog,
    CallRecording,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Person,
)
from inbound_api.services.job_handlers import graph_ingest
from inbound_api.settings import get_settings


async def _create_assistant(client) -> tuple[str, str]:
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    config["recording"]["enabled"] = True
    response = await client.post(
        "/assistants",
        json={"name": "Delete test assistant", "template": "uk_law_firm", "config": config},
    )
    assert response.status_code == 201, response.text
    assistant_id = response.json()["id"]
    number = "020 7946 0000"
    channel = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": number},
    )
    assert channel.status_code == 201, channel.text
    return assistant_id, number


async def _start_conversation(client, assistant_id: str, number: str) -> tuple[UUID, str]:
    external_call_id = f"delete-{uuid4()}"
    started = await client.post(
        "/internal/voice/calls",
        headers={"X-Service-Token": get_settings().voice_service_token},
        json={
            "to_number": number,
            "from_number": "07700 900456",
            "external_call_id": external_call_id,
            "channel": "phone",
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    assert started.status_code == 201, started.text
    return UUID(started.json()["conversation_id"]), external_call_id


async def _complete_conversation(
    tenant_id: str, conversation_id: UUID, *, keep_person: bool = False
) -> UUID | None:
    async with tenant_session(UUID(tenant_id)) as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.status = ConversationStatus.COMPLETED
        conversation.ended_at = datetime.now(UTC)
        person_id = None
        if keep_person:
            person = Person(tenant_id=UUID(tenant_id), display_name="Kept contact")
            session.add(person)
            await session.flush()
            conversation.person_id = person.id
            person_id = person.id
        session.add(
            Message(
                tenant_id=UUID(tenant_id),
                conversation_id=conversation_id,
                role=MessageRole.CALLER,
                text="A message that should be deleted.",
                ts=conversation.started_at,
            )
        )
        return person_id


async def test_delete_conversation_cascades_data_and_removes_recording(
    tenant_pair, tmp_path, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    assistant_id, number = await _create_assistant(client)
    conversation_id, external_call_id = await _start_conversation(client, assistant_id, number)
    upload = await client.post(
        f"/internal/voice/calls/{conversation_id}/recording?duration_seconds=1",
        content=b"RIFFtest-recording",
        headers={
            "X-Service-Token": get_settings().voice_service_token,
            "X-External-Call-ID": external_call_id,
            "Content-Type": "audio/wav",
        },
    )
    assert upload.status_code == 202, upload.text
    recording_path = tmp_path / "recordings" / account["tenant_id"] / f"{conversation_id}.wav"
    assert recording_path.is_file()
    person_id = await _complete_conversation(
        account["tenant_id"], conversation_id, keep_person=True
    )

    deleted = await client.delete(f"/conversations/{conversation_id}")

    assert deleted.status_code == 204
    assert not recording_path.exists()
    async with tenant_session(UUID(account["tenant_id"])) as session:
        assert await session.get(Conversation, conversation_id) is None
        assert (
            await session.scalar(
                select(Message.id).where(Message.conversation_id == conversation_id)
            )
            is None
        )
        assert (
            await session.scalar(
                select(CallRecording.id).where(CallRecording.conversation_id == conversation_id)
            )
            is None
        )
        assert await session.get(Person, person_id) is not None
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "conversation.deleted",
                AuditLog.target_id == conversation_id,
            )
        )
        assert audit is not None
    async with tenant_session(UUID(account["tenant_id"])) as session:
        await graph_ingest(session, {"conversation_id": str(conversation_id)})


async def test_delete_conversation_is_owner_only_and_tenant_scoped(tenant_pair, owner_database_url):
    first, second = tenant_pair["first"], tenant_pair["second"]
    assistant_id, number = await _create_assistant(first["client"])
    conversation_id, _ = await _start_conversation(first["client"], assistant_id, number)
    await _complete_conversation(first["tenant_id"], conversation_id)

    hidden = await second["client"].delete(f"/conversations/{conversation_id}")
    assert hidden.status_code == 404

    with psycopg.connect(owner_database_url) as connection:
        connection.execute(
            "UPDATE memberships SET role = 'staff' WHERE tenant_id = %s AND user_id = %s",
            (first["tenant_id"], first["user_id"]),
        )
    forbidden = await first["client"].delete(f"/conversations/{conversation_id}")
    assert forbidden.status_code == 403


async def test_delete_conversation_rejects_in_progress_conversation(tenant_pair) -> None:
    account = tenant_pair["first"]
    assistant_id, number = await _create_assistant(account["client"])
    conversation_id, _ = await _start_conversation(account["client"], assistant_id, number)

    response = await account["client"].delete(f"/conversations/{conversation_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "Conversation is still in progress"


async def test_bulk_delete_counts_skipped_conversations_and_assistant_list(tenant_pair) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    assistant_id, number = await _create_assistant(client)
    completed_id, _ = await _start_conversation(client, assistant_id, number)
    active_id, _ = await _start_conversation(client, assistant_id, number)
    await _complete_conversation(account["tenant_id"], completed_id)

    assistant_list = await client.get("/assistants")
    item = next(row for row in assistant_list.json()["items"] if row["id"] == assistant_id)
    assert item["conversation_count"] == 2

    response = await client.post(
        "/conversations/bulk-delete",
        json={"ids": [str(completed_id), str(active_id)]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1, "skipped_in_progress": 1}
    remaining = await client.get(f"/conversations/{active_id}")
    assert remaining.status_code == 200
    assert (await client.get(f"/conversations/{completed_id}")).status_code == 404


async def test_assistant_delete_requires_flag_and_keeps_contact(tenant_pair) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    assistant_id, number = await _create_assistant(client)
    conversation_id, _ = await _start_conversation(client, assistant_id, number)
    person_id = await _complete_conversation(
        account["tenant_id"], conversation_id, keep_person=True
    )

    blocked = await client.delete(f"/assistants/{assistant_id}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Assistant has conversations"

    deleted = await client.delete(
        f"/assistants/{assistant_id}", params={"delete_conversations": "true"}
    )

    assert deleted.status_code == 204
    async with tenant_session(UUID(account["tenant_id"])) as session:
        assert await session.get(Conversation, conversation_id) is None
        assert await session.get(Person, person_id) is not None
