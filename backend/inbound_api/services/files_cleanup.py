import asyncio
import shutil
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import PermanentJobError, register
from inbound_api.settings import get_settings


@register("knowledge_files_cleanup")
async def knowledge_files_cleanup(
    session: AsyncSession, payload: dict[str, Any]
) -> dict[str, bool]:
    try:
        assistant_id = UUID(payload["assistant_id"])
        tenant_id = UUID(
            str(await session.scalar(text("SELECT current_setting('app.tenant_id', true)")))
        )
    except (ValueError, TypeError, KeyError) as error:
        raise PermanentJobError("Invalid cleanup job context") from error
    root = get_settings().upload_dir.resolve()
    target = (root / str(tenant_id) / str(assistant_id)).resolve()
    if root not in target.parents:
        raise PermanentJobError("Cleanup path is outside UPLOAD_DIR")
    try:
        await asyncio.to_thread(shutil.rmtree, target)
    except FileNotFoundError:
        pass
    return {"removed": True}
