"""커서 기반 페이지네이션 (CLAUDE.md §3).

커서는 (created_at, id) 키셋이다. offset 을 쓰지 않으므로 삽입 중에도 중복/누락이 없다.
"""

import base64
import binascii
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Query

from app.core.errors import InvalidInput

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cursor = Annotated[str | None, Query()]


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, _, row_id = base64.urlsafe_b64decode(padded).decode().partition("|")
        return datetime.fromisoformat(created_at), UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInput("cursor 형식이 올바르지 않습니다", field="cursor") from exc


def next_cursor[T](rows: list[T], limit: int) -> tuple[list[T], dict[str, Any]]:
    """limit+1 건 조회 결과를 (페이지, meta) 로 자른다."""
    if len(rows) <= limit:
        return rows, {"next_cursor": None}
    page = rows[:limit]
    last = page[-1]
    return page, {"next_cursor": encode_cursor(last.created_at, last.id)}  # type: ignore[attr-defined]
