import base64
import binascii
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        created_at = datetime.fromisoformat(value[0])
        row_id = UUID(value[1])
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor") from None
    return created_at, row_id


async def page_query(
    session: AsyncSession,
    statement: Select,
    *,
    created_at_column: Any,
    id_column: Any,
    cursor: str | None,
    limit: int,
) -> tuple[list[Any], str | None]:
    if cursor:
        created_at, row_id = decode_cursor(cursor)
        statement = statement.where(tuple_(created_at_column, id_column) < (created_at, row_id))
    statement = statement.order_by(created_at_column.desc(), id_column.desc()).limit(limit + 1)
    result = await session.scalars(statement)
    rows = list(result)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None
    return rows, next_cursor
