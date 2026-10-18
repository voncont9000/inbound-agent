from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from inbound_api.db import tenant_session
from inbound_api.jobs import HANDLERS
from inbound_api.models import CallRecording, Job
from inbound_api.routers.internal_voice import upload_call_recording
from inbound_api.services.recordings import recording_delete
from inbound_api.settings import get_settings
from inbound_api.web_call_tokens import issue


async def _create_recording_conversation(
    client,
    *,
    enabled: bool = True,
    number: str = "020 7946 0000",
    tenant_id: UUID | None = None,
    test_call: bool = False,
):
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    config["recording"]["enabled"] = enabled
    assistant_response = await client.post(
        "/assistants",
        json={"name": "Recording assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant_response.json()["id"]
    channel_response = await client.post(
        f"/assistants/{assistant_id}/channels",
        json={"kind": "phone", "external_id": number},
    )
    assert channel_response.status_code == 201, channel_response.text
    service_headers = {"X-Service-Token": get_settings().voice_service_token}
    call_channel = "web" if test_call else "phone"
    web_call_token = None
    if test_call:
        assert tenant_id is not None
        web_call_token, _ = issue(tenant_id, UUID(assistant_id))
    started = await client.post(
        "/internal/voice/calls",
        headers=service_headers,
        json={
            "to_number": number,
            "from_number": "07700 900456",
            "external_call_id": "recording-call",
            "channel": call_channel,
            "web_call_token": web_call_token,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    assert started.status_code == 201, started.text
    return UUID(started.json()["conversation_id"]), service_headers


@pytest.mark.asyncio
async def test_recording_upload_and_staff_retrieval(
    tenant_pair, api_client, monkeypatch, tmp_path
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    conversation_id, service_headers = await _create_recording_conversation(client)
    audio = b"RIFFfake-wav-data"
    response = await client.post(
        f"/internal/voice/calls/{conversation_id}/recording?duration_seconds=12.5",
        content=audio,
        headers={
            **service_headers,
            "Content-Type": "audio/wav",
            "X-External-Call-ID": "recording-call",
        },
    )
    assert response.status_code == 202, response.text
    path = tmp_path / "recordings" / account["tenant_id"] / f"{conversation_id}.wav"
    assert path.read_bytes() == audio
    detail = await client.get(f"/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.json()["recording"]["duration_seconds"] == 12.5
    assert detail.json()["recording"]["size_bytes"] == len(audio)
    playback = await client.get(f"/conversations/{conversation_id}/recording")
    assert playback.status_code == 200
    assert playback.content == audio
    assert playback.headers["content-type"] == "audio/wav"
    assert playback.headers["cache-control"] == "private, no-store"
    partial = await client.get(
        f"/conversations/{conversation_id}/recording",
        headers={"Range": "bytes=0-3"},
    )
    assert partial.status_code == 206
    assert partial.content == audio[:4]
    assert partial.headers["content-range"] == f"bytes 0-3/{len(audio)}"

    replacement = b"RIFFreplacement-wav-data"
    replaced = await client.post(
        f"/internal/voice/calls/{conversation_id}/recording?duration_seconds=20",
        content=replacement,
        headers={
            **service_headers,
            "Content-Type": "audio/wav",
            "X-External-Call-ID": "recording-call",
        },
    )
    assert replaced.status_code == 202, replaced.text
    assert path.read_bytes() == replacement
    assert (await client.get(f"/conversations/{conversation_id}")).json()["recording"][
        "duration_seconds"
    ] == 20

    async with tenant_session(UUID(account["tenant_id"])) as session:
        recordings = list(await session.scalars(select(CallRecording)))
        jobs = list(await session.scalars(select(Job).where(Job.kind == "recording_delete")))
    assert len(recordings) == 1
    assert len(jobs) == 2
    assert any(job.run_after == recordings[0].expires_at for job in jobs)

    denied = await tenant_pair["second"]["client"].get(
        f"/conversations/{conversation_id}/recording"
    )
    assert denied.status_code == 404
    unauthenticated = await api_client.get(f"/conversations/{conversation_id}/recording")
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_recording_upload_conflicts_and_size_limit(
    tenant_pair, monkeypatch, tmp_path
) -> None:
    client = tenant_pair["first"]["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "recording_max_bytes", 4)
    conversation_id, service_headers = await _create_recording_conversation(client)
    base_url = f"/internal/voice/calls/{conversation_id}/recording"
    headers = {
        **service_headers,
        "Content-Type": "audio/wav",
        "X-External-Call-ID": "recording-call",
    }
    too_large = await client.post(base_url, content=b"12345", headers=headers)
    assert too_large.status_code == 413
    mismatch = await client.post(
        base_url,
        content=b"1234",
        headers={**headers, "X-External-Call-ID": "different-call"},
    )
    assert mismatch.status_code == 409
    unauthorized = await client.post(
        base_url,
        content=b"1234",
        headers={**headers, "X-Service-Token": "wrong"},
    )
    assert unauthorized.status_code == 401
    disabled_id, disabled_headers = await _create_recording_conversation(
        tenant_pair["second"]["client"],
        enabled=False,
        number="020 7946 0001",
    )
    disabled = await tenant_pair["second"]["client"].post(
        f"/internal/voice/calls/{disabled_id}/recording",
        content=b"1234",
        headers={
            **disabled_headers,
            "Content-Type": "audio/wav",
            "X-External-Call-ID": "recording-call",
        },
    )
    assert disabled.status_code == 409


@pytest.mark.asyncio
async def test_recording_upload_requires_content_length() -> None:
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "root_path": "",
        }
    )
    with pytest.raises(HTTPException) as error:
        await upload_call_recording(
            UUID(int=1),
            request,
            x_external_call_id="recording-call",
            content_length=None,
            duration_seconds=None,
        )
    assert error.value.status_code == 411


@pytest.mark.asyncio
async def test_recording_delete_handler_requeues_then_deletes(
    tenant_pair, monkeypatch, tmp_path
) -> None:
    client = tenant_pair["first"]["client"]
    account = tenant_pair["first"]
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    conversation_id, service_headers = await _create_recording_conversation(client)
    path = tmp_path / "recordings" / account["tenant_id"] / f"{conversation_id}.wav"
    response = await client.post(
        f"/internal/voice/calls/{conversation_id}/recording",
        content=b"RIFFfake",
        headers={
            **service_headers,
            "Content-Type": "audio/wav",
            "X-External-Call-ID": "recording-call",
        },
    )
    assert response.status_code == 202
    async with tenant_session(UUID(account["tenant_id"])) as session:
        recording = await session.scalar(
            select(CallRecording).where(CallRecording.conversation_id == conversation_id)
        )
        assert recording is not None
        recording_id = recording.id
        first_result = await recording_delete(session, {"recording_id": str(recording_id)})
        jobs = list(await session.scalars(select(Job).where(Job.kind == "recording_delete")))
        assert first_result == {"deleted": False}
        assert len(jobs) == 2
        recording = await session.get(CallRecording, recording_id)
        assert recording is not None
        recording.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    async with tenant_session(UUID(account["tenant_id"])) as session:
        result = await recording_delete(session, {"recording_id": str(recording_id)})
    assert result == {"deleted": True}
    assert not path.exists()
    async with tenant_session(UUID(account["tenant_id"])) as session:
        assert await session.get(CallRecording, recording_id) is None
    assert "recording_delete" in HANDLERS


@pytest.mark.asyncio
async def test_recording_upload_marks_web_practice_calls_as_test(
    tenant_pair, monkeypatch, tmp_path
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    conversation_id, service_headers = await _create_recording_conversation(
        client,
        test_call=True,
        tenant_id=UUID(account["tenant_id"]),
    )
    response = await client.post(
        f"/internal/voice/calls/{conversation_id}/recording",
        content=b"RIFFpractice-wav",
        headers={
            **service_headers,
            "Content-Type": "audio/wav",
            "X-External-Call-ID": "recording-call",
        },
    )
    assert response.status_code == 202, response.text
    async with tenant_session(UUID(account["tenant_id"])) as session:
        recording = await session.scalar(
            select(CallRecording).where(CallRecording.conversation_id == conversation_id)
        )
    assert recording is not None
    assert recording.is_test is True


def test_local_recording_store_rejects_paths_outside_root(tmp_path) -> None:
    from inbound_api.services.recordings import LocalRecordingStore

    store = LocalRecordingStore(tmp_path / "recordings")
    with pytest.raises(ValueError, match="outside recording storage"):
        store.open("../outside.wav")
