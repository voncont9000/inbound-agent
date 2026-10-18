from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import PermanentJobError, enqueue, register
from inbound_api.models import CallRecording
from inbound_api.settings import get_settings


class RecordingStore(Protocol):
    def save(self, key: str, content: bytes | BinaryIO) -> Path: ...

    def open(self, key: str) -> Path: ...

    def delete(self, key: str) -> None: ...


class LocalRecordingStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save(self, key: str, content: bytes | BinaryIO) -> Path:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                if isinstance(content, bytes):
                    output.write(content)
                else:
                    shutil.copyfileobj(content, output)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target

    def open(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Recording path is outside recording storage")
        return path


@register("recording_delete")
async def recording_delete(session: AsyncSession, payload: dict) -> dict[str, bool]:
    try:
        recording_id = UUID(payload["recording_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise PermanentJobError("Invalid recording deletion job") from error
    recording = await session.scalar(
        select(CallRecording).where(CallRecording.id == recording_id).with_for_update()
    )
    if recording is None:
        return {"deleted": False}
    now = datetime.now(UTC)
    if recording.expires_at > now:
        await enqueue(
            session,
            "recording_delete",
            {"recording_id": str(recording.id)},
            run_after=recording.expires_at,
        )
        return {"deleted": False}
    store = LocalRecordingStore(get_settings().upload_dir / "recordings")
    store.delete(recording.storage_key)
    await session.delete(recording)
    return {"deleted": True}
